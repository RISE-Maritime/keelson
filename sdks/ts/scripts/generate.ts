// scripts/generate.ts
import { execa } from "execa";
import fs from "node:fs";
import { mkdtemp } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import https from "node:https";

import fse from "fs-extra";
import * as tar from "tar";
import { load as yamlLoad } from "js-yaml";

// ====== CONFIG ======
const REPO = "RISE-Maritime/keelson";
// Optional override: export KEELSON_TAG="v1.2.3" or pass --tag v1.2.3
const CLI_TAG = (() => {
  const i = process.argv.indexOf("--tag");
  return i >= 0 ? process.argv[i + 1] : undefined;
})();
const ENV_TAG = process.env.KEELSON_TAG;
const TAG = CLI_TAG || ENV_TAG || null;

const API_BASE = `https://api.github.com/repos/${REPO}`;
const OUT_BASE = path.resolve("src/keelson");
const OUT_PAYLOADS = path.join(OUT_BASE, "payloads");
const OUT_INTERFACES = path.join(OUT_BASE, "interfaces");
const TS_PROTO_PLUGIN = "./node_modules/.bin/protoc-gen-ts_proto";

const GITHUB_TOKEN = process.env.GITHUB_TOKEN;

// ====== HELPERS ======
function ghHeaders(extra?: Record<string, string>) {
  const headers: Record<string, string> = {
    "User-Agent": "keelson-gen-script",
    "Accept": "application/vnd.github+json"
  };
  if (GITHUB_TOKEN) headers.Authorization = `Bearer ${GITHUB_TOKEN}`;
  return { ...headers, ...(extra || {}) };
}

async function ghJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: ghHeaders() });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`GitHub API ${res.status} ${res.statusText} at ${url}\n${txt}`);
  }
  return res.json() as Promise<T>;
}

// Follow 3xx redirects when downloading (GitHub tarballs redirect to codeload)
function downloadToFile(
  url: string,
  dest: string
): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const makeReq = (u: string, depth = 0) => {
      if (depth > 5) return reject(new Error("Too many redirects"));
      const uo = new URL(u);
      const headers: Record<string, string> = {
        "User-Agent": "keelson-gen-script",
      };
      // Only add octet-stream hint for codeload; api.github.com should stay default
      if (uo.hostname === "codeload.github.com") {
        headers["Accept"] = "application/octet-stream";
      }
      if (process.env.GITHUB_TOKEN) {
        headers["Authorization"] = `Bearer ${process.env.GITHUB_TOKEN}`;
      }

      const req = https.request(
        {
          hostname: uo.hostname,
          path: uo.pathname + (uo.search || ""),
          protocol: uo.protocol,
          method: "GET",
          headers
        },
        (res) => {
          if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
            req.destroy();
            return makeReq(res.headers.location, depth + 1);
          }
          if (res.statusCode && res.statusCode >= 400) {
            let body = "";
            res.on("data", (c) => (body += c.toString()));
            res.on("end", () => reject(new Error(`HTTP ${res.statusCode} for ${u}\n${body}`)));
            return;
          }
          fse.ensureDirSync(path.dirname(dest));
          const file = fs.createWriteStream(dest);
          res.pipe(file);
          file.on("finish", () => file.close(() => resolve()));
          file.on("error", reject);
        }
      );
      req.on("error", reject);
      req.end();
    };
    makeReq(url);
  });
}


async function extractTarGz(tarPath: string, destDir: string): Promise<void> {
  await fse.ensureDir(destDir);
  await tar.x({ file: tarPath, cwd: destDir, gzip: true });
}

async function assertDeps() {
  try {
    const { stdout } = await execa("protoc", ["--version"], { shell: true });
    console.log(`Found protoc: ${stdout}`);
  } catch {
    console.error(
      [
        "✖ protoc not found.",
        "Install it and ensure it's on PATH:",
        "• macOS (brew):   brew install protobuf",
        "• Ubuntu/Debian:  sudo apt-get install -y protobuf-compiler",
        "• Windows (choco): choco install protoc",
        "• Releases:       https://github.com/protocolbuffers/protobuf/releases",
      ].join("\n")
    );
    process.exit(1);
  }

  if (!(await fse.pathExists(TS_PROTO_PLUGIN))) {
    console.error(
      [
        "protoc-gen-ts_proto not found in node_modules.",
        "Run:",
        "  npm i -D ts-proto",
        "",
        "Note: If you previously used 'protoc-gen-ts-proto' wrapper,",
        "ensure your plugin path matches what's actually installed."
      ].join("\n")
    );
    process.exit(1);
  }
  console.log("Found protoc-gen-ts_proto");
}

