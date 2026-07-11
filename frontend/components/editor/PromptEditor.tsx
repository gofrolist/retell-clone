"use client";

import { useRef } from "react";

/** Prompt textarea with {{variable}} chip highlighting via a synced backdrop. */
export default function PromptEditor({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const backdropRef = useRef<HTMLDivElement>(null);

  const syncScroll = (el: HTMLTextAreaElement) => {
    if (backdropRef.current) {
      backdropRef.current.scrollTop = el.scrollTop;
      backdropRef.current.scrollLeft = el.scrollLeft;
    }
  };

  const parts = value.split(/(\{\{[a-zA-Z0-9_.]+\}\})/g);

  const shared =
    "whitespace-pre-wrap break-words font-mono text-[13px] leading-[1.65] p-4";

  return (
    <div className="relative min-h-96 grow overflow-hidden rounded-xl border border-line bg-white focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/15">
      <div
        ref={backdropRef}
        aria-hidden
        className={`pointer-events-none absolute inset-0 overflow-auto text-ink ${shared}`}
      >
        {parts.map((p, i) =>
          /^\{\{[a-zA-Z0-9_.]+\}\}$/.test(p) ? (
            <span
              key={i}
              className="rounded bg-blue-50 px-0.5 font-medium text-accent-deep ring-1 ring-blue-100"
            >
              {p}
            </span>
          ) : (
            <span key={i}>{p}</span>
          ),
        )}
        {"\n"}
      </div>
      <textarea
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          syncScroll(e.target);
        }}
        onScroll={(e) => syncScroll(e.currentTarget)}
        spellCheck={false}
        className={`absolute inset-0 h-full w-full resize-none bg-transparent text-transparent caret-ink outline-none ${shared}`}
      />
    </div>
  );
}
