echo "Generating code for javascript"

# Working directory is the directory in which this script is located!
cd "$(dirname "$0")"

# Remove everything except manually written files
echo "  Cleaning up old files..."
rm -rf keelson/payloads
rm -rf keelson/google
rm keelson/Envelope.ts
rm keelson/subjects.json
rm keelson/typeRegistry.ts

echo "  Creating directories"
mkdir -p keelson/payloads
mkdir -p ../../messages/payloads/js

echo "      Converting subjects.yaml to json"
npx js-yaml ../../messages/subjects.yaml >> keelson/subjects.json
npx js-yaml ../../messages/procedures.yaml >> keelson/procedures.json



echo "  Generating code for Envelope.proto..."
protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson \
    --proto_path ../../messages \
    --ts_proto_opt=outputTypeRegistry=true \
    --ts_proto_opt=esModuleInterop=true \
    ../../messages/Envelope.proto
    # --ts_proto_opt=useDate=false \

echo "  Generating payloads"
protoc \
    --plugin=./node_modules/.bin/protoc-gen-ts_proto \
    --ts_proto_out=keelson/payloads \
    --proto_path=../../messages/payloads \
    --ts_proto_opt=esModuleInterop=true \
    --ts_proto_opt=outputIndex=true \
    --ts_proto_opt=outputTypeRegistry=true \
    ../../messages/payloads/*.proto

echo "Javascript done!"