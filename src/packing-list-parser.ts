export type PackingSheet = {
  name: string;
  headers: string[];
  rows: string[][];
};

export type PackingWorkbook = {
  sheets: PackingSheet[];
};

export type PackingField =
  | "itemCode"
  | "description"
  | "quantity"
  | "unit"
  | "lineReference"
  | "packageDimensions"
  | "sourceVolumeM3"
  | "sourceAreaM2";

export type ColumnMapping = Record<PackingField, number | null>;

export type PackingPreviewRow = {
  sourceRow: number;
  itemCode: string;
  description: string;
  quantity: string;
  unit: string;
  lineReference: string;
  packageDimensions: string;
  sourceVolumeM3: string;
  sourceAreaM2: string;
  errors: string[];
};

export const emptyColumnMapping: ColumnMapping = {
  itemCode: null,
  description: null,
  quantity: null,
  unit: null,
  lineReference: null,
  packageDimensions: null,
  sourceVolumeM3: null,
  sourceAreaM2: null,
};

const MAX_FILE_BYTES = 10 * 1024 * 1024;
const MAX_UNCOMPRESSED_BYTES = 24 * 1024 * 1024;
const MAX_WORKSHEET_ROWS = 10_000;
const XML_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";

function decimalParts(value: string): { whole: string; fraction: string } | null {
  const normalized = value.trim();
  if (normalized.length > 64 || !/^(?:\d+(?:\.\d*)?|\.\d+)$/.test(normalized)) return null;
  const [whole = "0", fraction = ""] = normalized.split(".");
  return { whole: whole || "0", fraction };
}

export function isNonNegativeDecimal(value: string): boolean {
  return decimalParts(value) !== null;
}

export function isPositiveDecimal(value: string): boolean {
  const parts = decimalParts(value);
  return parts !== null && (BigInt(parts.whole || "0") !== 0n || /[1-9]/.test(parts.fraction));
}

export function sumDecimalStrings(values: string[]): string {
  const parsed = values.map(decimalParts).filter(
    (value): value is { whole: string; fraction: string } => value !== null,
  );
  if (!parsed.length) return "0";
  const scale = Math.max(...parsed.map(({ fraction }) => fraction.length));
  const factor = 10n ** BigInt(scale);
  const total = parsed.reduce((sum, { whole, fraction }) => {
    const padded = fraction.padEnd(scale, "0");
    return sum + BigInt(whole || "0") * factor + BigInt(padded || "0");
  }, 0n);
  const sign = total < 0n ? "-" : "";
  const absolute = total < 0n ? -total : total;
  if (scale === 0) return `${sign}${absolute}`;
  const integer = absolute / factor;
  const fractional = (absolute % factor).toString().padStart(scale, "0").replace(/0+$/, "");
  return fractional ? `${sign}${integer}.${fractional}` : `${sign}${integer}`;
}

