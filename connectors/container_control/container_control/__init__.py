"""Host container inspection and control for Keelson.

Serves the ``container_control/v1`` RPC interface over the Docker Engine API and
publishes the host's container set (``container_status``), per-container
resource use (``container_stats``) and container logs (``log_message``). The
entry point is ``bin/docker2keelson.py``; only :mod:`container_control.backend`
imports ``docker``.
"""

#: The ``{interface}/{version}`` served, as registered in
#: ``messages/interfaces.yaml``.
INTERFACE = "container_control"
VERSION = "v1"
