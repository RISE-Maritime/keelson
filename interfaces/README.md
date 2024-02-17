# Interfaces

This folder contain the sources of different interfaces to keelson. An interface to keelson can be:

* a publisher of data, and/or
* a subscriber of data from another publisher, and/or
* a responder to requests, and/or
* a requester

Commonly, an interface is either an interface towards

* a sensor hardware
* another middleware
* a centralized data provider
* an HMI
* a logging system
* etc

For example, an interface to a sensor hardware is, typically, a publisher of data and, possibly, a responder to requests for changing the sensor settings. 

For details of each of the interfaces provided in this repo, see the specific README.md in that subfolder.
