import type { McpServer } from "@/lib/api";

/** The parsed, validated values `McpSection`'s form collects before a save. */
export interface McpServerFormValues {
  name: string;
  url: string;
  headers?: Record<string, string>;
  queryParams?: Record<string, string>;
  timeoutMs?: number;
}

/**
 * Builds the `McpServer` object `McpSection.save()` writes back, spreading
 * *original* (the entry being edited, or `undefined` when adding a new one)
 * so unknown stored fields survive an edit — the Fidelity rule. Mirrors
 * `FunctionsSection.tsx`'s tool-save pattern ("Spread the original entry so
 * unknown stored fields survive an edit").
 *
 * `McpServer` is typed with exactly five known fields, but the flow path
 * (`RawConversationFlow.mcps`) is `Record<string, unknown>[]` on the wire and
 * gets cast to `McpServer[]` at the call site for this shared component —
 * an entry there can carry a field this type doesn't know about, and without
 * spreading it, opening and re-saving that entry silently drops it.
 */
export function buildMcpServer(
  original: McpServer | undefined,
  values: McpServerFormValues,
): McpServer {
  const server: McpServer = {
    ...original,
    name: values.name,
    url: values.url,
  };
  // Empty optional groups are omitted from the wire shape entirely, same as
  // FunctionsSection's tool save.
  delete server.headers;
  delete server.query_params;
  delete server.timeout_ms;
  if (values.headers) server.headers = values.headers;
  if (values.queryParams) server.query_params = values.queryParams;
  if (values.timeoutMs !== undefined) server.timeout_ms = values.timeoutMs;
  return server;
}
