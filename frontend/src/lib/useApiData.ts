import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Shared client-side data-fetching hook.
 *
 * Runs `fetcher` once on mount, exposing `{ data, loading, error }` plus a
 * `reload()` that re-runs the fetch (resetting loading + clearing error) — the
 * shape every dashboard list page used to hand-roll.
 *
 * The fetcher is held in a ref so callers can pass an inline arrow
 * (`() => api.listX()`, a new function each render) without the mount effect
 * re-running in a loop: the effect depends only on the stable `reload`.
 *
 * `setData` / `setError` are exposed so pages can apply optimistic list
 * mutations (toggles, delete-from-list) and surface mutation errors through
 * the same error slot, matching the pre-extraction behaviour.
 */
export function useApiData<T>(fetcher: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Synced in an effect, not during render: a render can be discarded (Strict
  // Mode, Suspense, a torn-up concurrent pass), and writing the ref there
  // would publish a fetcher belonging to a render that never committed.
  // Declared before the mount effect below so the ref is current by the time
  // anything reads it — effects run in declaration order.
  const fetcherRef = useRef(fetcher);
  useEffect(() => {
    fetcherRef.current = fetcher;
  });

  const reload = useCallback(async (): Promise<T | undefined> => {
    setLoading(true);
    setError(null);
    try {
      const result = await fetcherRef.current();
      setData(result);
      return result;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
      return undefined;
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, setData, loading, error, setError, reload };
}
