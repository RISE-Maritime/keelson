set -euo pipefail

echo "Generating code for javascript"

# Working directory is the directory in which this script is located!
cd "$(dirname "$0")"

# Remove everything except manually written files
echo "  Cleaning up old files..."
rm -rf keelson/payloads
rm -rf keelson/interfaces
rm -rf keelson/google
rm -rf keelson/Envelope.ts
rm -rf keelson/subjects.json
rm -rf keelson/qos.json

echo "  Creating directories"
mkdir -p keelson/payloads
# mkdir -p ../../messages/payloads/js

echo "      Converting subjects.yaml to json"
npx js-yaml ../../messages/subjects.yaml >> keelson/subjects.json

echo "      Converting qos.yaml to json"
npx js-yaml ../../messages/qos.yaml >> keelson/qos.json


echo "  Generating code for Envelope.proto..."
uv run protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson \
    --proto_path ../../messages \
    ../../messages/Envelope.proto

echo "  Generating payloads"
uv run protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson/payloads \
    --proto_path=../../messages/payloads \
    --ts_proto_opt=esModuleInterop=true \
    --ts_proto_opt=outputIndex=true \
    --ts_proto_opt=outputTypeRegistry=true \
    ../../messages/payloads/*.proto \
    ../../messages/payloads/**/*.proto

# Creating a directory for the interface if it doesnt already exists
echo "	Creating directory for interfaces..."
mkdir -p keelson/interfaces

# Generate code for interfaces. Interfaces may import shared domain types
# (Coordinate, Mission, ...) from the keelson domain-type pool; those
# imported payload protos are compiled into keelson/interfaces/ as well so
# ts-proto's relative imports resolve (unlike Python, ts-proto has no
# global descriptor pool, so the duplicate compilation is harmless).
echo "	Generating code for interfaces..."
DOMAIN_PROTOS=""
for imported in $(grep -hoE '^import "[A-Za-z0-9_]+\.proto"' ../../interfaces/*.proto | sed -E 's/^import "(.*)"$/\1/' | sort -u); do
    if [ -f "../../messages/payloads/${imported}" ]; then
        DOMAIN_PROTOS="${DOMAIN_PROTOS} ../../messages/payloads/${imported}"
    fi
done
uv run protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson/interfaces \
    --proto_path=../../interfaces \
    --proto_path=../../messages/payloads \
    ../../interfaces/*.proto ${DOMAIN_PROTOS}

echo "Javascript done!"