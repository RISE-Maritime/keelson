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

Install the local keelson package in editable mode as follows (from the repository root):

`pip install -e sdks/python/ --config-settings editable_mode=strict`
