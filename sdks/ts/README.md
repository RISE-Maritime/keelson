# Keelson TypeScript SDK

Modern TypeScript SDK for **Keelson**, built on top of [`@eclipse-zenoh/zenoh-ts`](https://www.npmjs.com/package/@eclipse-zenoh/zenoh-ts).  
This repo auto-generates protobuf message bindings from the Keelson spec and exposes a typed client.

## Version control & Dependencies. 

This relies on the rust based **Zenoh** middleware, and uses the `zenoh-plugin-remote-api` bundled within `zenoh-ts`. Currently tested and working on 1.6.2.
When using the install instructions found at [`zenoh-ts`](https://www.npmjs.com/package/@eclipse-zenoh/zenoh-ts), keep in mind that this plug-in is required in any instances of `zenohd` that this SDK will talk to. 

---

## Quick start

```bash
# 1) Install deps
npm install

# 2) Generate code (downloads proto sources & runs protoc)
npm run generate

# 3) Build TypeScript
npm run build

# 4) Run tests
npm test

```
## Other scripts

The following scripts are baked into `npm` and can be executed using `npm run [SCRIPT]`

```bash
# 1) Uninstall completely 
npm run clean:all

# 2) Uninstall current message types
npm run clean

# 3) Uninstall all deps, message types and reinstall
npm run reinstall 

# 4) Start Zenoh DEAMON with premade config suitable for the SDK. 
npm run zenoh-ts

```
