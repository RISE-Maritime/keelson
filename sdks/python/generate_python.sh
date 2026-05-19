set -euo pipefail

echo "Generating code for Python..."

# Working directory is the directory in which this script is located!
cd "$(dirname "$0")"

# Clean up all old generated files
echo "	Cleaning up old files..."
rm -rf keelson/*_pb2*
rm -rf keelson/**/*_pb2*
rm -rf keelson/**/**/*_pb2*

# Copy tags.yaml (forcing an overwrite)
echo "	Copying subjects.yaml..."
cp -rf ../../messages/subjects.yaml keelson/subjects.yaml 

# Generate code for Envelope.proto
echo "	Generating code for Envelope.proto..."
uv run protoc \
    --python_out=keelson \
    --pyi_out=keelson \
    --proto_path ../../messages \
    ../../messages/Envelope.proto

# Creating a directory for the payloads if it doesnt already exists
echo "	Creating directory for payloads..."
mkdir -p keelson/payloads

# Generate code for payloads
echo "	Generating code for payloads..."
uv run protoc \
    --python_out=keelson/payloads \
    --pyi_out=keelson/payloads \
    --proto_path=../../messages/payloads \
    --descriptor_set_out=keelson/payloads/protobuf_file_descriptor_set.bin \
    --include_imports \
    ../../messages/payloads/*.proto \
    ../../messages/payloads/**/*.proto

# Ensuring the generated code for foxglove is importable as a subpackage
echo "	Post-processing generated code for foxglove package..."
sed -E -i 's/^from foxglove import/from . import/g' keelson/payloads/foxglove/*_pb2.py
sed -E -i 's/^from foxglove import/from .foxglove import/g' keelson/payloads/*_pb2.py
touch keelson/payloads/foxglove/__init__.py

# Creating a directory for the interface if it doesnt already exists
echo "	Creating directory for interfaces..."
mkdir -p keelson/interfaces

# Generate code for interfaces
echo "	Generating code for interfaces..."
uv run protoc \
    --python_out=keelson/interfaces \
    --pyi_out=keelson/interfaces \
    --proto_path=../../interfaces \
    ../../interfaces/*.proto

echo "Python done!"