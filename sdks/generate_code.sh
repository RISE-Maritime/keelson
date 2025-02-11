#! /bin/bash

echo "Generating code..."

set -euo pipefail

# This file generates code for all keelson-sdks

# Enables globstar feature of bash
shopt -s globstar

## Python
chmod +x ./sdks/python/generate_python.sh
./sdks/python/generate_python.sh

## Javascript
chmod +x ./sdks/js/generate_javascript.sh
./sdks/js/generate_javascript.sh