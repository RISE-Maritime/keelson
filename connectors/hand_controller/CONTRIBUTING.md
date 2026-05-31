# Contributing

Thanks for your interest in improving the Keelson hand-controller connector.

## Development setup

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv pip install --system ".[dev]"
```

This installs the runtime dependencies (`keelson`, `skarv`, `environs`), plus
the dev tools (`black`, `pylint`, `pytest`). Contributors working on the
host-side relay (`bin/hid_relay.py`) also want `.[relay]` for `pygame-ce`.

## Running the test suite

```bash
pytest tests/ -v
```

## Lint and format

```bash
black --check bin/*
pylint bin/*
```

`black bin/*` (without `--check`) applies formatting in place. CI runs all
three checks on every PR; see [.github/workflows/ci-checks.yml](.github/workflows/ci-checks.yml).

## Architecture and codebase guidance

[CLAUDE.md](CLAUDE.md) documents the codebase layout, the HID wire format,
the cross-platform relay architecture, and the controller-profile system.
Start there before larger changes.

## Pull requests

- Branch off `main`. CI runs on every PR regardless of base.
- Keep PRs focused; bundle unrelated cleanups separately.
- Add or update tests in [tests/](tests/) for behaviour changes.
- Make sure `black`, `pylint`, and `pytest` all pass locally before pushing.