// ====== MAIN ======
async function run() {
  console.log("Generating code for javascript/typescript from LATEST RELEASE");

  await assertDeps();

  // 0) figure out which release to use
  type Release = {
    tag_name: string;
    tarball_url: string;
    name?: string;
  };
  const relUrl = TAG
    ? `${API_BASE}/releases/tags/${encodeURIComponent(TAG)}`
    : `${API_BASE}/releases/latest`;

  console.log(`Fetching release metadata: ${relUrl}`);
  const release = await ghJson<Release>(relUrl);
  const tagName = release.tag_name;
  const tarballUrl = release.tarball_url;
  console.log(`Using release: ${tagName}${release.name ? ` (${release.name})` : ""}`);
  // NOTE: tarball_url is an API endpoint that redirects to codeload; we must:
  //  - send User-Agent + (optional) Authorization
  //  - Accept: application/octet-stream
  //  - follow redirects (handled in downloadToFile)

  // 1) temp workspace
  const tmp = await mkdtemp(path.join(os.tmpdir(), "keelson-gen-"));

  // 2) download tarball
  const tarball = path.join(tmp, `repo-${tagName}.tar.gz`);
  console.log("  Downloading release tarball…");
  await downloadToFile(tarballUrl, tarball);

  // 3) extract
  console.log("  Extracting tarball…");
  const extractDir = path.join(tmp, "repo");
  await extractTarGz(tarball, extractDir);
  const [top] = await fse.readdir(extractDir);
  const repoRoot = path.join(extractDir, top); // <repo>-<sha>

  // 4) clean generated artifacts (preserve core lib)
  console.log("  Cleaning generated artifacts (preserving core lib)...");
  const generatedPaths = [
    OUT_PAYLOADS,
    OUT_INTERFACES,
    path.join(OUT_BASE, "google"),
    path.join(OUT_BASE, "Envelope.ts"),
    path.join(OUT_BASE, "subjects.json"),
  ];
  for (const p of generatedPaths) {
    if (await fse.pathExists(p)) await fse.remove(p);
  }
  await fse.ensureDir(OUT_PAYLOADS);
  await fse.ensureDir(OUT_INTERFACES);

  // 5) subjects.yaml → subjects.json
  const subjectsYamlPath = path.join(repoRoot, "messages", "subjects.yaml");
  console.log("  Converting subjects.yaml → subjects.json…");
  const subjectsYaml = await fse.readFile(subjectsYamlPath, "utf8");
  const subjectsObj = yamlLoad(subjectsYaml);
  await fse.writeJSON(path.join(OUT_BASE, "subjects.json"), subjectsObj, { spaces: 2 });

  // 6) Envelope.proto → src/keelson
  console.log("  Generating code for Envelope.proto…");
  const protoPathMessages = path.join(repoRoot, "messages");
  await execa(
    "protoc",
    [
      `--plugin=${TS_PROTO_PLUGIN}`,
      `--ts_proto_out=${OUT_BASE}`,
      `--proto_path`,
      protoPathMessages,
      path.join(protoPathMessages, "Envelope.proto"),
    ],
    { stdio: "inherit", shell: true }
  );

  // 7) Payloads → src/keelson/payloads
  console.log("  Generating payloads…");
  const payloadsDir = path.join(repoRoot, "messages", "payloads");
  const payloadFiles: string[] = [];
  const walk = async (dir: string) => {
    const entries = await fse.readdir(dir, { withFileTypes: true });
    for (const e of entries) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) await walk(p);
      else if (e.isFile() && p.endsWith(".proto")) payloadFiles.push(p);
    }
  };
  if (await fse.pathExists(payloadsDir)) {
    await walk(payloadsDir);
  }
  if (payloadFiles.length > 0) {
    await execa(
      "protoc",
      [
        `--plugin=${TS_PROTO_PLUGIN}`,
        `--ts_proto_out=${OUT_PAYLOADS}`,
        `--proto_path=${payloadsDir}`,
        `--ts_proto_opt=esModuleInterop=true`,
        `--ts_proto_opt=outputIndex=true`,
        `--ts_proto_opt=outputTypeRegistry=true`,
        ...payloadFiles,
      ],
      { stdio: "inherit", shell: true }
    );
  } else {
    console.warn("  No payload .proto files found.");
  }

  // 8) Interfaces → src/keelson/interfaces
  console.log("  Generating interfaces…");
  const interfacesDir = path.join(repoRoot, "interfaces");
  const ifaceFiles =
    (await fse.pathExists(interfacesDir))
      ? (await fse.readdir(interfacesDir))
          .filter((f) => f.endsWith(".proto"))
          .map((f) => path.join(interfacesDir, f))
      : [];

  if (ifaceFiles.length > 0) {
    await execa(
      "protoc",
      [
        `--plugin=${TS_PROTO_PLUGIN}`,
        `--ts_proto_out=${OUT_INTERFACES}`,
        `--proto_path=${interfacesDir}`,
        ...ifaceFiles,
      ],
      { stdio: "inherit", shell: true }
    );
  } else {
    console.warn("  No interface .proto files found.");
  }

  console.log(`javascript/typescript done! Built on Keelson: ${tagName}${release.name ? ` (${release.name})` : ""}`);
}

run().catch((err) => {
  console.error(err);
  process.exit(1);
});
