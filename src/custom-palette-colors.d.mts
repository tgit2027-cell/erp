export type PaletteCssColors = Record<string, string>;

export function buildPaletteColors(accent: string): PaletteCssColors;
export function contrastRatio(first: string, second: string): number;
export function customPaletteContrastIssues(colors: PaletteCssColors | null): string[];
