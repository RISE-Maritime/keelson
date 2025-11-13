const result = await Deno.emit("./keelson/index.ts", {
  bundle: "none",
});
await Deno.mkdir("dist", { recursive: true });
await Deno.writeTextFile("dist/index.js", result.files["file:///keelson/index.js"]);
