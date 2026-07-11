// Categorical palette (CVD-validated for light surface). Fixed order, never cycled.
export const CHART_BLUE = "#5c7cf0";
export const CHART_CYAN = "#2ea8d5";
export const CHART_PURPLE = "#9333ea";
export const CHART_ORANGE = "#d97b16";
export const CHART_PINK = "#e0447c";
export const CHART_GRAY = "#94a3b8"; // reserved for Unknown/Other

export const CATEGORICAL = [
  CHART_BLUE,
  CHART_CYAN,
  CHART_PURPLE,
  CHART_ORANGE,
  CHART_PINK,
];

/** Color for a named slice: semantic categories get stable colors. */
export function sliceColor(name: string, index: number): string {
  const n = name.toLowerCase();
  if (n.includes("unknown") || n === "other") return CHART_GRAY;
  if (n.includes("negative") || n.includes("unsuccessful")) return CHART_ORANGE;
  if (n.includes("positive") || n.includes("successful")) return CHART_BLUE;
  if (n.includes("neutral")) return CHART_CYAN;
  return CATEGORICAL[index % CATEGORICAL.length];
}
