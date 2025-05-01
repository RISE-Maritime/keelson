#!/bin/bash

# Install python requirements
pip3 install -r requirements_dev.txt -r requirements_connectors.txt

# Install javascript dependencies
npm install --prefix sdks/js 

# Install dependencies for docs
sudo wget -O /usr/bin/protodot https://protodot.seamia.net/binaries/linux && sudo chmod +x /usr/bin/protodot
sudo apt-get update && sudo apt-get install graphviz

# Generate code for SDKs
chmod +x sdks/generate_code.sh && sdks/generate_code.sh

# Build docs
chmod +x 
