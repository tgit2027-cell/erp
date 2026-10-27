import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  buildPaletteColors,
  contrastRatio,
  customPaletteContrastIssues,
} from "../src/custom-palette-colors.mjs";

const css = readFileSync(new URL("../src/style.css", import.meta.url), "utf8");
const purchasingCss = readFileSync(new URL("../src/purchasing-workbench.css", import.meta.url), "utf8");
const deliveryPreparationCss = readFileSync(new URL("../src/delivery-preparation.css", import.meta.url), "utf8");

function tokensIn(selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = css.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(block, `Missing CSS token block: ${selector}`);
  return Object.fromEntries(
    [...block[1].matchAll(/(--[\w-]+)\s*:\s*(#[\da-fA-F]{3,8})\s*;/g)]
      .map(([, name, value]) => [name, value.toUpperCase()]),
  );
}

const tivoli = tokensIn(":root");
const roche = tokensIn(':root[data-unit-theme="roche"]');
const natuzzi = tokensIn(':root[data-unit-theme="natuzzi"]');
const slate = tokensIn(':root[data-unit-theme="slate"]');

function color(hex) {
  const normalized = hex.slice(1);
  const full = normalized.length === 3
    ? [...normalized].map((digit) => digit + digit).join("")
    : normalized;
  assert.match(full, /^[\da-f]{6}$/i, `Expected a six-digit hex color, got ${hex}`);
  return [0, 2, 4].map((index) => Number.parseInt(full.slice(index, index + 2), 16) / 255);
}

function luminance(hex) {
  const [r, g, b] = color(hex).map((channel) =>
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  );
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(first, second) {
  const values = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

function requireRatio(label, foreground, background, minimum) {
  const ratio = contrast(foreground, background);
  assert.ok(
    ratio >= minimum,
    `${label}: expected at least ${minimum}:1, got ${ratio.toFixed(2)}:1 (${foreground} on ${background})`,
  );
  console.log(`PASS ${label}: ${ratio.toFixed(2)}:1`);
}

assert.match(deliveryPreparationCss, /input:focus-visible \+ \.dp-check-box/);
requireRatio("Delivery preparation body on paper", "#28352f", "#fffefa", 4.5);
requireRatio("Delivery preparation muted text on paper", "#68766e", "#fffefa", 4.5);
requireRatio("Delivery preparation reviewed text", "#3c4d42", "#fbfaf5", 4.5);
for (const [name, palette] of Object.entries({ tivoli, roche, natuzzi, slate })) {
  requireRatio(`Delivery preparation ${name} accent on paper`, palette["--brand-deep"], "#fffefa", 4.5);
  requireRatio(`Delivery preparation ${name} focus on paper`, palette["--brand"], "#fffefa", 3);
}

assert.equal(tivoli["--brand"], "#B24F00", "Tivoli primary accent must remain #B24F00");
assert.equal(tivoli["--green"], "#B24F00");
assert.equal(tivoli["--line"], "#E9D8CA");
assert.equal(tivoli["--sand"], "#FBF7F2");
assert.equal(tivoli["--brand-deep"], "#843B00");
assert.equal(tivoli["--brand-soft"], "#F8EBDD");
assert.equal(tivoli["--brand-mid"], "#843B00");
assert.equal(tivoli["--sidebar"], "#30271F");
assert.equal(tivoli["--sidebar-muted"], "#D4C6B8");
assert.equal(tivoli["--focus"], "#B24F00");

requireRatio("Button white text on primary accent", "#FFFFFF", tivoli["--brand"], 4.5);
requireRatio("Small accent text on soft surface", tivoli["--brand-mid"], tivoli["--brand-soft"], 4.5);
requireRatio("Small accent text on white", tivoli["--brand-mid"], "#FFFFFF", 4.5);
requireRatio("Dark text on soft surface", tivoli["--ink"], tivoli["--brand-soft"], 4.5);
requireRatio("Muted sidebar text on sidebar", tivoli["--sidebar-muted"], tivoli["--sidebar"], 4.5);
requireRatio("Focus ring on white", tivoli["--focus"], "#FFFFFF", 3);
requireRatio("Tivoli purchasing button text on unit accent", "#FFFAF4", tivoli["--brand"], 4.5);
requireRatio("Tivoli purchasing accent text on tinted surface", tivoli["--brand-deep"], tivoli["--brand-soft"], 4.5);
requireRatio("Tivoli purchasing focus ring on tinted surface", tivoli["--brand"], tivoli["--brand-soft"], 3);

const expectedPalettes = {
  roche: {
    "--brand": "#426D8B", "--brand-deep": "#315872", "--brand-soft": "#E8F1F6",
    "--brand-mid": "#3B6380", "--sidebar": "#263F50", "--sidebar-muted": "#B7CBD7",
    "--focus": "#6394B2", "--green": "#426D8B", "--line": "#DCE6EC", "--sand": "#F1F5F7",
  },
  natuzzi: {
    "--brand": "#806343", "--brand-deep": "#654B32", "--brand-soft": "#F2ECE4",
    "--brand-mid": "#745838", "--sidebar": "#473A2E", "--sidebar-muted": "#C9B9A4",
    "--focus": "#A9855F", "--green": "#806343", "--line": "#E8E0D6", "--sand": "#F6F3EE",
  },
  slate: {
    "--brand": "#4C6474", "--brand-deep": "#344C5C", "--brand-soft": "#EAF0F3",
    "--brand-mid": "#4B6575", "--sidebar": "#2C414D", "--sidebar-muted": "#C0CCD2",
    "--focus": "#718FA1", "--green": "#4C6474", "--line": "#DFE6E9", "--sand": "#F2F5F6",
  },
};

const workspaceCss = readFileSync(new URL("../src/personal-reporting-workspace.css", import.meta.url), "utf8");
assert.match(workspaceCss, /:focus-visible\s*\{\s*outline:\s*2px solid var\(--brand\)/);
for (const [name, palette] of Object.entries({ tivoli, roche, natuzzi, slate })) {
  const ink = tivoli["--ink"];
  const muted = tivoli["--muted"];
  requireRatio(`${name} workspace body on white`, ink, "#FFFFFF", 4.5);
  requireRatio(`${name} workspace muted labels on tinted surface`, muted, palette["--sand"], 4.5);
  requireRatio(`${name} workspace panel labels on soft surface`, palette["--brand-mid"], palette["--brand-soft"], 4.5);
  requireRatio(`${name} sales-example body text on soft surface`, ink, palette["--brand-soft"], 4.5);
  requireRatio(`${name} sales-example kicker on soft surface`, palette["--brand-deep"], palette["--brand-soft"], 4.5);
  requireRatio(`${name} sales-example metadata on white`, muted, "#FFFFFF", 4.5);
  requireRatio(`${name} workspace focus outline on white`, palette["--brand"], "#FFFFFF", 3);
}
requireRatio("Workspace unavailable/error text on error surface", "#854A3F", "#FBF0EB", 4.5);

for (const [name, expected] of Object.entries(expectedPalettes)) {
  const actual = { roche, natuzzi, slate }[name];
  for (const [token, value] of Object.entries(expected)) {
    assert.equal(actual[token], value, `${name} ${token} token changed`);
  }
  requireRatio(`${name} purchasing button text on unit accent`, "#FFFAF4", actual["--brand"], 4.5);
  requireRatio(`${name} purchasing accent text on tinted surface`, actual["--brand-deep"], actual["--brand-soft"], 4.5);
  requireRatio(`${name} purchasing focus ring on tinted surface`, actual["--brand"], actual["--brand-soft"], 3);
  console.log(`PASS ${name} palette tokens unchanged`);
}

assert.match(purchasingCss, /--pw-ink:\s*var\(--ink/);
assert.match(purchasingCss, /--pw-wash:\s*var\(--sand/);
assert.match(purchasingCss, /--pw-line:\s*var\(--line/);
assert.match(purchasingCss, /--pw-accent:\s*var\(--brand/);
assert.match(purchasingCss, /--pw-accent-dark:\s*var\(--brand-deep/);
assert.match(purchasingCss, /--pw-accent-soft:\s*var\(--brand-soft/);
console.log("PASS purchasing workbench surfaces and accents follow the selected business-unit theme");

const generatedCustomPalette = buildPaletteColors("#B24F00");
assert.deepEqual(generatedCustomPalette, {
  "--brand": "#B24F00",
  "--green": "#B24F00",
  "--brand-deep": "#8E3F00",
  "--brand-mid": "#974300",
  "--brand-soft": "#F7EDE6",
  "--sidebar": "#2E1500",
  "--sidebar-muted": "#EBD1BD",
  "--focus": "#B24F00",
  "--line": "#F1DFD1",
  "--sand": "#FDFAF7",
});
assert.deepEqual(customPaletteContrastIssues(generatedCustomPalette), []);
requireRatio("Custom white text on selected accent", "#FFFFFF", generatedCustomPalette["--brand"], 4.5);
requireRatio(
  "Custom accent text on generated soft surface",
  generatedCustomPalette["--brand-mid"],
  generatedCustomPalette["--brand-soft"],
  4.5,
);
assert.ok(customPaletteContrastIssues(buildPaletteColors("#FF0000")).length > 0);
assert.ok(customPaletteContrastIssues(null).length > 0);
assert.throws(() => buildPaletteColors("orange"), /six-digit hex color/);
console.log("PASS custom palette tint generation, hex validation, and WCAG contrast rejection");

assert.match(css, /\.brand-monogram\s*\{\s*background:\s*#244b39;\s*\}/i);
assert.match(css, /\.sidebar-brand \.brand-monogram\s*\{\s*color:\s*#244b39;\s*\}/i);
console.log("PASS Tivoli monogram colors remain fixed across business-unit themes");

const preparationCss = readFileSync(new URL("../src/packing-receipt-preparation.css", import.meta.url), "utf8");
assert.match(preparationCss, /color-mix\(in srgb, var\(--brand-soft\) 24%, var\(--paper\)\)/);
for (const [name, palette] of Object.entries({
  tivoli, roche, natuzzi, slate, custom: generatedCustomPalette,
})) {
  const surface = `#${color(palette["--brand-soft"]).map((channel) =>
    Math.round((channel * 0.24 + 0.76) * 255).toString(16).padStart(2, "0"),
  ).join("")}`;
  requireRatio(`${name} receipt-preparation muted text on blended surface`, tivoli["--muted"], surface, 4.5);
  requireRatio(`${name} receipt-preparation badge on blended surface`, palette["--brand-deep"], surface, 4.5);
  requireRatio(`${name} receipt-preparation input focus on white`, palette["--focus"], "#FFFFFF", 3);
}

const navigationCss = readFileSync(new URL("../src/section-navigation.css", import.meta.url), "utf8");
assert.match(navigationCss, /outline:\s*2px solid var\(--sidebar-muted\)/);
for (const [name, palette] of Object.entries({
  tivoli, roche, natuzzi, slate, custom: generatedCustomPalette,
})) {
  const sidebar = palette["--sidebar"];
  const muted = palette["--sidebar-muted"];
  // Existing active child background is white at 22/255 alpha. This is
  // lighter than the new header's 8%-muted hover surface: a conservative
  // contrast check for white active text and the muted keyboard focus ring.
  const activeSurface = `#${color(sidebar).map((channel) =>
    Math.round((22 / 255 + channel * (1 - 22 / 255)) * 255)
      .toString(16).padStart(2, "0")).join("")}`;
  requireRatio(`${name} sidebar group and child text`, muted, sidebar, 4.5);
  requireRatio(`${name} sidebar active child text`, "#FFFFFF", activeSurface, 4.5);
  requireRatio(`${name} sidebar keyboard focus ring`, muted, activeSurface, 3);
}

const returnCss = readFileSync(new URL("../src/purchasing-workbench.css", import.meta.url), "utf8");
assert.match(returnCss, /\.pw-return-index[^}]+background: var\(--brand\)/);
assert.match(returnCss, /\.pw-return-banner strong[^}]+color: var\(--brand-deep\)/);
for (const [name, palette] of Object.entries({
  tivoli, roche, natuzzi, slate, custom: generatedCustomPalette,
})) {
  const tint = `#${color(palette["--brand-soft"]).map((channel) =>
    Math.round((channel * 0.45 + 0.55) * 255).toString(16).padStart(2, "0"),
  ).join("")}`;
  requireRatio(`${name} return-intake accent label on tinted surface`, palette["--brand-deep"], tint, 4.5);
  requireRatio(`${name} return-intake body on tinted surface`, "#34443A", tint, 4.5);
  requireRatio(`${name} return-intake muted text on tinted surface`, "#59665C", tint, 4.5);
  requireRatio(`${name} return-intake index on unit accent`, "#FFF8ED", palette["--brand"], 4.5);
}

const inventoryCss = readFileSync(new URL("../src/inventory-workspace.css", import.meta.url), "utf8");
assert.doesNotMatch(inventoryCss, /var\(--sidebar-muted\)/,
  "Inventory's light surfaces must not use pale text intended for the dark sidebar");
assert.match(inventoryCss, /\.inventory-bucket-fields dt,[^{]+\{\s*color:\s*var\(--muted\)/);
console.log("PASS Inventory light-surface labels use the content-muted contrast token");
for (const [name, palette] of Object.entries({
  tivoli, roche, natuzzi, slate, custom: generatedCustomPalette,
})) {
  requireRatio(`${name} Inventory muted labels on white`, tivoli["--muted"], "#FFFFFF", 4.5);
  requireRatio(`${name} Inventory muted labels on tinted surface`, tivoli["--muted"], palette["--sand"], 4.5);
}

const collectionsCss = readFileSync(new URL("../src/retail-collections.css", import.meta.url), "utf8");
assert.match(collectionsCss, /\.rc-stamp[^}]+background:\s*var\(--brand-soft\)/);
assert.match(collectionsCss, /\.rc-caveat[^}]+background:\s*var\(--brand-soft\)/);
assert.match(collectionsCss, /\.rc-table thead th[^}]+background:\s*var\(--brand-soft\)/);
assert.match(collectionsCss, /\.rc-button-primary:hover[^}]+background:\s*var\(--brand-deep\)/);
assert.match(collectionsCss, /\.retail-collections button:focus-visible,[^{]+\{\s*outline:\s*3px solid var\(--rc-accent\)/);
assert.match(collectionsCss, /--rc-accent:\s*var\(--brand-deep/);
assert.doesNotMatch(collectionsCss, /#f8ebdd|#6f3000/i,
  "Collection surfaces and hover states must follow the selected unit palette");
for (const [name, palette] of Object.entries({
  tivoli, roche, natuzzi, slate, custom: generatedCustomPalette,
})) {
  requireRatio(`${name} collection body text on unit tint`, tivoli["--ink"], palette["--brand-soft"], 4.5);
  requireRatio(`${name} collection caveat text on unit tint`, "#4F5B53", palette["--brand-soft"], 4.5);
  requireRatio(`${name} collection accent text on unit tint`, palette["--brand-deep"], palette["--brand-soft"], 4.5);
  requireRatio(`${name} collection table heading on unit tint`, "#394B40", palette["--brand-soft"], 4.5);
  requireRatio(`${name} collection button text on unit accent`, "#FFFAF4", palette["--brand-deep"], 4.5);
  requireRatio(`${name} collection button hover text on paper`, palette["--brand-deep"], "#FFFFFF", 4.5);
  requireRatio(`${name} collection keyboard focus on paper`, palette["--brand-deep"], "#FFFFFF", 3);
  requireRatio(`${name} collection keyboard focus on unit tint`, palette["--brand-deep"], palette["--brand-soft"], 3);
}

// Quotation presentation uses explicit neutral surfaces in every BU theme.
for (const [label, foreground, background] of [
  ["quotation item copy", "#536157", "#F6F4ED"],
  ["quotation item title", "#28382F", "#F6F4ED"],
  ["quotation missing translation", "#8B4F2C", "#FFFDF7"],
  ["quotation Arabic form label", "#536157", "#F8F6EF"],
  ["quotation image unavailable", "#536157", "#EFEDE5"],
  ["quotation document body", "#536157", "#FFFDF7"],
  ["quotation document total", "#28382F", "#FFFDF7"],
  ["quotation pending owner terms", "#704622", "#F6EFE3"],
]) {
  assert.ok(purchasingCss.toLowerCase().includes(foreground.toLowerCase()), `${label}: missing CSS foreground`);
  requireRatio(label, foreground, background, 4.5);
}

// Standalone Finance prototype is not mounted in the shared app.
const financeDraftCss = readFileSync(new URL("../src/finance-draft-workspace.css", import.meta.url), "utf8");
const financeColor = (name) => {
  const match = financeDraftCss.match(new RegExp(`${name}:\\s*(#[a-fA-F0-9]{6})\\s*;`));
  assert.ok(match, `Missing Finance prototype color token ${name}`);
  return match[1];
};
for (const [label, foreground, background, minimum] of [
  ["Finance draft body on paper", "--finance-ink", "--finance-paper", 4.5],
  ["Finance draft body on banner", "--finance-ink", "--finance-tint", 4.5],
  ["Finance draft warning on banner", "--finance-warning", "--finance-tint", 4.5],
  ["Finance draft button text", "--finance-paper", "--finance-button", 4.5],
  ["Finance draft disabled control text", "--finance-disabled-ink", "--finance-disabled-bg", 4.5],
  ["Finance draft focus on paper", "--finance-focus", "--finance-paper", 3],
  ["Finance draft focus on banner", "--finance-focus", "--finance-tint", 3],
]) {
  requireRatio(label, financeColor(foreground), financeColor(background), minimum);
}

const allocationCss = readFileSync(new URL("../src/finance-allocation.css", import.meta.url), "utf8");
function allocationToken(name) {
  const match = allocationCss.match(new RegExp(`${name}:\\s*(#[a-fA-F0-9]{6})\\s*;`));
  assert.ok(match, `Missing allocation token ${name}`);
  return match[1];
}
function allocationRule(selector, property) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = allocationCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  const match = block?.[1].match(new RegExp(`(?:^|;)\\s*${property}:\\s*(#[a-fA-F0-9]{6})`));
  assert.ok(match, `Missing allocation ${selector} ${property}`);
  return match[1];
}
const allocationSurfaces = {
  card: allocationToken("--allocation-card"),
  banner: allocationRule(".allocation-lab .finance-draft-banner", "background"),
  remainder: allocationRule(".allocation-remainder", "background"),
  journal: allocationRule(".allocation-journal-review", "background"),
};
for (const [name, background] of Object.entries(allocationSurfaces)) {
  requireRatio(`Allocation body on ${name}`, allocationToken("--allocation-ink"), background, 4.5);
  requireRatio(`Allocation focus on ${name}`, financeColor("--finance-focus"), background, 3);
}
for (const name of ["card", "banner"]) {
  requireRatio(`Allocation links on ${name}`, allocationRule(".allocation-lab a", "color"), allocationSurfaces[name], 4.5);
}
requireRatio("Allocation warning on remainder", allocationRule(".allocation-remainder p", "color"), allocationSurfaces.remainder, 4.5);

const budgetCss = readFileSync(new URL("../src/finance-budget.css", import.meta.url), "utf8");
function budgetRule(selector, property) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = budgetCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  const value = block?.[1].match(new RegExp(`(?:^|;)\\s*${property}:\\s*(#[a-fA-F0-9]{6})`))?.[1];
  assert.ok(value, `Missing budget ${selector} ${property}`);
  return value;
}
for (const selector of [
  ".budget-lab .finance-draft-banner", ".budget-register-empty", ".budget-currency",
  ".budget-entry", ".budget-total", ".budget-advisories", ".budget-validation",
]) {
  const surface = budgetRule(selector, "background");
  requireRatio(`Budget body on ${selector}`, allocationToken("--allocation-ink"), surface, 4.5);
  requireRatio(`Budget focus on ${selector}`, financeColor("--finance-focus"), surface, 3);
}
for (const [text, surface] of [
  [".budget-currency strong", ".budget-currency"], [".budget-currency small", ".budget-currency"],
  [".budget-entry legend", ".budget-entry"], [".budget-total strong", ".budget-total"],
  [".budget-total p", ".budget-total"], [".budget-advisories strong", ".budget-advisories"],
  [".budget-validation", ".budget-validation"],
]) {
  requireRatio(`Budget text ${text}`, budgetRule(text, "color"), budgetRule(surface, "background"), 4.5);
}
requireRatio("Budget live status", budgetRule(".budget-lab .budget-live-status", "color"), allocationToken("--allocation-paper"), 4.5);

const reconciliationCss = readFileSync(new URL("../src/finance-reconciliation.css", import.meta.url), "utf8");
function reconciliationRule(selector, property) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = reconciliationCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  const value = block?.[1].match(new RegExp(`(?:^|;)\\s*${property}:\\s*(#[a-fA-F0-9]{6}|var\\(--rec-[a-z]+\\))`))?.[1];
  assert.ok(value, `Missing reconciliation ${selector} ${property}`);
  return value.startsWith("var(") ? reconciliationRule(".reconciliation-lab", value.slice(4, -1)) : value;
}
for (const surface of [
  ".reconciliation-lab .finance-draft-banner", ".reconciliation-lab .rec-editor",
  ".reconciliation-lab .rec-currency", ".reconciliation-lab .rec-formula",
  ".reconciliation-lab .rec-match", ".reconciliation-lab .rec-match-alert",
  ".reconciliation-lab .rec-validation",
]) {
  const background = reconciliationRule(surface, "background");
  requireRatio(`Reconciliation body on ${surface}`, reconciliationRule(".reconciliation-lab", "--rec-ink"), background, 4.5);
  requireRatio(`Reconciliation focus on ${surface}`, financeColor("--finance-focus"), background, 3);
}
for (const [text, surface] of [
  [".reconciliation-lab a", ".reconciliation-lab .finance-draft-banner"],
  [".reconciliation-lab a", ".reconciliation-lab .rec-editor"],
  [".reconciliation-lab .rec-editor legend", ".reconciliation-lab .rec-editor"],
  [".reconciliation-lab .rec-editor legend", ".reconciliation-lab .rec-match"],
  [".reconciliation-lab .rec-math dt", ".reconciliation-lab .rec-formula"],
  [".reconciliation-lab .rec-match-alert", ".reconciliation-lab .rec-match-alert"],
  [".reconciliation-lab .rec-validation", ".reconciliation-lab .rec-validation"],
]) {
  requireRatio(`Reconciliation text ${text} on ${surface}`, reconciliationRule(text, "color"),
    reconciliationRule(surface, "background"), 4.5);
}

const expenseCss = readFileSync(new URL("../src/finance-expense.css", import.meta.url), "utf8");
function expenseColor(selector, property) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = expenseCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1];
  const declaration = block?.match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))?.[1];
  const value = declaration?.match(/#[a-fA-F0-9]{6}|var\(--expense-[a-z]+\)/)?.[0];
  assert.ok(value, `Missing expense ${selector} ${property}`);
  return value.startsWith("var(") ? expenseColor(".expense-lab", value.slice(4, -1)) : value;
}
const expenseInk = expenseColor(".expense-lab", "--expense-ink");
const expenseFocus = expenseColor(".expense-lab summary:focus-visible", "outline");
for (const surface of [".expense-lab .expense-editor", ".expense-lab .expense-row",
  ".expense-lab .expense-boundary", ".expense-lab .expense-currency"]) {
  requireRatio(`Expense body on ${surface}`, expenseInk, expenseColor(surface, "background"), 4.5);
  requireRatio(`Expense focus on ${surface}`, expenseFocus, expenseColor(surface, "background"), 3);
}
requireRatio("Expense paper body", expenseInk, expenseColor(".expense-lab", "background"), 4.5);
requireRatio("Expense paper focus", expenseFocus, expenseColor(".expense-lab", "background"), 3);
requireRatio("Expense muted text", expenseColor(".expense-lab", "--expense-muted"), expenseColor(".expense-lab", "background"), 4.5);
requireRatio("Expense input text", expenseColor(".expense-lab textarea", "color"), expenseColor(".expense-lab textarea", "background"), 4.5);
requireRatio("Expense input focus", expenseFocus, expenseColor(".expense-lab textarea", "background"), 3);
requireRatio("Expense banner text", expenseColor(".expense-lab .finance-draft-banner", "color"), expenseColor(".expense-lab .finance-draft-banner", "background"), 4.5);
requireRatio("Expense banner eyebrow", expenseColor(".expense-lab .finance-draft-banner .finance-draft-eyebrow", "color"), expenseColor(".expense-lab .finance-draft-banner", "background"), 4.5);
requireRatio("Expense banner link focus", expenseColor(".expense-lab .finance-draft-banner a:focus-visible", "outline"), expenseColor(".expense-lab .finance-draft-banner", "background"), 3);
requireRatio("Expense total text", expenseColor(".expense-lab .expense-total", "color"), expenseColor(".expense-lab .expense-total", "background"), 4.5);
requireRatio("Expense primary button text", expenseColor(".expense-lab button", "color"), expenseColor(".expense-lab button", "background"), 4.5);
requireRatio("Expense secondary button", expenseColor(".expense-lab .expense-secondary", "color"), expenseColor(".expense-lab .expense-editor", "background"), 4.5);
requireRatio("Expense alert text", expenseColor(".expense-lab .expense-alert", "color"), expenseColor(".expense-lab .expense-alert", "background"), 4.5);
requireRatio("Expense input border against field", expenseColor(".expense-lab textarea", "border"), expenseColor(".expense-lab textarea", "background"), 3);
requireRatio("Expense input border against row", expenseColor(".expense-lab textarea", "border"), expenseColor(".expense-lab .expense-row", "background"), 3);

const assetCss = readFileSync(new URL("../src/finance-asset.css", import.meta.url), "utf8");
function assetColor(selector, property) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const block = assetCss.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`))?.[1];
  const declaration = block?.match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))?.[1];
  const value = declaration?.match(/#[a-fA-F0-9]{6}|var\(--asset-[a-z]+\)/)?.[0];
  assert.ok(value, `Missing asset ${selector} ${property}`);
  return value.startsWith("var(") ? assetColor(".asset-lab", value.slice(4, -1)) : value;
}
const assetInk = assetColor(".asset-lab", "--asset-ink");
const assetFocus = assetColor(".asset-lab summary:focus-visible", "outline");
for (const surface of [".asset-lab .asset-editor", ".asset-lab .asset-note", ".asset-lab .asset-currency"]) {
  requireRatio(`Asset body on ${surface}`, assetInk, assetColor(surface, "background"), 4.5);
  requireRatio(`Asset focus on ${surface}`, assetFocus, assetColor(surface, "background"), 3);
}
requireRatio("Asset paper body", assetInk, assetColor(".asset-lab", "background"), 4.5);
requireRatio("Asset paper focus", assetFocus, assetColor(".asset-lab", "background"), 3);
requireRatio("Asset muted text", assetColor(".asset-lab", "--asset-muted"), assetColor(".asset-lab", "background"), 4.5);
requireRatio("Asset input text", assetColor(".asset-lab textarea", "color"), assetColor(".asset-lab textarea", "background"), 4.5);
requireRatio("Asset input focus", assetFocus, assetColor(".asset-lab textarea", "background"), 3);
requireRatio("Asset banner text", assetColor(".asset-lab .finance-draft-banner", "color"), assetColor(".asset-lab .finance-draft-banner", "background"), 4.5);
requireRatio("Asset banner eyebrow", assetColor(".asset-lab .finance-draft-banner .finance-draft-eyebrow", "color"), assetColor(".asset-lab .finance-draft-banner", "background"), 4.5);
requireRatio("Asset banner link focus", assetColor(".asset-lab .finance-draft-banner a:focus-visible", "outline-color"), assetColor(".asset-lab .finance-draft-banner", "background"), 3);
requireRatio("Asset cost text", assetColor(".asset-lab .asset-cost", "color"), assetColor(".asset-lab .asset-cost", "background"), 4.5);
requireRatio("Asset primary button", assetColor(".asset-lab button", "color"), assetColor(".asset-lab button", "background"), 4.5);
requireRatio("Asset secondary button", assetColor(".asset-lab .asset-secondary", "color"), assetColor(".asset-lab .asset-editor", "background"), 4.5);
requireRatio("Asset alert text", assetColor(".asset-lab .asset-alert", "color"), assetColor(".asset-lab .asset-alert", "background"), 4.5);
requireRatio("Asset input border against field", assetColor(".asset-lab textarea", "border"), assetColor(".asset-lab textarea", "background"), 3);
requireRatio("Asset input border against editor", assetColor(".asset-lab textarea", "border"), assetColor(".asset-lab .asset-editor", "background"), 3);

const forecastCss = readFileSync(new URL("../src/finance-forecast.css", import.meta.url), "utf8");
function forecastColor(selector, property, optional = false) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  let value;
  for (const match of forecastCss.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, "g"))) {
    const declaration = match[1].match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))?.[1];
    const color = declaration?.match(/#[a-fA-F0-9]{6}|var\(--forecast-[a-z]+\)/)?.[0];
    if (color) value = color;
  }
  if (!value && optional) return null;
  assert.ok(value, `Missing forecast ${selector} ${property}`);
  return value.startsWith("var(") ? forecastColor(".forecast-lab", value.slice(4, -1)) : value;
}
const forecastInk = forecastColor(".forecast-lab", "--forecast-ink");
const forecastFocus = forecastColor(".forecast-lab summary:focus-visible", "outline");
for (const surface of [".forecast-lab", ".forecast-lab .forecast-editor", ".forecast-lab .flow-row",
  ".forecast-lab .forecast-note", ".forecast-lab .forecast-currency"]) {
  requireRatio(`Forecast body on ${surface}`, forecastInk, forecastColor(surface, "background"), 4.5);
  requireRatio(`Forecast focus on ${surface}`, forecastFocus, forecastColor(surface, "background"), 3);
}
requireRatio("Forecast muted text", forecastColor(".forecast-lab", "--forecast-muted"), forecastColor(".forecast-lab", "background"), 4.5);
requireRatio("Forecast input text", forecastColor(".forecast-lab textarea", "color"), forecastColor(".forecast-lab textarea", "background"), 4.5);
requireRatio("Forecast input focus", forecastFocus, forecastColor(".forecast-lab textarea", "background"), 3);
requireRatio("Forecast banner text", forecastColor(".forecast-lab .forecast-banner", "color"), forecastColor(".forecast-lab .forecast-banner", "background"), 4.5);
requireRatio("Forecast banner eyebrow", forecastColor(".forecast-lab .forecast-banner .forecast-eyebrow", "color"), forecastColor(".forecast-lab .forecast-banner", "background"), 4.5);
requireRatio("Forecast banner focus", forecastColor(".forecast-lab .forecast-banner a:focus-visible", "outline-color", true) || forecastFocus, forecastColor(".forecast-lab .forecast-banner", "background"), 3);
requireRatio("Forecast review text", forecastColor(".forecast-lab .forecast-review", "color"), forecastColor(".forecast-lab .forecast-review", "background"), 4.5);
requireRatio("Forecast primary button", forecastColor(".forecast-lab button", "color"), forecastColor(".forecast-lab button", "background"), 4.5);
requireRatio("Forecast alert text", forecastColor(".forecast-lab .forecast-alert", "color"), forecastColor(".forecast-lab .forecast-alert", "background"), 4.5);
requireRatio("Forecast input border", forecastColor(".forecast-lab textarea", "border"), forecastColor(".forecast-lab textarea", "background"), 3);

const invoiceCss = readFileSync(new URL("../src/finance-invoice.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
function invoiceColor(selector, property) {
  let value;
  for (const match of invoiceCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const selectors = match[1].trim().split(/,(?![^()]*\))/).map(item => item.trim());
    if (!selectors.includes(selector)) continue;
    const declaration = match[2].match(new RegExp(`(?:^|;)\\s*${property}:\\s*([^;]+)`))?.[1];
    const color = declaration?.match(/#[a-fA-F0-9]{6}|var\(--invoice-[a-z]+\)/)?.[0];
    if (color) value = color;
  }
  assert.ok(value, `Missing invoice ${selector} ${property}`);
  return value.startsWith("var(") ? invoiceColor(".invoice-lab", value.slice(4, -1)) : value;
}
const invoiceInk = invoiceColor(".invoice-lab", "--invoice-ink");
const invoiceFocus = invoiceColor(".invoice-lab :is(input, select, textarea, button, a, summary):focus-visible", "outline");
for (const surface of [".invoice-lab", ".invoice-history", ".invoice-line", ".invoice-note", ".invoice-currency"]) {
  requireRatio(`Invoice body on ${surface}`, invoiceInk, invoiceColor(surface, "background"), 4.5);
  requireRatio(`Invoice focus on ${surface}`, invoiceFocus, invoiceColor(surface, "background"), 3);
}
requireRatio("Invoice muted text", invoiceColor(".invoice-lab", "--invoice-muted"), invoiceColor(".invoice-lab", "background"), 4.5);
requireRatio("Invoice field text", invoiceColor(".invoice-lab textarea", "color"), invoiceColor(".invoice-lab textarea", "background"), 4.5);
requireRatio("Invoice field focus", invoiceFocus, invoiceColor(".invoice-lab textarea", "background"), 3);
requireRatio("Invoice field border", invoiceColor(".invoice-lab textarea", "border"), invoiceColor(".invoice-lab textarea", "background"), 3);
requireRatio("Invoice banner body", invoiceColor(".invoice-banner", "color"), invoiceColor(".invoice-banner", "background"), 4.5);
requireRatio("Invoice banner kicker", invoiceColor(".invoice-banner .invoice-kicker", "color"), invoiceColor(".invoice-banner", "background"), 4.5);
requireRatio("Invoice primary button", invoiceColor(".invoice-lab button", "color"), invoiceColor(".invoice-lab button", "background"), 4.5);
requireRatio("Invoice surface kicker", invoiceColor(".invoice-kicker", "color"), invoiceColor(".invoice-history", "background"), 4.5);
requireRatio("Invoice review body", invoiceColor(".invoice-review", "color"), invoiceColor(".invoice-review", "background"), 4.5);
requireRatio("Invoice duplicate note", invoiceColor(".invoice-duplicate", "color"), invoiceColor(".invoice-duplicate", "background"), 4.5);
requireRatio("Invoice alert", invoiceColor(".invoice-alert", "color"), invoiceColor(".invoice-alert", "background"), 4.5);
requireRatio("Invoice banner link focus", invoiceColor(".invoice-banner a:focus-visible", "outline-color"), invoiceColor(".invoice-banner", "background"), 3);
requireRatio("Invoice review kicker", invoiceColor(".invoice-review .invoice-kicker", "color"), invoiceColor(".invoice-review", "background"), 4.5);

// Consolidated lab uses new scoped surfaces, independently of normal ERP
// palettes. Check worst-case opaque gradient stop, not just the pale base.
const labCss = readFileSync(new URL("../src/finance-lab-review.css", import.meta.url), "utf8");
function labToken(name) {
  const value = labCss.match(new RegExp(`--lab-${name}:\\s*(#[0-9a-f]{6})`, "i"))?.[1];
  assert.ok(value, `Missing Finance lab token ${name}`);
  return value;
}
for (const background of [labToken("paper"), labToken("card"), "#e9eee5", "#e6eee5", "#d0dfcf"]) {
  for (const token of ["ink", "muted", "teal"]) {
    requireRatio(`Finance lab ${token} on ${background}`, labToken(token), background, 4.5);
  }
  requireRatio(`Finance lab focus on ${background}`, labToken("teal"), background, 3);
}
requireRatio("Finance lab button", "#f8f8ee", labToken("teal"), 4.5);
requireRatio("Finance lab hover", "#ffffff", "#174d48", 4.5);
requireRatio("Finance lab brand", "#f6f7ed", labToken("teal"), 4.5);
requireRatio("Finance lab recovery text", "#765d4d", "#faf2e8", 4.5);
requireRatio("Finance lab recovery identity", "#735a47", "#faf2e8", 4.5);
requireRatio("Finance lab recovery control", labToken("teal"), "#faf2e8", 4.5);
requireRatio("Finance lab saved item ID", "#a75d48", labToken("card"), 4.5);
requireRatio("Finance lab large unavailable marker", labToken("coral"), labToken("card"), 3);
