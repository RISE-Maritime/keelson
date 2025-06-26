echo "Generating code for Rust..."

# Working directory is the directory in which this script is located!
cd "$(dirname "$0")"

# Copy subjects.yaml (forcing an overwrite)
echo "\tCopying subjects.yaml..."
cp -rf ../../messages/subjects.yaml src/subjects.yaml

echo "Rust: subjects.yaml copied. Protobuf code is now generated via build.rs and prost-build."