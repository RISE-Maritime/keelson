#!/bin/bash
set -euo pipefail

# Workspace setup only. Everything slow and the same for every workspace --
# uv, protodot, graphviz and ArduPilot SITL -- is in the base image
# (.devcontainer/base/Dockerfile), so this runs in a couple of minutes.

# Allow non-root access to /dev/ttyACM* devices bind-mounted from the host.
# The host nodes are owned by root:dialout, and the devcontainer.json cgroup
# rule restricts kernel access to major 166 (ttyACM) regardless of what else
# is visible under /dev.
echo "Adding $(whoami) to dialout group..."
sudo usermod -aG dialout "$(whoami)"

# Sync Python workspace with uv (includes all packages and dev/docs dependencies)
echo "Syncing Python workspace with uv..."
uv sync --all-packages --group dev --group docs

# Generate code for Python SDK (requires protoc)
echo "Generating Python SDK code..."
chmod +x sdks/python/generate_python.sh && bash sdks/python/generate_python.sh

# Install JavaScript dependencies
echo "Installing JavaScript dependencies..."
npm install --prefix sdks/js

# Generate JavaScript SDK code
echo "Generating JavaScript SDK code..."
chmod +x sdks/js/generate_javascript.sh && bash sdks/js/generate_javascript.sh

# Build docs
echo "Building documentation..."
chmod +x generate_docs.sh && ./generate_docs.sh

echo "Development environment setup complete!"
