// Build-graph sizes, not timing or measured wire transfer. No dependencies.
import { readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { gzipSync } from "node:zlib";
import assert from "node:assert/strict";

const [beforeDir, afterDir, output] = process.argv.slice(2);
if (!beforeDir || !afterDir || !output) throw new Error("Supply before/after build directories and generated JSON output.");
async function inspect(directory) {
  const root = resolve(directory);
  const manifest = JSON.parse(await readFile(resolve(root, ".vite/manifest.json"), "utf8"));
  const entry = Object.keys(manifest).find((key) => manifest[key].isEntry);
  assert.ok(entry);
  function closure(key, files = new Set(), seen = new Set()) {
    if (seen.has(key)) return files;
    seen.add(key);
    const chunk = manifest[key];
    assert.ok(chunk, `Missing manifest dependency ${key}`);
    files.add(chunk.file);
    for (const css of chunk.css ?? []) files.add(css);
    for (const dependency of chunk.imports ?? []) closure(dependency, files, seen);
    return files;
  }
  async function sizes(files) {
    const totals = { jsBytes: 0, cssBytes: 0, gzipJsBytes: 0, gzipCssBytes: 0 };
    for (const file of files) {
      assert.ok(!file.includes("..") && !file.startsWith("/"));
      const data = await readFile(resolve(root, file));
      if (file.endsWith(".js")) { totals.jsBytes += data.length; totals.gzipJsBytes += gzipSync(data).length; }
      if (file.endsWith(".css")) { totals.cssBytes += data.length; totals.gzipCssBytes += gzipSync(data).length; }
    }
    return totals;
  }
  const initial = closure(entry);
  const dynamic = Object.keys(manifest).filter((key) => manifest[key].isDynamicEntry);
  const editors = {};
  for (const key of dynamic) {
    assert.ok(!initial.has(manifest[key].file), `Editor eagerly included: ${key}`);
    const all = closure(key);
    editors[key] = {
      incremental: await sizes(new Set([...all].filter((file) => !initial.has(file)))),
      initialPlusEditor: await sizes(new Set([...initial, ...all])),
      files: [...all].sort(),
    };
  }
  const all = new Set();
  for (const key of Object.keys(manifest)) closure(key, all);
  return { initial: await sizes(initial), initialFiles: [...initial].sort(),
    allAssets: await sizes(all), editors };
}
const before = await inspect(beforeDir), after = await inspect(afterDir);
assert.equal(Object.keys(before.editors).length, 0);
assert.equal(Object.keys(after.editors).length, 15); //14 actual destinations + unchanged offline sample.
const result = { method: "Vite static dependency closure; deduplicated JS/CSS. gzip calculated per emitted file, not measured wire bytes. No speed claim.",
  before, after };
await writeFile(output, JSON.stringify(result, null, 2) + "\n");
console.log(JSON.stringify({ before: before.initial, after: after.initial,
  editorIncrementalJsBytes: Object.fromEntries(Object.entries(after.editors).map(([key, value]) => [key, value.incremental.jsBytes])),
  allAssetsBefore: before.allAssets, allAssetsAfter: after.allAssets }, null, 2));
