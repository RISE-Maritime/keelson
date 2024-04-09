#! /bin/bash
set -euo pipefail

# This file generates code for all keelson-sdks

# Enables globstar feature of bash
shopt -s globstar

## Python
./python/generate_python.sh

## Javascript
./js/generate_javascript.sh