function parseCsv(text: string, name: string): PackingSheet {
  const input = text.replace(/^\uFEFF/, "");
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let quoted = false;
  for (let index = 0; index < input.length; index += 1) {
    const character = input[index];
    if (quoted) {
      if (character === '"') {
        if (input[index + 1] === '"') {
          cell += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        cell += character;
      }
      continue;
    }
    if (character === '"' && cell.length === 0) {
      quoted = true;
    } else if (character === ",") {
      row.push(cell);
      cell = "";
    } else if (character === "\n" || character === "\r") {
      row.push(cell);
      if (row.some((value) => value.trim() !== "")) rows.push(row);
      row = [];
      cell = "";
      if (character === "\r" && input[index + 1] === "\n") index += 1;
    } else {
      cell += character;
    }
  }
  if (quoted) throw new Error("CSV contains an unterminated quoted field.");
  row.push(cell);
  if (row.some((value) => value.trim() !== "")) rows.push(row);
  if (!rows.length) throw new Error("The selected file is empty.");
  if (rows.length - 1 > MAX_WORKSHEET_ROWS) {
    throw new Error(`The preview is limited to ${MAX_WORKSHEET_ROWS.toLocaleString()} data rows.`);
  }
  return tableToSheet(name, rows);
}

function tableToSheet(name: string, rows: string[][]): PackingSheet {
  const width = Math.max(0, ...rows.map((row) => row.length));
  const headers = Array.from({ length: width }, (_, index) => {
    const label = rows[0]?.[index]?.trim();
    return label || `Column ${index + 1}`;
  });
  const dataRows = rows.slice(1)
    .filter((row) => row.some((value) => value.trim() !== ""))
    .map((row) => Array.from({ length: width }, (_, index) => row[index] ?? ""));
  return { name, headers, rows: dataRows };
}

function parseXml(source: string, label: string): Document {
  const document = new DOMParser().parseFromString(source, "application/xml");
  if (document.getElementsByTagName("parsererror").length) {
    throw new Error(`The Excel workbook has malformed ${label}.`);
  }
  return document;
}

function descendants(node: ParentNode, name: string): Element[] {
  const xmlNode = node as Document | Element;
  return Array.from(xmlNode.getElementsByTagNameNS("*", name));
}

function findZipEnd(bytes: Uint8Array): number {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const minimum = Math.max(0, bytes.length - 65_557);
  for (let offset = bytes.length - 22; offset >= minimum; offset -= 1) {
    if (view.getUint32(offset, true) === 0x06054b50) return offset;
  }
  throw new Error("The Excel workbook is not a valid .xlsx archive.");
}

function crc32(bytes: Uint8Array): number {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
  }
  return (crc ^ 0xffffffff) >>> 0;
}

