// Test adapter for the already-installed, local packing-list workbook reader.
// Reads bytes from stdin; no files, network, persistence or external tools.
import { JSDOM } from "jsdom";
import { parsePackingListFile } from "../src/packing-list-parser.ts";

globalThis.DOMParser = new JSDOM().window.DOMParser;
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const bytes = Buffer.concat(chunks);
const workbook = await parsePackingListFile({
  name: "synthetic-report.xlsx",
  size: bytes.byteLength,
  arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
});
console.log(JSON.stringify(workbook));
