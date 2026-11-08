// One typeface everywhere (Sam, 6 Nov 2026). Older sections were written with a
// mix of Manrope, DM Sans, Trebuchet MS, Georgia and Consolas, none of which
// are loaded, so each computer picked its own fallback. At build time every
// font list is pointed at the ERP's own font (--font-ui), and code-style text
// at one monospace font (--font-mono). The Tivoli "T" monogram keeps its serif.
const MONO = /(^|[\s,"'])(DM Mono|Consolas|Courier New|ui-monospace|SFMono-Regular|Menlo|monospace)\b/i;
const KNOWN = /(^|[\s,"'])(Manrope|DM Sans|Trebuchet MS|Georgia|Times New Roman|Segoe UI|Aptos|Arial|Helvetica|system-ui|sans-serif|serif|DM Mono|Consolas|Courier New|ui-monospace|monospace)\b/i;
// A font list at the end of a value: names, quoted names and commas.
const TRAILING_LIST = /(^|\s)((?:"[^"]+"|'[^']+'|[A-Za-z][\w-]*)(?:\s*,\s*(?:"[^"]+"|'[^']+'|[A-Za-z][\w-]*))*)\s*$/;

export function oneFont(value) {
  const match = value.match(TRAILING_LIST);
  if (!match || !KNOWN.test(match[2])) return value;
  const family = MONO.test(match[2]) ? "var(--font-mono)" : "var(--font-ui)";
  return value.slice(0, match.index) + match[1] + family;
}

export default function oneFontPlugin() {
  const done = new WeakSet();
  const visit = (decl) => {
    if (done.has(decl)) return;
    done.add(decl);
    const selector = decl.parent?.selector ?? "";
    if (/monogram/.test(selector)) return;
    decl.value = oneFont(decl.value);
  };
  return { postcssPlugin: "tivoli-one-font", Declaration: { "font-family": visit, font: visit } };
}
oneFontPlugin.postcss = true;
