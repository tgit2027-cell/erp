/* Code 128 barcodes drawn as SVG, so labels print sharply on any printer. */

// Code 128 bar patterns for values 0 to 105 (1 is a bar module, 0 a space). Standard table.
const PATTERNS = [
  "11011001100", "11001101100", "11001100110", "10010011000", "10010001100", "10001001100", "10011001000", "10011000100",
  "10001100100", "11001001000", "11001000100", "11000100100", "10110011100", "10011011100", "10011001110", "10111001100",
  "10011101100", "10011100110", "11001110010", "11001011100", "11001001110", "11011100100", "11001110100", "11101101110",
  "11101001100", "11100101100", "11100100110", "11101100100", "11100110100", "11100110010", "11011011000", "11011000110",
  "11000110110", "10100011000", "10001011000", "10001000110", "10110001000", "10001101000", "10001100010", "11010001000",
  "11000101000", "11000100010", "10110111000", "10110001110", "10001101110", "10111011000", "10111000110", "10001110110",
  "11101110110", "11010001110", "11000101110", "11011101000", "11011100010", "11011101110", "11101011000", "11101000110",
  "11100010110", "11101101000", "11101100010", "11100011010", "11101111010", "11001000010", "11110001010", "10100110000",
  "10100001100", "10010110000", "10010000110", "10000101100", "10000100110", "10110010000", "10110000100", "10011010000",
  "10011000010", "10000110100", "10000110010", "11000010010", "11001010000", "11110111010", "11000010100", "10001111010",
  "10100111100", "10010111100", "10010011110", "10111100100", "10011110100", "10011110010", "11110100100", "11110010100",
  "11110010010", "11011011110", "11011110110", "11110110110", "10101111000", "10100011110", "10001011110", "10111101000",
  "10111100010", "11110101000", "11110100010", "10111011110", "10111101110", "11101011110", "11110101110", "11010000100",
  "11010010000", "11010011100",
];
const STOP = "1100011101011";

const START_B = 104;
const START_C = 105;

/** The bar and space modules for a value, using code set C for all-digit values of even length and B otherwise. */
export function code128Modules(value: string): string {
  if (!value) throw new Error("Nothing to encode.");
  const codes: number[] = [];
  if (/^\d+$/.test(value) && value.length % 2 === 0 && value.length >= 4) {
    codes.push(START_C);
    for (let index = 0; index < value.length; index += 2) codes.push(Number(value.slice(index, index + 2)));
  } else {
    codes.push(START_B);
    for (const char of value) {
      const code = char.charCodeAt(0) - 32;
      if (code < 0 || code > 95) throw new Error(`“${char}” cannot be printed in a barcode.`);
      codes.push(code);
    }
  }
  const check = codes.reduce((sum, code, index) => sum + code * (index === 0 ? 1 : index), 0) % 103;
  return [...codes, check].map((code) => PATTERNS[code]).join("") + STOP;
}

export function canEncode(value: string): boolean {
  return !!value && [...value].every((char) => char.charCodeAt(0) >= 32 && char.charCodeAt(0) <= 127);
}

/** An SVG of the barcode with a quiet zone of 10 modules on each side. */
export function Barcode({ value, height = 40, label }: { value: string; height?: number; label?: string }) {
  const modules = code128Modules(value);
  const quiet = 10;
  const bars: { x: number; width: number }[] = [];
  for (let index = 0; index < modules.length; index += 1) {
    if (modules[index] !== "1") continue;
    const last = bars[bars.length - 1];
    if (last && last.x + last.width === index + quiet) last.width += 1;
    else bars.push({ x: index + quiet, width: 1 });
  }
  const width = modules.length + quiet * 2;
  return <svg className="barcode" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" role="img" aria-label={label ?? `Barcode ${value}`} data-value={value}>
    <rect width={width} height={height} fill="#fff" />
    {bars.map((bar) => <rect key={bar.x} x={bar.x} width={bar.width} height={height} fill="#000" />)}
  </svg>;
}
