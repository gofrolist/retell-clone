import { useEffect, useState } from "react";

import { api } from "@/lib/api";
import { FALLBACK_PRICES } from "@/lib/estimates";
import type { PriceCard } from "@/lib/types";

/**
 * The current price card, with the compiled-in fallback as the initial value.
 *
 * Deliberately not `useApiData`: callers need a card on the very first render
 * to price the model picker, and a null-plus-loading shape would put a spinner
 * or a blank price where a number belongs. A stale price beats no price.
 */
export function usePricing(): PriceCard {
  const [card, setCard] = useState<PriceCard>(FALLBACK_PRICES);

  useEffect(() => {
    let live = true;
    api
      .getPricing()
      .then((fresh) => {
        if (live) setCard(fresh);
      })
      .catch(() => {
        // Keep the fallback. The editor must still estimate offline.
      });
    return () => {
      live = false;
    };
  }, []);

  return card;
}
