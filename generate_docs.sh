rm -rf docs/payloads
mkdir -p docs/payloads

echo "Generating docs for subjects and types"
python scripts/generate_docs_for_subjects_and_types.py messages/subjects.yaml messages/payloads/ docs/

rm -rf docs/interfaces
mkdir -p docs/interfaces

echo "Generating docs for interfaces"
python scripts/generate_docs_for_interfaces.py interfaces/ docs/
