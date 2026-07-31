"use client";

import CopyId from "@/components/ui/CopyId";
import HoverCard from "@/components/ui/HoverCard";
import type { RawLlm } from "@/lib/api";
import type { ReactNode } from "react";
import {
  type Estimate,
  estimateCost,
  estimateLatency,
  estimateTokens,
  formatTokenValue,
  formatUsdPerMin,
  TOKEN_WARNING_THRESHOLD,
} from "@/lib/estimates";

const msRange = (min: number, max: number) =>
  `${Math.round(min)}-${Math.round(max)}ms`;
const tokenRange = (min: number, max: number) =>
  min === max
    ? formatTokenValue(max)
    : `${formatTokenValue(min)} - ${formatTokenValue(max)}`;

function Headline({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-app px-3 py-2 text-left">
      <div className="text-[11px] text-sub">{label}</div>
      <div className="text-[15px] font-semibold text-ink">{value}</div>
    </div>
  );
}

function Rows({
  estimate,
  format,
}: {
  estimate: Estimate;
  format: (min: number, max: number) => string;
}) {
  return (
    <div className="mt-2 space-y-1.5 border-t border-dashed border-line px-1 pb-1 pt-2">
      {estimate.rows.map((row) => (
        <div
          key={row.label}
          className="flex items-center justify-between gap-4 text-[12px]"
        >
          <span className="text-sub">{row.label}</span>
          <span className="font-medium text-ink">{format(row.min, row.max)}</span>
        </div>
      ))}
    </div>
  );
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span>
      {label}{" "}
      <span className="cursor-default underline decoration-dotted underline-offset-2">
        {value}
      </span>
    </span>
  );
}

/** One `label … value` line of the stacked card, with the same hover breakdown. */
function StackedRow({
  label,
  value,
  children,
}: {
  label: string;
  value: string;
  children: ReactNode;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2 text-[12px]">
      <span className="text-sub">{label}</span>
      {/* Opens upward: this card is pinned to the bottom of the flow editor's
          left rail, so a downward panel would fall off the viewport. */}
      <HoverCard
        placement="top"
        trigger={
          <span className="cursor-default font-medium text-ink underline decoration-dotted underline-offset-2">
            {value}
          </span>
        }
      >
        {children}
      </HoverCard>
    </div>
  );
}

export default function MetaRow({
  agentId,
  llm,
  chat = false,
  stacked = false,
}: {
  agentId: string;
  llm: RawLlm | null;
  /** Chat agent: cost-per-minute and voice latency don't apply to text. */
  chat?: boolean;
  /**
   * Render as a vertical card instead of a one-line row. The flow editor pins
   * this to the bottom of its node rail, where there is no width for chips;
   * the prompt editor keeps the row above its top selectors.
   */
  stacked?: boolean;
}) {
  const tokens = estimateTokens(llm);
  const cost = estimateCost(llm, tokens);
  const latency = estimateLatency(llm);
  // Sum the rows as displayed (each rounded to $0.001) so the breakdown
  // always adds up to the total shown on screen.
  const costTotal = cost.rows.reduce((s, r) => s + Number(r.max.toFixed(3)), 0);

  if (stacked) {
    return (
      <div className="space-y-1.5 rounded-lg border border-line bg-app px-2.5 py-2">
        <div className="flex items-center justify-between gap-2">
          <span className="text-[11px] font-semibold tracking-wide text-faint uppercase">
            Agent details
          </span>
          <CopyId value={agentId} display="ID" />
        </div>
        {!chat && (
          <>
            <StackedRow label="Cost" value={formatUsdPerMin(costTotal)}>
              <Headline label="Estimated Cost per Minute" value={formatUsdPerMin(costTotal)} />
              <Rows estimate={cost} format={(_, max) => formatUsdPerMin(max)} />
            </StackedRow>
            <StackedRow label="Latency" value={msRange(latency.min, latency.max)}>
              <Headline label="Estimated Latency" value={msRange(latency.min, latency.max)} />
              <Rows estimate={latency} format={msRange} />
            </StackedRow>
          </>
        )}
        {tokens && (
          <StackedRow label="Tokens" value={tokenRange(tokens.min, tokens.max)}>
            <Headline
              label="Estimated Tokens"
              value={`${tokens.min.toLocaleString("en-US")}–${tokens.max.toLocaleString("en-US")} tokens`}
            />
            {tokens.max > TOKEN_WARNING_THRESHOLD && (
              <p className="mt-2 px-1 text-[12px] font-medium text-amber-600">
                Estimated context exceeding {TOKEN_WARNING_THRESHOLD.toLocaleString("en-US")} tokens
                significantly increases hallucination risk.
              </p>
            )}
            <Rows estimate={tokens} format={tokenRange} />
          </StackedRow>
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-sub">
      <span className="font-medium text-ink">Agent Details</span>
      {!chat && (
        <>
          <HoverCard trigger={<Chip label="Cost" value={formatUsdPerMin(costTotal)} />}>
            <Headline label="Estimated Cost per Minute" value={formatUsdPerMin(costTotal)} />
            <Rows estimate={cost} format={(_, max) => formatUsdPerMin(max)} />
          </HoverCard>
          <span aria-hidden>·</span>
          <HoverCard
            trigger={<Chip label="Latency" value={msRange(latency.min, latency.max)} />}
          >
            <Headline
              label="Estimated Latency"
              value={msRange(latency.min, latency.max)}
            />
            <Rows estimate={latency} format={msRange} />
          </HoverCard>
        </>
      )}
      {tokens ? (
        <>
          <span aria-hidden>·</span>
          <HoverCard
            trigger={<Chip label="Tokens" value={tokenRange(tokens.min, tokens.max)} />}
          >
            <Headline
              label="Estimated Tokens"
              value={`${tokens.min.toLocaleString("en-US")}–${tokens.max.toLocaleString("en-US")} tokens`}
            />
            {tokens.max > TOKEN_WARNING_THRESHOLD && (
              <p className="mt-2 px-1 text-[12px] font-medium text-amber-600">
                Estimated context exceeding{" "}
                {TOKEN_WARNING_THRESHOLD.toLocaleString("en-US")} tokens
                significantly increases hallucination risk.
              </p>
            )}
            <Rows estimate={tokens} format={tokenRange} />
          </HoverCard>
        </>
      ) : null}
      <span className="ml-auto">
        <CopyId value={agentId} display="ID" />
      </span>
    </div>
  );
}
