# Keelson TypeScript SDK

Modern TypeScript SDK for **Keelson**, built on top of [`@eclipse-zenoh/zenoh-ts`](https://www.npmjs.com/package/@eclipse-zenoh/zenoh-ts).  
This repo auto-generates protobuf message bindings from the Keelson spec and exposes a typed client.

---

## ✨ Quick start

```bash
# 1) Install deps
npm install

# 2) Generate code (downloads proto sources & runs protoc)
npm run generate

# 3) Build TypeScript → dist
npm run build

# 4) Run tests
npm test
