const HEX_COLOR_PATTERN = /^#[\da-fA-F]{6}$/;

function mixHex(color, target, amount) {
  if (!HEX_COLOR_PATTERN.test(color)) {
    throw new Error("Expected a six-digit hex color.");
  }
  const channels = color.slice(1).match(/.{2}/g).map((part) => Number.parseInt(part, 16));
  return `#${channels.map((channel) => {
    const mixed = Math.floor(channel * (1 - amount) + target * amount + 0.5);
    return mixed.toString(16).padStart(2, "0");
  }).join("")}`.toUpperCase();
}

function luminance(hex) {
  const channels = hex.slice(1).match(/.{2}/g).map((part) => Number.parseInt(part, 16) / 255);
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

export function buildPaletteColors(accent) {
  if (!HEX_COLOR_PATTERN.test(accent)) {
    throw new Error("Enter a six-digit hex color such as #B24F00.");
  }
  const normalized = accent.toUpperCase();
  return {
    "--brand": normalized,
    "--green": normalized,
    "--brand-deep": mixHex(normalized, 0, 0.2),
    "--brand-mid": mixHex(normalized, 0, 0.15),
    "--brand-soft": mixHex(normalized, 255, 0.9),
    "--sidebar": mixHex(normalized, 0, 0.74),
    "--sidebar-muted": mixHex(normalized, 255, 0.74),
    "--focus": normalized,
    "--line": mixHex(normalized, 255, 0.82),
    "--sand": mixHex(normalized, 255, 0.97),
  };
}

export function contrastRatio(first, second) {
  const firstLuminance = luminance(first);
  const secondLuminance = luminance(second);
  return (Math.max(firstLuminance, secondLuminance) + 0.05)
    / (Math.min(firstLuminance, secondLuminance) + 0.05);
}

export function customPaletteContrastIssues(colors) {
  if (!colors) return ["Enter a six-digit hex color such as #B24F00."];
  const checks = [
    ["#FFFFFF", colors["--brand"], 4.5, "White text on the accent"],
    [colors["--brand-mid"], "#FFFFFF", 4.5, "Brand mid on white"],
    [colors["--brand-mid"], colors["--brand-soft"], 4.5, "Brand mid on soft surface"],
    ["#192820", colors["--brand-soft"], 4.5, "Body text on soft surface"],
    [colors["--sidebar-muted"], colors["--sidebar"], 4.5, "Sidebar text"],
    [colors["--focus"], "#FFFFFF", 3, "Focus indicator on white"],
  ];
  return checks
    .filter(([foreground, background, minimum]) => contrastRatio(foreground, background) < minimum)
    .map(([, , , label]) => `${label} does not meet the required contrast ratio.`);
}
