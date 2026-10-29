#!/usr/bin/env python3
"""Weekly report of available Python dependency updates and known vulnerabilities.

Dependabot cannot bump this workspace on its own: connector requirements.txt
pins feed uv.lock through dynamic metadata, and a bump there needs a re-lock.
So instead of PRs, the dependency-report workflow runs this and posts the
markdown it prints to a tracking issue. Nothing here changes the lock.

Reads the current venv (synced with every package and group) and uv.lock:

- uv lock --upgrade --dry-run   what a plain re-lock would take, within every
                                declared range and pin
- uv pip list --outdated        everything behind its latest release
- pip-audit                     known vulnerabilities in the locked versions

Usage: uv run --no-sync python scripts/dependency_report.py > report.md
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIP_AUDIT = "pip-audit==2.10.1"


def run(*args: str) -> str:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"{' '.join(args)} failed:\n{result.stderr}")
    return result.stdout + result.stderr


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def relock_targets() -> dict[str, str]:
    """Package -> version a plain `uv lock --upgrade` would move it to."""
    out = run("uv", "lock", "--upgrade", "--dry-run")
    return {
        norm(m[1]): m[3]
        for m in re.finditer(r"^Update (\S+) v(\S+) -> v(\S+)$", out, re.M)
    }


def outdated() -> list[dict]:
    out = run("uv", "pip", "list", "--outdated", "--format", "json")
    return json.loads(out[out.index("[") :])


def declared_by(name: str) -> list[str]:
    """Where the workspace itself declares `name`, e.g. `connectors/tak`."""
    pattern = re.compile(rf"^\s*\"?{re.escape(name)}\s*(\[[^\]]*\])?\s*[=<>~!]", re.I)
    found = []
    for req in sorted(ROOT.glob("connectors/*/requirements.txt")):
        if any(pattern.match(line) for line in req.read_text().splitlines()):
            found.append(str(req.parent.relative_to(ROOT)))
    for toml in ["pyproject.toml", "sdks/python/pyproject.toml"]:
        if any(pattern.match(line) for line in (ROOT / toml).read_text().splitlines()):
            found.append(toml)
    return found


def export(*flags: str) -> set[str]:
    out = run(
        "uv",
        "export",
        "--frozen",
        "--format",
        "requirements-txt",
        "--no-emit-workspace",
        "--no-hashes",
        "--no-header",
        "--no-annotate",
        *flags,
    )
    return {line for line in out.splitlines() if "==" in line}


def vulnerabilities() -> tuple[list[dict], set[str]]:
    """Known vulnerabilities in every locked package, and the names that ship."""
    everything = export("--all-groups")
    shipped = {norm(line.split("==")[0]) for line in export("--no-dev")}
    with tempfile.NamedTemporaryFile("w", suffix=".txt") as req:
        req.write("\n".join(sorted(everything)) + "\n")
        req.flush()
        result = subprocess.run(
            [
                "uvx",
                PIP_AUDIT,
                "--no-deps",
                "--disable-pip",
                "--progress-spinner",
                "off",
            ]
            + ["--format", "json", "-r", req.name],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
    # pip-audit exits 1 when it finds something; anything else is a failure.
    if result.returncode not in (0, 1):
        sys.exit(f"pip-audit failed:\n{result.stderr}")
    # The export lists a package once per environment marker; key by id.
    found = {
        (dep["name"], vuln["id"]): {
            "name": dep["name"],
            "version": dep["version"],
            **vuln,
        }
        for dep in json.loads(result.stdout)["dependencies"]
        for vuln in dep.get("vulns", [])
    }
    return list(found.values()), shipped


def pypi(name: str) -> str:
    return f"[{name}](https://pypi.org/project/{name}/#history)"


def main():
    targets = relock_targets()
    vulns, shipped = vulnerabilities()

    relock, held = [], []
    for pkg in sorted(outdated(), key=lambda p: norm(p["name"])):
        name, have, latest = norm(pkg["name"]), pkg["version"], pkg["latest_version"]
        if targets.get(name) == latest:
            relock.append((name, have, latest))
        else:
            held.append((name, have, targets.get(name, have), latest))

    lines = ["## Known vulnerabilities", ""]
    if vulns:
        lines += [
            "| Package | Locked | ID | Fixed in | Ships in image |",
            "|---|---|---|---|---|",
        ]
        # What ships in the image first.
        for v in sorted(
            vulns, key=lambda v: (norm(v["name"]) not in shipped, v["name"], v["id"])
        ):
            ids = ", ".join([v["id"], *sorted(v.get("aliases", []))])
            fixed = ", ".join(v.get("fix_versions", [])) or "no fix yet"
            ships = "yes" if norm(v["name"]) in shipped else "no (dev/docs)"
            lines.append(
                f"| {pypi(v['name'])} | {v['version']} | {ids} | {fixed} | {ships} |"
            )
    else:
        lines.append("None known (pip-audit, PyPI advisory database).")

    lines += ["", f"## A re-lock would take these ({len(relock)})", ""]
    if relock:
        lines += ["`uv lock --upgrade-package <name>`, then re-export.", ""]
        lines += ["| Package | Locked | Latest |", "|---|---|---|"]
        lines += [f"| {pypi(n)} | {have} | {new} |" for n, have, new in relock]
    else:
        lines.append("Nothing.")

    lines += ["", f"## Held back by a declared pin or range ({len(held)})", ""]
    if held:
        lines += [
            "Needs the declaration changed first (a connector `requirements.txt`: "
            "then `uv lock --refresh-package keelson-connector-<x>`). "
            "Not declared by us: held by another package's range.",
            "",
            "| Package | Locked | Re-lock gets | Latest | Declared in |",
            "|---|---|---|---|---|",
        ]
        for n, have, gets, newest in held:
            where = ", ".join(f"`{d}`" for d in declared_by(n)) or "—"
            lines.append(f"| {pypi(n)} | {have} | {gets} | {newest} | {where} |")
    else:
        lines.append("Nothing.")

    print("\n".join(lines))


if __name__ == "__main__":
    main()
