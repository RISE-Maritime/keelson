import { transpile } from "https://deno.land/x/emit/mod.ts";

const result = await transpile("./keelson/index.ts");
await Deno.mkdir("dist", { recursive: true });
await Deno.writeTextFile(
  "dist/index.js",
  result["keelson/index.ts.js"],
);
