import assert from "node:assert/strict";
import { test } from "node:test";
import { zipArchive } from "./synthetic-xlsx-fixture.mjs";
import { JSDOM } from "jsdom";
import {
  emptyColumnMapping,
  isNonNegativeDecimal,
  isPositiveDecimal,
  mapPackingRows,
  parsePackingListFile,
  sumDecimalStrings,
} from "../src/packing-list-parser.ts";

const encoder = new TextEncoder();
globalThis.DOMParser = new JSDOM().window.DOMParser;

function fakeFile(name, content) {
  const bytes = typeof content === "string" ? encoder.encode(content) : content;
  return {
    name,
    size: bytes.byteLength,
    arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
  };
}

function workbookFixture(extraHeaderCell = "") {
  const strings = [
    "Supplier code", "Description", "Quantity", "Unit", "Line ref",
    "Package dimensions", "Supplier volume m3", "Supplier area m2",
    "CHAIR-X", "TEST ONLY chair", "EA", "LINE-9", "35 x 40 x 50 cm",
  ];
  const sharedStrings = `<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="${strings.length}" uniqueCount="${strings.length}">${strings.map((value) => `<si><t>${value}</t></si>`).join("")}</sst>`;
  const row = (rowNumber, values) => `<row r="${rowNumber}">${values.map((value, index) => {
    const column = String.fromCharCode(65 + index);
    if (typeof value === "number") return `<c r="${column}${rowNumber}"><v>${value}</v></c>`;
    return `<c r="${column}${rowNumber}" t="s"><v>${value}</v></c>`;
  }).join("")}</row>`;
  const header = row(1, ["0", "1", "2", "3", "4", "5", "6", "7"])
    .replace("</row>", `${extraHeaderCell}</row>`);
  const sheet = `<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>${header}${row(2, ["8", "9", 2, "10", "11", "12", 0.075, 1.25])}</sheetData></worksheet>`;
  return zipArchive({
    "xl/workbook.xml": `<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="TEST ONLY Imports" sheetId="1" r:id="rId1"/></sheets></workbook>`,
    "xl/_rels/workbook.xml.rels": `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="worksheet"/></Relationships>`,
    "xl/sharedStrings.xml": sharedStrings,
    "xl/worksheets/sheet1.xml": sheet,
  }, 8);
}

test("CSV parsing handles quoted commas, escaped quotes, and line breaks", async () => {
  const workbook = await parsePackingListFile(fakeFile(
    "synthetic-pack.csv",
    'Item,Description,Qty,Unit,Reference\r\nCHAIR-1,"TEST ONLY, chair",2,EA,"line ""A"""\r\n',
  ));
  assert.equal(workbook.sheets[0].name, "synthetic-pack.csv");
  assert.deepEqual(workbook.sheets[0].headers, ["Item", "Description", "Qty", "Unit", "Reference"]);
  assert.deepEqual(workbook.sheets[0].rows[0], ["CHAIR-1", "TEST ONLY, chair", "2", "EA", 'line "A"']);
});

test("XLSX workbook parsing reads shared strings, numbers, and worksheet names locally", async () => {
  const workbook = await parsePackingListFile(fakeFile("synthetic-pack.xlsx", workbookFixture()));
  assert.equal(workbook.sheets.length, 1);
  assert.equal(workbook.sheets[0].name, "TEST ONLY Imports");
  assert.deepEqual(workbook.sheets[0].headers, [
    "Supplier code", "Description", "Quantity", "Unit", "Line ref",
    "Package dimensions", "Supplier volume m3", "Supplier area m2",
  ]);
  assert.deepEqual(workbook.sheets[0].rows[0], [
    "CHAIR-X", "TEST ONLY chair", "2", "EA", "LINE-9",
    "35 x 40 x 50 cm", "0.075", "1.25",
  ]);
});

test("empty, malformed, and unsupported files fail with a visible explanation", async () => {
  await assert.rejects(parsePackingListFile(fakeFile("empty.csv", "")), /file is empty/i);
  await assert.rejects(
    parsePackingListFile(fakeFile("bad.csv", 'Item,Qty\n"unclosed,2')),
    /unterminated quoted field/i,
  );
  await assert.rejects(parsePackingListFile(fakeFile("old.xls", "not a workbook")), /\.xlsx workbook/i);
  await assert.rejects(parsePackingListFile(fakeFile("bad.xlsx", "not zip")), /valid \.xlsx archive/i);
  await assert.rejects(
    parsePackingListFile(fakeFile(
      "wide.xlsx",
      workbookFixture('<c r="IW1" t="inlineStr"><is><t>Beyond limit</t></is></c>'),
    )),
    /limited to 256 columns/i,
  );
});

test("column mapping is explicit and remaps supplier fields without template assumptions", async () => {
  const workbook = await parsePackingListFile(fakeFile(
    "mapped.csv",
    "Reference,Units,Part,Text,Count\nLINE-7,EA,SOFA-7,TEST ONLY sofa,1.5\n",
  ));
  const mapping = {
    ...emptyColumnMapping,
    itemCode: 2,
    description: 3,
    quantity: 4,
    unit: 1,
    lineReference: 0,
  };
  assert.deepEqual(mapPackingRows(workbook.sheets[0], mapping), [{
    sourceRow: 2,
    itemCode: "SOFA-7",
    description: "TEST ONLY sofa",
    quantity: "1.5",
    unit: "EA",
    lineReference: "LINE-7",
    packageDimensions: "",
    sourceVolumeM3: "",
    sourceAreaM2: "",
    errors: [],
  }]);
});

test("invalid quantities and optional area/volume values are flagged without unit conversion", async () => {
  const workbook = await parsePackingListFile(fakeFile(
    "invalid.csv",
    "Code,Qty,Unit,Volume m3,Area m2,Dimensions\nCHAIR-1,0,EA,not-a-number,2.4,raw dimensions\nCHAIR-2,2,EA,0.3,1.1,another raw value\n",
  ));
  const mapping = {
    ...emptyColumnMapping,
    itemCode: 0,
    quantity: 1,
    unit: 2,
    sourceVolumeM3: 3,
    sourceAreaM2: 4,
    packageDimensions: 5,
  };
  const rows = mapPackingRows(workbook.sheets[0], mapping);
  assert.ok(rows[0].errors.some((error) => /positive decimal/.test(error)));
  assert.ok(rows[0].errors.some((error) => /volume.*m³/.test(error)));
  assert.equal(rows[0].sourceAreaM2, "2.4");
  assert.equal(rows[0].packageDimensions, "raw dimensions");
  assert.deepEqual([rows[1].sourceVolumeM3, rows[1].sourceAreaM2], ["0.3", "1.1"]);
  assert.equal(sumDecimalStrings(rows.map((row) => row.sourceVolumeM3)), "0.3");
  assert.equal(sumDecimalStrings(rows.map((row) => row.sourceAreaM2)), "3.5");
});

test("decimal checks and summaries remain exact and keep m2 separate from m3", () => {
  assert.equal(isPositiveDecimal(".25"), true);
  assert.equal(isPositiveDecimal("0"), false);
  assert.equal(isPositiveDecimal("-1"), false);
  assert.equal(isNonNegativeDecimal("0.0001"), true);
  assert.equal(isNonNegativeDecimal("-0.1"), false);
  assert.equal(sumDecimalStrings(["0.10000000000000001", "0.2"]), "0.30000000000000001");
  assert.equal(sumDecimalStrings(["1.2", "2.35"]), "3.55");
});
