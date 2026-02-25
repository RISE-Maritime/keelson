#!/bin/bash
set -euo pipefail

# Install uv
echo "Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

# Add uv to PATH for future shells
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc

# Install dependencies for docs
echo "Installing documentation dependencies..."
sudo wget -O /usr/bin/protodot https://protodot.seamia.net/binaries/linux && sudo chmod +x /usr/bin/protodot
sudo apt-get update && sudo apt-get -y install graphviz

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
