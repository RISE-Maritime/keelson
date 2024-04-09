#! /bin/bash
set -euo pipefail

# This file generates code for all keelson-sdks

# Enables globstar feature of bash
shopt -s globstar

## Python
	echo "Generating code for Python..."

	# Clean up all old generated files
	echo "	Cleaning up old files..."
	rm -rf python/keelson/**/**/*_pb2*

	# Copy tags.yaml (forcing an overwrite)
	echo "	Copying subjects.yaml..."
	cp -rf ../messages/subjects.yaml python/keelson/subjects.yaml 

	# Generate code for Envelope.proto
	echo "	Generating code for Envelope.proto..."
	protoc \
		--python_out=python/keelson \
		--proto_path ../messages \
		../messages/Envelope.proto

	# Creating a directory for the payloads if it doesnt already exists
	echo "	Creating directory for payloads..."
	mkdir -p python/keelson/payloads

	# Generate code for payloads
	echo "	Generating code for payloads..."
	protoc \
		--python_out=python/keelson/payloads \
		--proto_path=../messages/payloads \
		--descriptor_set_out=python/keelson/payloads/protobuf_file_descriptor_set.bin \
		--include_imports \
		../messages/payloads/*.proto

	# Ensuring the generated code for foxglove is importable as a subpackage
	echo "	Change imports to relative..."
	sed -E -i 's/^import/from . import/g' python/keelson/payloads/*_pb2.py

	echo "Python done!"

## JAVASCRIPT
	echo "Generating code for Javascript..."

	cd js

  npm install

	chmod +x ./generate_js.sh
	./generate_js.sh
	cd ..

	echo "Javascript done!"