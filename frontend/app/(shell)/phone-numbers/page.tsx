"use client";

import BuyNumberModal from "@/components/phone/BuyNumberModal";
import PhoneDetail from "@/components/phone/PhoneDetail";
import SecondaryPanel from "@/components/shell/SecondaryPanel";
import EmptyState from "@/components/ui/EmptyState";
import SearchInput from "@/components/ui/SearchInput";
import { api } from "@/lib/api";
import type { Agent, PhoneNumber } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Phone, Plus } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

export default function PhoneNumbersPage() {
  const [numbers, setNumbers] = useState<PhoneNumber[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [buyOpen, setBuyOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const list = await api.listPhoneNumbers();
    setNumbers(list);
    return list;
  }, []);

  useEffect(() => {
    refresh()
      .then((list) => {
        setSelected((s) => s ?? list[0]?.phone_number ?? null);
        setError(null);
      })
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : "Failed to load phone numbers"),
      )
      .finally(() => setLoading(false));
    api.listAgents().then(setAgents).catch(() => setAgents([]));
  }, [refresh]);

  const phone = numbers.find((n) => n.phone_number === selected);
  const filtered = numbers.filter((n) =>
    (n.nickname ?? n.phone_number).toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <SecondaryPanel
      panel={
        <div className="p-3">
          <div className="mb-2 flex items-center justify-between px-2 pt-1">
            <span className="inline-flex items-center gap-2 text-[13.5px] font-semibold">
              <Phone className="size-4 text-sub" strokeWidth={1.8} />
              Phone Numbers
            </span>
            <button
              onClick={() => setBuyOpen(true)}
              className="flex size-6 items-center justify-center rounded-md bg-ink text-white hover:bg-black/80 cursor-pointer"
              aria-label="Connect phone number"
            >
              <Plus className="size-3.5" />
            </button>
          </div>
          <SearchInput
            value={query}
            onChange={setQuery}
            placeholder="Search phone numbers"
            className="mb-2"
          />
          {loading && <p className="px-3 py-2 text-[13px] text-sub">Loading…</p>}
          {error && <p className="px-3 py-2 text-[13px] text-bad">{error}</p>}
          <div className="space-y-0.5">
            {filtered.map((n) => (
              <button
                key={n.phone_number}
                onClick={() => setSelected(n.phone_number)}
                className={cn(
                  "flex w-full items-center rounded-lg px-3 py-2 text-left text-[13.5px] transition-colors cursor-pointer",
                  selected === n.phone_number
                    ? "bg-white font-medium shadow-sm border border-line"
                    : "text-sub hover:bg-black/4 hover:text-ink border border-transparent",
                )}
              >
                <span className="truncate">{n.nickname ?? n.phone_number}</span>
              </button>
            ))}
          </div>
        </div>
      }
    >
      {phone ? (
        <PhoneDetail
          phone={phone}
          agents={agents}
          onChanged={() => {
            refresh().catch(() => {});
          }}
          onDeleted={() => {
            refresh()
              .then((list) => setSelected(list[0]?.phone_number ?? null))
              .catch(() => {});
          }}
        />
      ) : (
        <EmptyState
          icon={Phone}
          title="No phone numbers"
          description="Connect a number you own to start taking calls."
        />
      )}
      <BuyNumberModal
        open={buyOpen}
        onClose={() => setBuyOpen(false)}
        agents={agents}
        onCreated={(num) => {
          setBuyOpen(false);
          refresh()
            .then(() => setSelected(num))
            .catch(() => {});
        }}
      />
    </SecondaryPanel>
  );
}
