# Devcontainer

`devcontainer.json` runs on a prebuilt base image,
`ghcr.io/rise-maritime/keelson-devcontainer:rover-4.5`, so creating or
rebuilding the container no longer builds ArduPilot. `post-create-script.sh`
only does what depends on the workspace: the `dialout` group, `uv sync`, SDK
generation, `npm install`, docs.

## What the image holds

Built from `base/Dockerfile`:

- `mcr.microsoft.com/devcontainers/python:3-3.14-bookworm`
- `uv`, on the system `PATH`
- `protodot` (built from source, so it runs on arm64 too) and `graphviz`, for
  the docs' proto diagrams
- **ArduPilot SITL** (`Rover-4.5`): `ardurover` and `sim_vehicle.py` on `PATH`,
  built under `/opt/ardupilot` in its own Python 3.13 venv
  (`/opt/ardupilot-venv`), by `install-ardupilot-sitl.sh`, the same script you
  would run by hand outside the image

It is published for **amd64 and arm64** by `.github/workflows/devcontainer.yml`,
on every change to `base/` or the SITL script and weekly. Tags are
`rover-4.5` and `rover-4.5-<commit>`.

## Without access to GHCR

Build the image locally under the same tag, then use **Rebuild Container**; the
devcontainer uses the local image rather than pulling:

```bash
docker build -f .devcontainer/base/Dockerfile \
  -t ghcr.io/rise-maritime/keelson-devcontainer:rover-4.5 .devcontainer
```

That builds ArduPilot once (about 10–30 minutes), not on every rebuild.

## Moving to another ArduPilot branch

Change these together: `ARDUPILOT_BRANCH` and `TAG` in
`.github/workflows/devcontainer.yml`, the `image:` here in `devcontainer.json`,
and the `container:` of the `test-mavlink-sitl` job in
`.github/workflows/ci.yml`.