async function inflateRaw(data: Uint8Array): Promise<Uint8Array> {
  if (typeof DecompressionStream === "undefined") {
    throw new Error("This browser does not support local .xlsx preview.");
  }
  try {
    const buffer = new ArrayBuffer(data.byteLength);
    new Uint8Array(buffer).set(data);
    const stream = new Blob([buffer]).stream().pipeThrough(
      new DecompressionStream("deflate-raw" as CompressionFormat),
    );
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch {
    throw new Error("The .xlsx workbook uses an unsupported or corrupt compression stream.");
  }
}

async function readZipEntries(bytes: Uint8Array): Promise<Map<string, Uint8Array>> {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const end = findZipEnd(bytes);
  const entryCount = view.getUint16(end + 10, true);
  const centralOffset = view.getUint32(end + 16, true);
  const decoder = new TextDecoder("utf-8");
  const entries = new Map<string, Uint8Array>();
  let cursor = centralOffset;
  let totalUncompressed = 0;

  if (entryCount > 1_000) throw new Error("The Excel workbook contains too many archive entries.");
  for (let index = 0; index < entryCount; index += 1) {
    if (cursor + 46 > bytes.length || view.getUint32(cursor, true) !== 0x02014b50) {
      throw new Error("The Excel workbook archive directory is malformed.");
    }
    const flags = view.getUint16(cursor + 8, true);
    const method = view.getUint16(cursor + 10, true);
    const expectedCrc = view.getUint32(cursor + 16, true);
    const compressedSize = view.getUint32(cursor + 20, true);
    const uncompressedSize = view.getUint32(cursor + 24, true);
    const nameLength = view.getUint16(cursor + 28, true);
    const extraLength = view.getUint16(cursor + 30, true);
    const commentLength = view.getUint16(cursor + 32, true);
    const localOffset = view.getUint32(cursor + 42, true);
    const nameStart = cursor + 46;
    const nameEnd = nameStart + nameLength;
    const name = decoder.decode(bytes.subarray(nameStart, nameEnd));
    cursor = nameEnd + extraLength + commentLength;
    if (flags & 1) throw new Error("Encrypted Excel workbooks cannot be previewed.");
    totalUncompressed += uncompressedSize;
    if (totalUncompressed > MAX_UNCOMPRESSED_BYTES) {
      throw new Error("The expanded workbook is too large for a local preview.");
    }
    if (localOffset + 30 > bytes.length || view.getUint32(localOffset, true) !== 0x04034b50) {
      throw new Error("The Excel workbook contains a malformed archive entry.");
    }
    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    const dataStart = localOffset + 30 + localNameLength + localExtraLength;
    const dataEnd = dataStart + compressedSize;
    if (dataEnd > bytes.length) throw new Error("The Excel workbook archive is truncated.");
    const compressed = bytes.subarray(dataStart, dataEnd);
    let content: Uint8Array;
    if (method === 0) content = compressed.slice();
    else if (method === 8) content = await inflateRaw(compressed);
    else throw new Error("The Excel workbook uses an unsupported compression method.");
    if (content.length !== uncompressedSize) {
      throw new Error("The Excel workbook contains an invalid archive entry size.");
    }
    if (crc32(content) !== expectedCrc) {
      throw new Error("The Excel workbook contains a corrupt archive entry.");
    }
    entries.set(name, content);
  }
  return entries;
}

function columnIndex(reference: string): number | null {
  const letters = reference.match(/^([A-Z]+)/i)?.[1]?.toUpperCase();
  if (!letters) return null;
  let result = 0;
  for (const letter of letters) {
    result = result * 26 + letter.charCodeAt(0) - 64;
    if (result > 256) {
      throw new Error("The preview is limited to 256 columns per worksheet.");
    }
  }
  return result - 1;
}

function resolveZipPath(basePath: string, target: string): string {
  const parts = target.startsWith("/") ? [] : basePath.split("/").slice(0, -1);
  for (const part of target.replace(/^\/+/, "").split("/")) {
    if (!part || part === ".") continue;
    if (part === "..") parts.pop();
    else parts.push(part);
  }
  return parts.join("/");
}

function parseXlsxSheet(
  xml: string,
  name: string,
  sharedStrings: string[],
): PackingSheet {
  const document = parseXml(xml, "worksheet XML");
  const table: string[][] = [];
  for (const rowElement of descendants(document, "row")) {
    const row = new Map<number, string>();
    for (const cellElement of descendants(rowElement, "c")) {
      const position = columnIndex(cellElement.getAttribute("r") ?? "");
      if (position === null) continue;
      if (descendants(cellElement, "f").length) {
        row.set(position, "");
        continue;
      }
      const type = cellElement.getAttribute("t");
      let value = "";
      if (type === "inlineStr") {
        value = descendants(cellElement, "t").map((node) => node.textContent ?? "").join("");
      } else {
        value = descendants(cellElement, "v")[0]?.textContent ?? "";
        if (type === "s") {
          const sharedIndex = Number(value);
          value = Number.isInteger(sharedIndex) ? sharedStrings[sharedIndex] ?? "" : "";
        }
      }
      row.set(position, value);
    }
    const width = Math.max(0, ...row.keys()) + 1;
    const values = Array.from({ length: width }, (_, index) => row.get(index) ?? "");
    if (values.some((value) => value.trim() !== "")) table.push(values);
    if (table.length > MAX_WORKSHEET_ROWS + 1) {
      throw new Error(`The preview is limited to ${MAX_WORKSHEET_ROWS.toLocaleString()} data rows.`);
    }
  }
  if (!table.length) return { name, headers: [], rows: [] };
  return tableToSheet(name, table);
}

async function parseXlsx(bytes: Uint8Array): Promise<PackingWorkbook> {
  const entries = await readZipEntries(bytes);
  const decoder = new TextDecoder("utf-8", { fatal: true });
  const workbookXml = entries.get("xl/workbook.xml");
  const relationshipsXml = entries.get("xl/_rels/workbook.xml.rels");
  if (!workbookXml || !relationshipsXml) {
    throw new Error("The .xlsx file is missing workbook metadata.");
  }
  const workbook = parseXml(decoder.decode(workbookXml), "workbook metadata");
  const relationships = parseXml(decoder.decode(relationshipsXml), "workbook relationships");
  const relationshipTargets = new Map(
    descendants(relationships, "Relationship").map((relationship) => [
      relationship.getAttribute("Id") ?? "",
      relationship.getAttribute("Target") ?? "",
    ]),
  );
  const sharedStringsXml = entries.get("xl/sharedStrings.xml");
  const sharedStrings = sharedStringsXml
    ? descendants(parseXml(decoder.decode(sharedStringsXml), "shared strings"), "si")
      .map((item) => descendants(item, "t").map((node) => node.textContent ?? "").join(""))
    : [];
  const sheets: PackingSheet[] = [];
  for (const sheetElement of descendants(workbook, "sheet")) {
    const relationshipId = sheetElement.getAttributeNS(XML_REL_NS, "id")
      ?? sheetElement.getAttribute("r:id")
      ?? "";
    const target = relationshipTargets.get(relationshipId);
    if (!target) continue;
    const entryPath = resolveZipPath("xl/workbook.xml", target);
    const sheetBytes = entries.get(entryPath);
    if (!sheetBytes) continue;
    const sheetName = sheetElement.getAttribute("name") || `Sheet ${sheets.length + 1}`;
    sheets.push(parseXlsxSheet(decoder.decode(sheetBytes), sheetName, sharedStrings));
  }
  if (!sheets.length) throw new Error("The .xlsx workbook has no readable worksheets.");
  return { sheets };
}

export async function parsePackingListFile(
  file: Pick<File, "name" | "size" | "arrayBuffer">,
): Promise<PackingWorkbook> {
  if (file.size > MAX_FILE_BYTES) {
    throw new Error("The selected file exceeds the 10 MiB local-preview limit.");
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  const lowerName = file.name.toLowerCase();
  if (lowerName.endsWith(".csv")) {
    let text: string;
    try {
      text = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch {
      throw new Error("CSV must use valid UTF-8 text.");
    }
    return { sheets: [parseCsv(text, file.name)] };
  }
  if (lowerName.endsWith(".xlsx")) return parseXlsx(bytes);
  throw new Error("Choose a CSV or .xlsx workbook. Legacy .xls files are not supported.");
}

function mappedValue(row: string[], column: number | null): string {
  return column === null ? "" : (row[column] ?? "").trim();
}

export function mapPackingRows(
  sheet: PackingSheet,
  mapping: ColumnMapping,
): PackingPreviewRow[] {
  return sheet.rows.map((row, index) => {
    const itemCode = mappedValue(row, mapping.itemCode);
    const description = mappedValue(row, mapping.description);
    const quantity = mappedValue(row, mapping.quantity);
    const unit = mappedValue(row, mapping.unit);
    const lineReference = mappedValue(row, mapping.lineReference);
    const packageDimensions = mappedValue(row, mapping.packageDimensions);
    const sourceVolumeM3 = mappedValue(row, mapping.sourceVolumeM3);
    const sourceAreaM2 = mappedValue(row, mapping.sourceAreaM2);
    const errors: string[] = [];
    if (mapping.itemCode === null) errors.push("Map an item-code column.");
    else if (!itemCode) errors.push("Item code is required.");
    if (mapping.quantity === null) errors.push("Map a quantity column.");
    else if (!isPositiveDecimal(quantity)) errors.push("Quantity must be a positive decimal.");
    if (mapping.unit === null) errors.push("Map a unit column.");
    else if (!unit) errors.push("Unit is required.");
    if (sourceVolumeM3 && !isNonNegativeDecimal(sourceVolumeM3)) {
      errors.push("Supplier volume must be a non-negative decimal in m³.");
    }
    if (sourceAreaM2 && !isNonNegativeDecimal(sourceAreaM2)) {
      errors.push("Supplier area must be a non-negative decimal in m².");
    }
    return {
      sourceRow: index + 2,
      itemCode,
      description,
      quantity,
      unit,
      lineReference,
      packageDimensions,
      sourceVolumeM3,
      sourceAreaM2,
      errors,
    };
  });
}
