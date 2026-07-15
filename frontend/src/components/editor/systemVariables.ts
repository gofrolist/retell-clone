/** Retell default system variables (docs.retellai.com/build/dynamic-variables)
 * resolved by the worker at call time, plus Architeq call-scoped aliases. */
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
  { name: "call.call_id", description: "Call-scoped alias of the call id" },
  { name: "call.direction", description: "Call-scoped alias of the direction" },
  { name: "call.from_number", description: "Number the call is from" },
  { name: "call.to_number", description: "Number the call is to" },
];

const SYSTEM_NAMES = new Set(SYSTEM_VARIABLES.map((v) => v.name));

/** Distinct non-system {{variables}} referenced in a prompt. */
export function promptVariables(prompt: string): string[] {
  const names = new Set<string>();
  for (const m of prompt.matchAll(/\{\{\s*([a-zA-Z0-9_./-]+)\s*\}\}/g)) {
    if (!SYSTEM_NAMES.has(m[1])) names.add(m[1]);
  }
  return [...names].sort();
}
