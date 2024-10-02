# Keelson-SDK (javascript)

A javascript SDK for [keelson](https://github.com/MO-RISE/keelson).

## Basic usage

See the [tests](https://github.com/MO-RISE/keelson/blob/main/sdks/js/keelson/index.test.ts)

## Development setup

Step 1: Generate protobuf messages

```bash
# Make sure you have rights to execute 
chmod +x generate_javascript.sh 
# Execute shell command
./generate_javascript.sh 
```

Step 2: Run tests

```bash
# Make sure you have test package installed 
npm install --save-dev jest @types/jest ts-jest

# Run test by 
npx jest
```
