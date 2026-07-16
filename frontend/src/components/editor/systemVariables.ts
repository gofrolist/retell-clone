/** Retell default system variables (docs.retellai.com/build/dynamic-variables)
 * resolved by the worker at call time, plus Arhiteq call-scoped aliases. */
export type SystemVariable = { name: string; description: string };

export const SYSTEM_VARIABLES: SystemVariable[] = [
  { name: "current_time", description: "Current time in America/Los_Angeles" },
  {
    name: "current_time_[timezone]",
    description: "Current time in an IANA timezone, e.g. current_time_Australia/Sydney",
  },
  { name: "current_hour", description: "Hour as a fraction (3.5 = 3:30) in America/Los_Angeles" },
  {
    name: "current_hour_[timezone]",
    description: "Hour as a fraction in an IANA timezone",
  },
  { name: "current_calendar", description: "14-day calendar in America/Los_Angeles" },
  {
    name: "current_calendar_[timezone]",
    description: "14-day calendar in an IANA timezone",
  },
  { name: "session_type", description: "voice or chat" },
  { name: "session_duration", description: "Elapsed time, e.g. 20 minutes 30 seconds" },
  { name: "direction", description: "inbound or outbound (phone calls only)" },
  { name: "user_number", description: "User's phone number (phone calls only)" },
  { name: "agent_number", description: "Agent's phone number (phone calls only)" },
  { name: "call_id", description: "Current call session id" },
  { name: "call_type", description: "web_call or phone_call" },
  { name: "call.call_id", description: "Call record id (all calls)" },
  {
    name: "call.direction",
    description: "Call record direction — web calls always store 'inbound'",
  },
  { name: "call.from_number", description: "Number the call is from" },
  { name: "call.to_number", description: "Number the call is to" },
];

/** Single source for the variable-name grammar shared by the chip
 * highlighter, the picker trigger, and prompt extraction. Inner whitespace
 * is tolerated to match the worker's resolver ({{ name }} == {{name}}). */
export const VARIABLE_NAME_CHARS = "[a-zA-Z0-9_./-]";
export const VARIABLE_PATTERN = `\\{\\{\\s*${VARIABLE_NAME_CHARS}+\\s*\\}\\}`;

const SYSTEM_NAMES = new Set(SYSTEM_VARIABLES.map((v) => v.name));
// The worker resolves these families by prefix (any IANA timezone suffix).
const SYSTEM_PREFIXES = ["current_time_", "current_hour_", "current_calendar_"];

/** Mirrors the worker's _system_value rule: exact names plus the
 * timezone-suffixed families. */
export function isSystemVariable(name: string): boolean {
  return (
    SYSTEM_NAMES.has(name) ||
    SYSTEM_PREFIXES.some((p) => name.startsWith(p) && name.length > p.length)
  );
}

/** Distinct non-system {{variables}} referenced in a prompt. */
export function promptVariables(prompt: string): string[] {
  const names = new Set<string>();
  const re = new RegExp(`\\{\\{\\s*(${VARIABLE_NAME_CHARS}+)\\s*\\}\\}`, "g");
  for (const m of prompt.matchAll(re)) {
    if (!isSystemVariable(m[1])) names.add(m[1]);
  }
  return [...names].sort();
}
