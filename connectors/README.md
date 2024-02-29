# Connectors

This folder contain the sources of different connectors to keelson. A connector to keelson can be:

* a publisher of data, and/or
* a subscriber of data from another publisher, and/or
* a responder to requests, and/or
* a requester

Commonly, a connector is either a communication bridge towards

* a sensor hardware
* another middleware
* a centralized data provider
* an HMI
* a logging system
* etc

For example, a connector to a sensor hardware is, typically, a publisher of data and, possibly, a responder to requests for changing the sensor settings. 

For details of each of the connectors provided in this repo, see the specific README.md in that subfolder.

## Externally developed connectors

The following are a list of known (not necessarily open-source) connectors housed elsewhere:

[keelson-connector-mavlink](https://github.com/MO-RISE/keelson-connector-mavlink)

[keelson-connector-haddock](https://github.com/MO-RISE/keelson-connector-haddock)

