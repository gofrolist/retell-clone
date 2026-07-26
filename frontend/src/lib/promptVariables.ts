/** Placeholder names a prompt reads — the dashboard's mirror of the backend's
 *  `template_variables.prompt_variables`. Same grammar: inner whitespace is
 *  stripped, and a nested key ({{current_time_{{user_timezone}}}}) reports its
 *  inner name, since that is the one worth setting. */

const PLACEHOLDER = /\{\{\s*((?:[^{}]|\{\{[^{}]+?\}\})+?)\s*\}\}/g;
const INNER = /\{\{\s*([^{}]+?)\s*\}\}/g;

/** Variable names referenced in `text`, in first-appearance order. */
export function promptVariables(text: string | null | undefined): string[] {
  const names: string[] = [];
  for (const match of (text ?? "").matchAll(PLACEHOLDER)) {
    const key = match[1].trim();
    const found = key.includes("{{")
      ? [...key.matchAll(INNER)].map((m) => m[1].trim())
      : [key];
    for (const name of found) {
      if (name && !names.includes(name)) names.push(name);
    }
  }
  return names;
}
