/// <reference types="bun" />
import { afterEach, describe, expect, test } from "bun:test";

import { type StreamStatus, subscribeStream } from "@/lib/stream";

/**
 * The SSE reader behind Live Monitoring.
 *
 * The wire format is parsed by hand (EventSource can't carry an Authorization
 * header), so the framing rules are ours to get right: events are separated by
 * a blank line, `:` lines are keepalive pings, and a chunk boundary can fall
 * anywhere — including the middle of a `data:` line.
 */

const realFetch = globalThis.fetch;
afterEach(() => {
  globalThis.fetch = realFetch;
});

/** Serve `chunks` as one streamed response body, then close it. */
function serve(chunks: string[], onRequest?: (init: RequestInit | undefined) => void) {
  globalThis.fetch = (async (_url: string, init?: RequestInit) => {
    onRequest?.(init);
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    return new Response(body, { status: 200 });
  }) as unknown as typeof fetch;
}

function collect() {
  const events: [string, unknown][] = [];
  const statuses: StreamStatus[] = [];
  let ended = false;
  return {
    events,
    statuses,
    get ended() {
      return ended;
    },
    opts: {
      token: () => "tok",
      onEvent: (event: string, data: unknown) => events.push([event, data]),
      onStatus: (status: StreamStatus) => statuses.push(status),
      onEnd: () => {
        ended = true;
      },
    },
  };
}

const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

describe("subscribeStream", () => {
  test("parses events and skips keepalive comments", async () => {
    serve([
      ": ping\n\n",
      'event: snapshot\ndata: {"calls":[]}\n\n',
      'event: snapshot\ndata: {"calls":[{"call_id":"call_1"}]}\n\n',
    ]);
    const sink = collect();
    const stop = subscribeStream("http://api.test/live-calls/stream", sink.opts);
    await tick();
    stop();

    expect(sink.events).toEqual([
      ["snapshot", { calls: [] }],
      ["snapshot", { calls: [{ call_id: "call_1" }] }],
    ]);
    expect(sink.statuses.slice(0, 2)).toEqual(["connecting", "live"]);
  });

  test("reassembles an event split across chunks", async () => {
    serve(['event: call\nda', 'ta: {"call_id":"call_1","transcr', 'ipt":"hi"}\n\n']);
    const sink = collect();
    const stop = subscribeStream("http://api.test/s", sink.opts);
    await tick();
    stop();

    expect(sink.events).toEqual([["call", { call_id: "call_1", transcript: "hi" }]]);
  });

  test("sends the bearer token on every attempt", async () => {
    let seen: HeadersInit | undefined;
    serve(['event: call\ndata: {}\n\n'], (init) => {
      seen = init?.headers;
    });
    const sink = collect();
    const stop = subscribeStream("http://api.test/s", sink.opts);
    await tick();
    stop();

    expect((seen as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  test("an end event stops the stream instead of reconnecting", async () => {
    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: end\ndata: {"call_id":"c"}\n\n'));
            // Deliberately left open: only the `end` event should close it.
          },
        }),
        { status: 200 },
      );
    }) as unknown as typeof fetch;

    const sink = collect();
    const stop = subscribeStream("http://api.test/s", sink.opts);
    await tick(1200); // longer than the first retry backoff
    stop();

    expect(sink.ended).toBe(true);
    expect(calls).toBe(1);
    expect(sink.statuses.at(-1)).toBe("offline");
  });

  test("reconnects after the connection drops", async () => {
    let calls = 0;
    globalThis.fetch = (async () => {
      calls += 1;
      return new Response(
        new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(new TextEncoder().encode('event: snapshot\ndata: {"n":1}\n\n'));
            controller.close(); // server hung up without an `end`
          },
        }),
        { status: 200 },
      );
    }) as unknown as typeof fetch;

    const sink = collect();
    const stop = subscribeStream("http://api.test/s", sink.opts);
    await tick(1200); // first backoff is 1s
    stop();

    expect(calls).toBeGreaterThan(1);
    expect(sink.events.length).toBeGreaterThan(1);
  });

  test("unsubscribing silences callbacks already in flight", async () => {
    serve(['event: snapshot\ndata: {"n":1}\n\n']);
    const sink = collect();
    const stop = subscribeStream("http://api.test/s", sink.opts);
    stop(); // before the response is even read
    await tick();

    expect(sink.events).toEqual([]);
  });
});
