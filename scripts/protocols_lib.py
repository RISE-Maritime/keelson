"""Load and validate protocol specifications (protocols/*.yaml).

Shared by scripts/generate_docs_for_protocols.py and
sdks/python/tests/test_protocols.py so that the rules a protocol file must
satisfy are stated once. See docs/protocol-specification.md §8.

A protocol file has these blocks:

  name, title, status, depends_on, purpose      identity and prose
  roles      {role: {description, reads?}}
  keys       [{subject, payload, key, shape, storage, writer_field?, rate?}]
  key_notes  markdown, optional: sentinel values, why a key has its shape
  actions    {action: {by, publish|rpc|get|clock, sets?, note?}}
  lifecycle  [{subject, payload, of?, states: [{name, field, is|present} | {name, derived}],
              transitions: [{from?, to, action}]}]
             `of` names the field, for a subject with more than one lifecycle
  flows      {flow: {description, steps: [{action, role?, guard?, note?}]}}
  invariants [markdown]
  not_solved markdown

Every structured field is either rendered into the generated page or checked
by validate(); nothing here is executed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import yaml

REQUIRED_TOP = (
    "name",
    "title",
    "status",
    "purpose",
    "roles",
    "keys",
    "actions",
    "lifecycle",
    "flows",
    "invariants",
    "not_solved",
)
STATUSES = ("as-built", "proposed")
SHAPES = ("instance", "slot")
STORAGES = ("none", "latest", "history")
ACTION_KINDS = ("publish", "rpc", "get", "clock")
TRANSITION_KEYS = {"from", "to", "action", "note"}
STEP_KEYS = {"action", "role", "guard", "note"}
KEY_VAR = re.compile(r"\{([a-z_][a-z0-9_]*)\}")
KEY_CHUNK = re.compile(r"^(\{[a-z_][a-z0-9_]*\}|[a-z0-9_\-.]+)$")


@dataclass
class Problem:
    protocol: str
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.protocol}: {self.where}: {self.message}"


@dataclass
class Protocol:
    path: Path
    data: Dict[str, Any]

    @property
    def name(self) -> str:
        return self.data.get("name", self.path.stem)

    @property
    def subjects(self) -> List[str]:
        return [k["subject"] for k in self.data.get("keys", []) if "subject" in k]

    def key_row(self, subject: str) -> Optional[Dict[str, Any]]:
        for k in self.data.get("keys", []):
            if k.get("subject") == subject:
                return k
        return None


def load_all(directory: Path) -> List[Protocol]:
    protocols = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
        protocols.append(Protocol(path=path, data=data))
    return protocols


def key_variables(template: str) -> List[str]:
    return KEY_VAR.findall(template)


def action_kind(action: Dict[str, Any]) -> Optional[str]:
    kinds = [k for k in ACTION_KINDS if k in action]
    return kinds[0] if len(kinds) == 1 else None


def rpc_target(action: Dict[str, Any]) -> tuple[str, Optional[str]]:
    """('interface', 'vehicle_navigation/v1') or ('class', 'actuation')."""
    target = action.get("rpc")
    if isinstance(target, dict) and "class" in target:
        return "class", str(target["class"])
    if isinstance(target, str):
        return "interface", target
    return "invalid", None


def roles_of(action: Dict[str, Any]) -> List[str]:
    by = action.get("by", [])
    return [by] if isinstance(by, str) else list(by)


# ---------------------------------------------------------------------------
# Validation. `resolver` supplies what only the SDK knows: the registry and
# the protobuf descriptors. Its interface is three callables so the script can
# run without a generated SDK when only structure is being checked.
# ---------------------------------------------------------------------------


@dataclass
class Resolver:
    subject_schema: Any = None  # subject -> type name, or raise KeyError
    message_class: Any = None  # type name -> protobuf message class
    interface_known: Any = None  # "iface/vN" -> bool

    @property
    def enabled(self) -> bool:
        return all((self.subject_schema, self.message_class, self.interface_known))


def _field(resolver: Resolver, payload: str, name: str):
    cls = resolver.message_class(payload)
    return cls.DESCRIPTOR.fields_by_name.get(name)


def _is_valid_for(fd, value) -> Optional[str]:
    """None if `value` is a legal `is:` for field `fd`, else a message."""
    from google.protobuf.descriptor import FieldDescriptor as F

    if fd.type == F.TYPE_ENUM:
        if not isinstance(value, str):
            return f"enum field needs a value name, got {value!r}"
        ev = fd.enum_type.values_by_name.get(value)
        if ev is None:
            return f"{value!r} is not a value of {fd.enum_type.full_name}"
        if ev.number == 0:
            return f"{value!r} is value 0, the proto3 default; a state must not be the default"
        return None
    if fd.type == F.TYPE_BOOL:
        return (
            None
            if isinstance(value, bool)
            else f"bool field needs true/false, got {value!r}"
        )
    if fd.type == F.TYPE_STRING:
        return (
            None
            if isinstance(value, str)
            else f"string field needs a string, got {value!r}"
        )
    return None  # numeric: accept


def _state_holds(state: Dict[str, Any], sets: Dict[str, Any]) -> bool:
    """Does an action that `sets` these fields land in `state`?"""
    f = state["field"]
    if "is" in state:
        return f in sets and sets[f] == state["is"]
    if "present" in state:
        return (f in sets) == bool(state["present"])
    return False


def validate(protocols: List[Protocol], resolver: Resolver) -> Iterator[Problem]:
    names = {p.name for p in protocols}
    owners: Dict[str, List[str]] = {}

    for p in protocols:
        d = p.data
        P = lambda where, msg: Problem(p.name, where, msg)  # noqa: E731

        for key in REQUIRED_TOP:
            if key not in d:
                yield P("top", f"missing section {key!r}")
        if d.get("status") not in STATUSES:
            yield P("status", f"must be one of {STATUSES}")
        if d.get("name") != p.path.stem:
            yield P("name", f"must equal the file name {p.path.stem!r}")
        for dep in d.get("depends_on") or []:
            if dep not in names:
                yield P("depends_on", f"unknown protocol {dep!r}")

        roles = d.get("roles") or {}
        for role, spec in roles.items():
            if not isinstance(spec, dict) or "description" not in spec:
                yield P(f"roles.{role}", "needs a description")
            for s in (spec or {}).get("reads", []) or []:
                if resolver.enabled:
                    try:
                        resolver.subject_schema(s)
                    except KeyError:
                        yield P(f"roles.{role}.reads", f"unknown subject {s!r}")

        # --- keys ---------------------------------------------------------
        payload_of: Dict[str, str] = {}
        for i, row in enumerate(d.get("keys") or []):
            where = f"keys[{i}]"
            subject = row.get("subject")
            if not subject:
                yield P(where, "needs a subject")
                continue
            owners.setdefault(subject, []).append(p.name)
            payload = row.get("payload")
            if not payload:
                yield P(where, "needs a payload type")
            if resolver.enabled:
                try:
                    registered = resolver.subject_schema(subject)
                except KeyError:
                    yield P(where, f"subject {subject!r} is not in subjects.yaml")
                    registered = None
                if registered and payload and registered != payload:
                    yield P(
                        where,
                        f"payload {payload!r} but subjects.yaml says {registered!r}",
                    )
            if payload:
                payload_of[subject] = payload
            shape = row.get("shape")
            if shape not in SHAPES:
                yield P(where, f"shape must be one of {SHAPES}")
            if row.get("storage") not in STORAGES:
                yield P(where, f"storage must be one of {STORAGES}")
            template = row.get("key", "")
            chunks = template.split("/") if template else []
            for c in chunks:
                if not KEY_CHUNK.match(c):
                    yield P(
                        where,
                        f"key chunk {c!r} is not a lowercase literal or a single {{var}}",
                    )
            variables = key_variables(template)
            if shape == "instance" and "producer" not in variables:
                yield P(where, "an instance key carries {producer} (§2.1.1)")
            if shape == "slot" and "producer" in variables:
                yield P(where, "a slot key carries no {producer} (§2.1.1)")
            if shape == "slot" and not row.get("writer_field"):
                yield P(
                    where,
                    "a slot names the payload field that carries the writer (§2.1.1)",
                )
            if resolver.enabled and payload:
                for v in variables:
                    if v == "producer":
                        continue
                    fd = _field(resolver, payload, v)
                    if fd is None:
                        yield P(
                            where, f"key variable {{{v}}} is not a field of {payload}"
                        )
                    elif fd.type != fd.TYPE_STRING or fd.is_repeated:
                        yield P(
                            where, f"key variable {{{v}}} must be a single string field"
                        )
                wf = row.get("writer_field")
                if wf and _field(resolver, payload, wf) is None:
                    yield P(where, f"writer_field {wf!r} is not a field of {payload}")

        # --- actions ------------------------------------------------------
        actions = d.get("actions") or {}
        for name, a in actions.items():
            where = f"actions.{name}"
            if not isinstance(a, dict):
                yield P(where, "must be a mapping")
                continue
            for r in roles_of(a):
                if r not in roles:
                    yield P(where, f"by: unknown role {r!r}")
            if not roles_of(a):
                yield P(where, "needs by: role(s)")
            kind = action_kind(a)
            if kind is None:
                yield P(where, f"needs exactly one of {ACTION_KINDS}")
                continue
            if kind in ("publish", "get") and a[kind] not in payload_of:
                yield P(
                    where, f"{kind}: {a[kind]!r} is not one of this protocol's keys"
                )
            if kind == "rpc":
                tk, target = rpc_target(a)
                if tk == "invalid":
                    yield P(where, "rpc: must be 'interface/vN' or {class: name}")
                elif tk == "interface" and resolver.enabled:
                    iface = target.split(".")[0]
                    if not resolver.interface_known(iface):
                        yield P(where, f"rpc: {iface!r} is not in interfaces.yaml")
            if "sets" in a and kind != "publish":
                yield P(where, "sets: only makes sense on a publish action")
            if resolver.enabled and kind == "publish" and "sets" in a:
                payload = payload_of.get(a["publish"])
                for f_name, value in (a["sets"] or {}).items():
                    fd = _field(resolver, payload, f_name) if payload else None
                    if fd is None:
                        yield P(where, f"sets: {f_name!r} is not a field of {payload}")
                    else:
                        err = _is_valid_for(fd, value)
                        if err:
                            yield P(where, f"sets.{f_name}: {err}")

        # --- lifecycle ----------------------------------------------------
        # One subject may have more than one lifecycle — a mode and the sub_mode
        # refining it — and then each names its field in `of`, so the two are
        # told apart here and do not collide under one heading in the docs.
        lifecycles_of: Dict[str, List[Any]] = {}
        for i, lc in enumerate(d.get("lifecycle") or []):
            where = f"lifecycle[{i}]"
            subject = lc.get("subject")
            if subject not in payload_of:
                yield P(
                    where, f"subject {subject!r} is not one of this protocol's keys"
                )
                continue
            payload = lc.get("payload")
            if payload != payload_of[subject]:
                yield P(
                    where,
                    f"payload {payload!r} differs from the key row's {payload_of[subject]!r}",
                )
            lifecycles_of.setdefault(subject, []).append(lc.get("of"))
            if lc.get("of") and resolver.enabled and payload:
                if _field(resolver, payload, lc["of"]) is None:
                    yield P(where, f"of: {lc['of']!r} is not a field of {payload}")
            states = {s["name"]: s for s in lc.get("states") or [] if "name" in s}
            for s_name, st in states.items():
                sw = f"{where}.states.{s_name}"
                wire = "field" in st
                if wire == ("derived" in st):
                    yield P(
                        sw,
                        "a state is either field+is/present or derived, not both or neither",
                    )
                    continue
                if wire:
                    if ("is" in st) == ("present" in st):
                        yield P(sw, "a wire state has exactly one of is: / present:")
                    if resolver.enabled:
                        fd = _field(resolver, payload, st["field"])
                        if fd is None:
                            yield P(
                                sw, f"field {st['field']!r} is not a field of {payload}"
                            )
                        elif "is" in st:
                            err = _is_valid_for(fd, st["is"])
                            if err:
                                yield P(sw, err)
                        elif "present" in st and not fd.has_presence:
                            yield P(
                                sw,
                                f"field {st['field']!r} has no presence; present: cannot be observed",
                            )
            for j, t in enumerate(lc.get("transitions") or []):
                tw = f"{where}.transitions[{j}]"
                for k in set(t) - TRANSITION_KEYS:
                    yield P(tw, f"unknown key {k!r} (an unquoted comma in a note?)")
                for end in ("from", "to"):
                    if end in t and t[end] not in states:
                        yield P(tw, f"{end}: unknown state {t[end]!r}")
                on = t.get("action")
                if on not in actions:
                    yield P(tw, f"action: unknown action {on!r}")
                    continue
                target = states.get(t.get("to"))
                if target is None:
                    continue
                a = actions[on]
                kind = action_kind(a)
                if "derived" in target:
                    if kind != "clock":
                        yield P(
                            tw,
                            f"{t['to']!r} is derived; only a clock action reaches it, not {on!r} ({kind})",
                        )
                else:
                    if kind != "publish" or a.get("publish") != subject:
                        yield P(
                            tw,
                            f"{on!r} does not publish {subject!r}, so it cannot move it to {t['to']!r}",
                        )
                    elif not _state_holds(target, a.get("sets") or {}):
                        yield P(
                            tw,
                            f"{on!r} sets {a.get('sets')!r}, which does not satisfy state {t['to']!r}",
                        )

        for subject, labels in lifecycles_of.items():
            if len(labels) < 2:
                continue
            where = f"lifecycle of {subject}"
            if any(label is None for label in labels):
                yield P(
                    where,
                    "a subject with more than one lifecycle names the field each "
                    "one follows in of:",
                )
            elif len(set(labels)) != len(labels):
                yield P(where, f"two lifecycles share the same of: {sorted(labels)}")

        # --- flows --------------------------------------------------------
        for f_name, flow in (d.get("flows") or {}).items():
            where = f"flows.{f_name}"
            if (
                not isinstance(flow, dict)
                or "description" not in flow
                or "steps" not in flow
            ):
                yield P(where, "needs description and steps")
                continue
            for j, step in enumerate(flow["steps"]):
                sw = f"{where}.steps[{j}]"
                for k in set(step) - STEP_KEYS:
                    yield P(sw, f"unknown key {k!r} (an unquoted comma in a note?)")
                if step.get("action") not in actions:
                    yield P(sw, f"unknown action {step.get('action')!r}")
                    continue
                if "role" in step and step["role"] not in roles_of(
                    actions[step["action"]]
                ):
                    yield P(
                        sw,
                        f"role {step['role']!r} is not one of the action's by: roles",
                    )

    for subject, ps in owners.items():
        if len(ps) > 1:
            yield Problem(
                "*", f"subject {subject}", f"claimed by more than one protocol: {ps}"
            )
