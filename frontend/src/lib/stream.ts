// Server-Sent Events client for the backend's Live Monitoring streams.
//
// Not `EventSource`: that API can't send an Authorization header, and the only
// ways around it are a token in the query string (which lands in every access
// log) or a cookie the API doesn't use. `fetch` + a ReadableStream reader
// carries the same bearer token as every other request, and gives us abort and
// reconnect control on top.
//
// Deliberately knows nothing about the API client — the caller passes an
// absolute URL and a token getter — so `lib/api.ts` can own it without the two
// modules importing each other.

export type StreamStatus = "connecting" | "live" | "offline";

export interface StreamOptions<T> {
  /** Fired per SSE event; `event` is the server's event name. */
  onEvent: (event: string, data: T) => void;
  onStatus?: (status: StreamStatus) => void;
  /** Fired when the server closes the stream on purpose (`event: end`). */
  onEnd?: () => void;
  /** Read per connection attempt, so a refreshed session token is picked up. */
  token: () => string | undefined;
}

const FIRST_RETRY_MS = 1_000;
const MAX_RETRY_MS = 15_000;

/**
 * Subscribe to an SSE endpoint until the returned function is called.
 *
 * Reconnects with exponential backoff on any drop. The backend caps a stream
 * at 30 minutes (so no proxy timeout is ever what cuts it) and re-sends
 * current state on connect, so a reconnect is routine and loses nothing.
 */
export function subscribeStream<T = unknown>(url: string, opts: StreamOptions<T>): () => void {
  const controller = new AbortController();
  let retryMs = FIRST_RETRY_MS;
  let stopped = false;

  // Every callback goes through here: once unsubscribed nothing fires again,
  // so an in-flight read can't setState on an unmounted component.
  const emit = {
    event: (event: string, data: T) => !stopped && opts.onEvent(event, data),
    status: (status: StreamStatus) => !stopped && opts.onStatus?.(status),
    end: () => !stopped && opts.onEnd?.(),
  };

  async function connect() {
    while (!stopped) {
      emit.status("connecting");
      try {
        const token = opts.token();
        const res = await fetch(url, {
          headers: {
            Accept: "text/event-stream",
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`stream failed: ${res.status}`);

        // "live" waits for a real event, not just the 200. A buffering proxy
        // answers with a perfectly good text/event-stream it never flushes —
        // promoting on the response would tell the page to stop its fallback
        // poll in precisely the case the fallback exists for. Both streams
        // send their current state on connect, so a healthy one promotes
        // immediately.
        const ended = await readEvents(res.body, (event, data) => {
          emit.status("live");
          retryMs = FIRST_RETRY_MS;
          if (event === "end") emit.end();
          else emit.event(event, data as T);
        });
        if (ended) {
          // Server said `end` — what we were watching is over. Don't
          // reconnect; the caller decides what happens next.
          emit.status("offline");
          return;
        }
      } catch {
        if (stopped) return;
        emit.status("offline");
      }
      if (stopped) return;
      await sleep(retryMs);
      retryMs = Math.min(retryMs * 2, MAX_RETRY_MS);
    }
  }

  connect();

  return () => {
    stopped = true;
    controller.abort();
  };
}

/**
 * Read an SSE body to completion. Resolves true if the server sent an `end`
 * event (a deliberate close), false if the stream simply ran out.
 */
async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: string, data: unknown) => void,
): Promise<boolean> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  for (;;) {
    const { done, value } = await reader.read();
    if (done) return false;
    buffer += decoder.decode(value, { stream: true });

    // Events are separated by a blank line; whatever follows the last one is a
    // partial event still arriving.
    let split: number;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      const parsed = parseBlock(block);
      if (!parsed) continue; // keepalive comment or malformed block
      onEvent(parsed.event, parsed.data);
      if (parsed.event === "end") {
        await reader.cancel().catch(() => {});
        return true;
      }
    }
  }
}

function parseBlock(block: string): { event: string; data: unknown } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith(":")) continue; // keepalive ping
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
