# Infrastructure guidelines

A good first overview of the possible infrastructure setups using Zenoh can be found [here](https://zenoh.io/docs/getting-started/deployment/). In general, keelson supports any infrastructure constellation that is supported by Zenoh but has some additional recommendations:

* mTLS should be used for for router-to-router connections, see [here](https://zenoh.io/docs/manual/tls/)
* proper role-based access-control should be used as soon as Zenoh support this.

In order to provide "seamless" connectivity between several geographically distributed edge deployments at least one router must be deployed in the "cloud" with a static address. This router will act as a proxy between the edge deployments.