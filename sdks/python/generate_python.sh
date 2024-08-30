echo "Generating code for Python..."

# Working directory is the directory in which this script is located!
cd "$(dirname "$0")"

# Clean up all old generated files
echo "	Cleaning up old files..."
rm -rf keelson/**/**/*_pb2*

# Copy tags.yaml (forcing an overwrite)
echo "	Copying subjects.yaml..."
cp -rf ../../messages/subjects.yaml keelson/subjects.yaml 

# Generate code for Envelope.proto
echo "	Generating code for Envelope.proto..."
protoc \
    --python_out=keelson \
    --pyi_out=keelson/payloads \
    --proto_path ../../messages \
    ../../messages/Envelope.proto

# Creating a directory for the payloads if it doesnt already exists
echo "	Creating directory for payloads..."
mkdir -p keelson/payloads

# Generate code for payloads
echo "	Generating code for payloads..."
protoc \
    --python_out=keelson/payloads \
    --pyi_out=keelson/payloads \
    --proto_path=../../messages/payloads \
    --descriptor_set_out=keelson/payloads/protobuf_file_descriptor_set.bin \
    --include_imports \
    ../../messages/payloads/*.proto

# Ensuring the generated code for foxglove is importable as a subpackage
echo "	Change imports to relative..."
sed -E -i 's/^import/from . import/g' keelson/payloads/*_pb2.py

echo "Python done!"