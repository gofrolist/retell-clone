/// <reference types="bun" />
import { describe, expect, spyOn, test } from "bun:test";
import { renderHook, waitFor } from "@testing-library/react";

import { api } from "@/lib/api";
import { FALLBACK_PRICES } from "@/lib/estimates";
import { usePricing } from "@/lib/usePricing";

/**
 * The model picker needs a price on its very first render, so this hook must
 * never return null or a loading state: it returns the compiled-in fallback
 * immediately and swaps in the live card only once the fetch resolves — and
 * keeps the fallback silently if the fetch never does.
 */
describe("usePricing", () => {
  test("returns the fallback before the fetch resolves", () => {
    // Never resolves within the test: proves the synchronous first render,
    // not a fetch that happens to be fast. Also keeps this test from making
    // a real network call.
    const spy = spyOn(api, "getPricing").mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => usePricing());
    expect(result.current).toBe(FALLBACK_PRICES);
    spy.mockRestore();
  });

  test("keeps the fallback when the fetch fails", async () => {
    const spy = spyOn(api, "getPricing").mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => usePricing());
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(result.current).toBe(FALLBACK_PRICES);
    spy.mockRestore();
  });

  test("swaps in the fetched card once it resolves", async () => {
    const live = { ...FALLBACK_PRICES, unpriced: ["some-model"] };
    const spy = spyOn(api, "getPricing").mockResolvedValue(live);
    const { result } = renderHook(() => usePricing());
    await waitFor(() => expect(result.current).toBe(live));
    spy.mockRestore();
  });
});
