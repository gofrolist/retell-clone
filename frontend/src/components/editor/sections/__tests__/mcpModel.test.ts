import { describe, expect, test } from "bun:test";
import { buildMcpServer } from "../mcpModel";
import type { McpServer } from "@/lib/api";

describe("buildMcpServer", () => {
  test("a sixth field on the entry being edited survives (Fidelity rule)", () => {
    // RawConversationFlow.mcps is `Record<string, unknown>[]` on the wire and
    // gets cast to `McpServer[]` where the flow path renders McpSection --
    // an entry there can carry a field McpServer's five-key type doesn't
    // know about. Cast the same way the page does, at the call site.
    const original = {
      name: "calendar-tools",
      url: "https://mcp.example.com/sse",
      flex_mode: true,
    } as unknown as McpServer;

    const saved = buildMcpServer(original, {
      name: "calendar-tools-renamed",
      url: "https://mcp.example.com/sse",
    });

    expect((saved as unknown as Record<string, unknown>).flex_mode).toBe(true);
    expect(saved.name).toBe("calendar-tools-renamed");
  });

  test("adding a new server (no original) has no leftover fields", () => {
    const saved = buildMcpServer(undefined, {
      name: "new-server",
      url: "https://mcp.example.com/sse",
    });
    expect(saved).toEqual({ name: "new-server", url: "https://mcp.example.com/sse" });
  });

  test("omits headers/query_params/timeout_ms entirely when not provided, even if the original had them", () => {
    const original: McpServer = {
      name: "a",
      url: "https://a.example.com",
      headers: { Authorization: "Bearer x" },
      query_params: { key: "v" },
      timeout_ms: 5000,
    };
    const saved = buildMcpServer(original, { name: "a", url: "https://a.example.com" });
    expect(saved.headers).toBeUndefined();
    expect(saved.query_params).toBeUndefined();
    expect(saved.timeout_ms).toBeUndefined();
  });

  test("sets headers/query_params/timeout_ms when provided", () => {
    const saved = buildMcpServer(undefined, {
      name: "a",
      url: "https://a.example.com",
      headers: { Authorization: "Bearer x" },
      queryParams: { key: "v" },
      timeoutMs: 8000,
    });
    expect(saved.headers).toEqual({ Authorization: "Bearer x" });
    expect(saved.query_params).toEqual({ key: "v" });
    expect(saved.timeout_ms).toBe(8000);
  });

  test("editing replaces name/url on the original entry without dropping the rest of it", () => {
    const original = {
      name: "old-name",
      url: "https://old.example.com",
      timeout_ms: 3000,
      custom_stored_field: "keep-me",
    } as unknown as McpServer;
    const saved = buildMcpServer(original, {
      name: "new-name",
      url: "https://new.example.com",
      timeoutMs: 3000,
    });
    expect(saved).toEqual({
      name: "new-name",
      url: "https://new.example.com",
      timeout_ms: 3000,
      custom_stored_field: "keep-me",
    } as unknown as McpServer);
  });
});
