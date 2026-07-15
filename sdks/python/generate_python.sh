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

# Copy qos.yaml (subject -> QoS profile assignments)
echo "	Copying qos.yaml..."
cp -rf ../../messages/qos.yaml keelson/qos.yaml

# Copy interfaces.yaml (well-known RPC interface registry)
echo "	Copying interfaces.yaml..."
cp -rf ../../messages/interfaces.yaml keelson/interfaces.yaml

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

# Rewrite bare peer-pb2 imports (e.g. `import Audio_pb2 as ...`) into
# relative imports. protoc emits absolute imports when a .proto file
# imports a sibling .proto with no path prefix, which fails when the
# generated module lives inside a package.
sed -E -i 's/^import ([A-Za-z0-9_]+)_pb2 as /from . import \1_pb2 as /g' keelson/payloads/*_pb2.py

# Creating a directory for the interface if it doesnt already exists
echo "	Creating directory for interfaces..."
mkdir -p keelson/interfaces

# Generate code for interfaces. The payloads directory is on the include
# path so interfaces can reference shared domain types (Coordinate,
# Mission, ...) from the keelson domain-type pool.
echo "	Generating code for interfaces..."
uv run protoc \
    --python_out=keelson/interfaces \
    --pyi_out=keelson/interfaces \
    --proto_path=../../interfaces \
    --proto_path=../../messages/payloads \
    ../../interfaces/*.proto

# Same peer-import fix-up for interfaces (enables cross-interface imports
# like a shared VehicleCommon.proto without breaking the SDK).
sed -E -i 's/^import ([A-Za-z0-9_]+)_pb2 as /from . import \1_pb2 as /g' keelson/interfaces/*_pb2.py

# Imports of shared domain types must resolve to the payloads package
# (single generation point — the descriptor pool rejects a second copy of
# the same .proto file). Rewrite any peer import whose module actually
# lives in keelson/payloads.
for payload_module in keelson/payloads/*_pb2.py; do
    module_name="$(basename "${payload_module%.py}")"
    sed -E -i "s/^from \. import ${module_name} as /from keelson.payloads import ${module_name} as /g" keelson/interfaces/*_pb2.py
done

echo "Python done!"