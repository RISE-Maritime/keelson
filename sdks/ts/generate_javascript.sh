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

echo "  Creating directories"
mkdir -p keelson/payloads
# mkdir -p ../../messages/payloads/js

echo "      Converting subjects.yaml to json"
npx js-yaml ../../messages/subjects.yaml >> keelson/subjects.json


echo "  Generating code for Envelope.proto..."
protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson \
    --proto_path ../../messages \
    --ts_proto_opt=env=browser \
    --ts_proto_opt=importSuffix=.ts \
    ../../messages/Envelope.proto

echo "  Generating payloads"
protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson/payloads \
    --proto_path=../../messages/payloads \
    --ts_proto_opt=esModuleInterop=true \
    --ts_proto_opt=outputIndex=true \
    --ts_proto_opt=outputTypeRegistry=true \
    --ts_proto_opt=env=browser \
    --ts_proto_opt=importSuffix=.ts \
    ../../messages/payloads/*.proto \
    ../../messages/payloads/**/*.proto

# Creating a directory for the interface if it doesnt already exists
echo "	Creating directory for interfaces..."
mkdir -p keelson/interfaces

# Generate code for interfaces
echo "	Generating code for interfaces..."
protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson/interfaces \
    --proto_path=../../interfaces \
    --ts_proto_opt=env=browser \
    --ts_proto_opt=importSuffix=.ts \
    ../../interfaces/*.proto

echo "Javascript done!"
