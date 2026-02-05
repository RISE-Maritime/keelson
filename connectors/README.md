# Connectors

This folder contain the small or early stage connectors to keelson. Commonly, a connector is either a communication bridge towards

* a sensor hardware
* another middleware
* a centralized data provider
* an HMI
* a logging system
* etc

For example, a connector to a sensor hardware is, typically, a publisher of data and, possibly, a responder to requests for changing the sensor settings.

For details of each of the connectors provided in this repo, see the specific README.md in that subfolder.

## Developed connectors

You can find all developed connectors on [RISE Maritime Github page](https://github.com/RISE-Maritime)

## Development setup

This repository uses [uv](https://docs.astral.sh/uv/) for dependency management. From the repository root:

```bash
# Install all dependencies (SDK + all connectors)
uv sync --all-packages --group dev

# Or install only a specific connector
uv sync --package keelson-connector-mcap
```
