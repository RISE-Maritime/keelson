#! /bin/bash
set -euo pipefail

# This file generates code for all keelson-sdks

# Enables globstar feature of bash
shopt -s globstar

## Python
	# Clean up all old generated files
	echo "Cleaning up old files..."
	rm -rf python/keelson/**/**/*_pb2*

	# Copy tags.yaml (forcing an overwrite)
	echo "Copying tags.yaml..."
	cp -rf ../messages/subjects.yaml python/keelson/subjects.yaml 

	# Generate code for core.proto
	echo "Generating code for core.proto..."
	protoc \
		--python_out=python/keelson \
		--proto_path ../messages \
		../messages/core.proto

	# Creating a directory for the payloads if it doesnt already exists
	echo "Creating directory for payloads..."
	mkdir -p python/keelson/payloads

	# Generate code for payloads
	echo "Generating code for payloads..."
	protoc \
		--python_out=python/keelson/payloads \
		--proto_path=../messages/payloads \
		--descriptor_set_out=python/keelson/payloads/protobuf_file_descriptor_set.bin \
		--include_imports \
		../messages/payloads/*.proto

	# Ensuring the generated code for foxglove is importable as a subpackage
	echo "Change imports to relative..."
	sed -E -i 's/^import/from . import/g' python/keelson/payloads/*_pb2.py