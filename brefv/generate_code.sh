#! /bin/bash
set -euo pipefail

# This file generates code for all brefv implementations

# Enables globstar feature of bash
shopt -s globstar

## Python
	# Clean up all old generated files
	echo "Cleaning up old files..."
	rm -rf python/brefv/**/*_pb2*

	# Copy tags.yaml (forcing an overwrite)
	echo "Copying tags.yaml..."
	cp -rf tags.yaml python/brefv/tags.yaml 

	# Generate code for core.proto
	echo "Generating code for core.proto..."
	protoc \
		--python_out=python/brefv \
		--proto_path . \
		core.proto

	# Creating a directory for the payloads if it doesnt already exists
	echo "Creating directory for payloads..."
	mkdir -p python/brefv/payloads

	# Generate code for payloads
	echo "Generating code for payloads..."
	protoc \
		--python_out=python/brefv/payloads \
		--proto_path=payloads \
		--descriptor_set_out=python/brefv/payloads/protobuf_file_descriptor_set.bin \
		--include_imports \
		payloads/**/*.proto

	# Ensuring the generated code for foxglove is importable as a subpackage
	echo "Change imports to relative..."
	sed -E -i 's/from foxglove import/from . import/g' python/brefv/payloads/foxglove/*_pb2.py
	sed -E -i 's/from foxglove import/from ..foxglove import/g' python/brefv/payloads/compound/*_pb2.py