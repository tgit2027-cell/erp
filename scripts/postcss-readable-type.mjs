// Lifts very small font sizes (written for a dense prototype) to a readable
// size at build time, keeping their relative order. 15px and above are left
// alone. Used by vite.config.ts for every stylesheet, so new CSS stays readable
// too. Mapping: 8px→11.5, 9px→12, 10px→12.5, 11px→13, 12px→13.5, 14px→14.5.
const PX = /(^|[\s/(,])(\d+(?:\.\d+)?)px\b/g;

export function readableSize(px) {
  if (px >= 15) return px;
  return Math.max(px, Math.round((px * 0.5 + 7.5) * 2) / 2);
}

function liftSizes(value) {
  return value.replace(PX, (match, lead, number) => `${lead}${readableSize(Number(number))}px`);
}

function liftFontShorthand(value) {
  // In `font:` the size is the token right before the family (optionally
  // followed by /line-height). Only that first px token is touched.
  let done = false;
  return value.replace(/(^|\s)(\d+(?:\.\d+)?)px(?=(\/|\s))/g, (match, lead, number) => {
    if (done) return match;
    done = true;
    return `${lead}${readableSize(Number(number))}px`;
  });
}

export default function readableType() {
  // PostCSS revisits a declaration after it changes, so remember the ones
  // already lifted to avoid scaling them twice.
  const lifted = new WeakSet();
  const once = (lift) => (decl) => {
    if (lifted.has(decl)) return;
    lifted.add(decl);
    decl.value = lift(decl.value);
  };
  return {
    postcssPlugin: "tivoli-readable-type",
    Declaration: {
      "font-size": once(liftSizes),
      font: once(liftFontShorthand),
    },
  };
}
readableType.postcss = true;
