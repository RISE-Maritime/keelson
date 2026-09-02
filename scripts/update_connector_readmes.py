"""Refresh the pasted ``--help`` dumps in ``connectors/*/README.md``.

Every connector README documents its binaries by pasting the output of
``--help`` into a fenced block. Pasted by hand, they drift: a flag added to the
common parser reaches every binary at once and no README, and a renamed binary
leaves its old name behind in the one place a reader looks for it. Five of the
blocks were still named after commands that no longer answer to those names.

So the dumps are captured rather than typed. Each block is located by its
``usage: <prog>`` first line, the corresponding binary is run with ``--help``,
and the block body is replaced with what it printed.

**Captured from the Docker image, not the host.** ``docker/Dockerfile`` puts
every ``connectors/*/bin/*.py`` on ``PATH`` with all connector dependencies
installed — the only environment where all 23 answer ``--help`` without a
hardware SDK or a platform-specific wheel getting in the way. CI's
``docker-build`` job already relies on exactly that.

Usage::

    docker build --platform linux/amd64 -f docker/Dockerfile -t keelson .
    uv run python scripts/update_connector_readmes.py            # rewrite
    uv run python scripts/update_connector_readmes.py --check    # drift only
"""

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
CONNECTORS = REPO / "connectors"

IMAGE = "keelson"
PLATFORM = "linux/amd64"

# Terminal width argparse renders at. Pinned because the width is otherwise
# whatever the terminal of whoever last pasted a block happened to be: the
# committed dumps ranged from 71 to 146 columns, which makes a real change
# indistinguishable from a reflow in review.
COLUMNS = "100"

# A block's `usage:` line names the prog argparse was given, which for five
# blocks is a command that has since been renamed. The README is the stale
# side, so the alias maps the name as written to the binary that exists now;
# regenerating then corrects the `usage:` line itself.
ALIASES = {
    "foxglove-liveview": "keelson2foxglove",
    "klog-record": "keelson2klog",
    "mediamtx": "mediamtx-whep",
    "fake_radar": "mockup-radar2keelson",
    "platform-geometry": "platform-geometry2keelson",
}

# Fence, body starting with a `usage:` line, closing fence. The fence tag is
# captured so a ```text block stays a ```text block.
BLOCK = re.compile(
    r"(?P<open>^```(?P<tag>[^\n]*)\n)(?P<body>usage: (?P<prog>\S+).*?)(?P<close>^```$)",
    re.MULTILINE | re.DOTALL,
)


def capture_help(prog: str) -> str:
    """Run ``prog --help`` in the image and return stdout.

    stdout only: the entrypoint prints a startup banner and some connectors
    emit import warnings, all on stderr. argparse writes ``--help`` to stdout,
    so the streams already separate cleanly.
    """
    result = subprocess.run(
        # fmt: off
        [
            "docker", "run", "--platform", PLATFORM, "--rm",
            "-e", f"COLUMNS={COLUMNS}", IMAGE, f"{prog} --help",
        ],
        # fmt: on
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.startswith("usage:"):
        raise SystemExit(
            f"`{prog} --help` did not produce a help text (exit "
            f"{result.returncode}).\n{result.stderr.strip()}"
        )
    return result.stdout.rstrip("\n") + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report drift and exit non-zero instead of rewriting.",
    )
    args = parser.parse_args()

    stale: list[str] = []
    rewritten = 0
    blocks = 0

    for readme in sorted(CONNECTORS.glob("*/README.md")):
        original = readme.read_text()
        seen: list[str] = []

        def replace(match: re.Match) -> str:
            nonlocal blocks
            blocks += 1
            written = match.group("prog")
            prog = ALIASES.get(written, written)
            seen.append(prog)
            return match.group("open") + capture_help(prog) + match.group("close")

        updated = BLOCK.sub(replace, original)
        if not seen:
            continue

        if updated != original:
            stale.append(f"{readme.relative_to(REPO)}: {', '.join(seen)}")
            if not args.check:
                readme.write_text(updated)
                rewritten += 1

    if args.check:
        if stale:
            print("Connector README --help dumps are out of date:", file=sys.stderr)
            for line in stale:
                print(f"  {line}", file=sys.stderr)
            print(
                "\nRegenerate with:\n"
                "  docker build --platform linux/amd64 -f docker/Dockerfile -t keelson .\n"
                "  uv run python scripts/update_connector_readmes.py",
                file=sys.stderr,
            )
            return 1
        print(f"{blocks} --help dumps are up to date.")
        return 0

    print(f"Checked {blocks} --help dumps; rewrote {rewritten} README(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
