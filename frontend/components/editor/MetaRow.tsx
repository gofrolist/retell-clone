"use client";

import CopyId from "@/components/ui/CopyId";

export default function MetaRow({ agentId }: { agentId: string }) {
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-sub">
      <span className="font-medium text-ink">Agent Details</span>
      <span>
        Cost{" "}
        <span className="underline decoration-dotted underline-offset-2" title="Estimate">
          $0.120/min
        </span>
      </span>
      <span aria-hidden>·</span>
      <span>
        Latency{" "}
        <span className="underline decoration-dotted underline-offset-2" title="Estimate">
          800-1200ms
        </span>
      </span>
      <span className="ml-auto">
        <CopyId value={agentId} display="ID" />
      </span>
    </div>
  );
}
