"use client";

import { type KeyboardEvent, useMemo, useRef, useState } from "react";

import { promptVariables, SYSTEM_VARIABLES } from "./systemVariables";

const VARIABLE_CHIP = /(\{\{[a-zA-Z0-9_./-]+\}\})/g;

type Menu = {
  /** index in value where the partial variable name starts (after "{{") */
  start: number;
  query: string;
  top: number;
  left: number;
  index: number;
};

type Item = { name: string; description?: string; group: "Agent" | "System" };

/** Pixel position of *pos* inside the textarea, via an off-screen mirror. */
function caretCoords(el: HTMLTextAreaElement, pos: number) {
  const mirror = document.createElement("div");
  const style = getComputedStyle(el);
  for (const prop of [
    "fontFamily",
    "fontSize",
    "fontWeight",
    "lineHeight",
    "letterSpacing",
    "padding",
    "border",
    "boxSizing",
  ] as const) {
    mirror.style[prop] = style[prop];
  }
  mirror.style.position = "absolute";
  mirror.style.visibility = "hidden";
  mirror.style.whiteSpace = "pre-wrap";
  mirror.style.overflowWrap = "break-word";
  mirror.style.width = `${el.clientWidth}px`;
  mirror.textContent = el.value.slice(0, pos);
  const marker = document.createElement("span");
  marker.textContent = "​";
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const lineHeight = parseFloat(style.lineHeight) || 21;
  const coords = {
    top: marker.offsetTop + lineHeight - el.scrollTop,
    left: marker.offsetLeft - el.scrollLeft,
  };
  mirror.remove();
  return coords;
}

/** Prompt textarea with {{variable}} chip highlighting via a synced backdrop
 * and a Retell-style variable picker that opens on typing "{{". */
export default function PromptEditor({
  value,
  onChange,
  agentVariables = [],
}: {
  value: string;
  onChange: (v: string) => void;
  /** Extra completions, e.g. keys of the LLM's default_dynamic_variables. */
  agentVariables?: string[];
}) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [menu, setMenu] = useState<Menu | null>(null);

  const items = useMemo<Item[]>(() => {
    const agent = new Set([...agentVariables, ...promptVariables(value)]);
    return [
      ...[...agent].sort().map((name): Item => ({ name, group: "Agent" })),
      ...SYSTEM_VARIABLES.map(
        (v): Item => ({ name: v.name, description: v.description, group: "System" }),
      ),
    ];
  }, [value, agentVariables]);

  const visible = menu
    ? items.filter((i) => i.name.toLowerCase().includes(menu.query.toLowerCase()))
    : [];

  const syncScroll = (el: HTMLTextAreaElement) => {
    if (backdropRef.current) {
      backdropRef.current.scrollTop = el.scrollTop;
      backdropRef.current.scrollLeft = el.scrollLeft;
    }
  };

  const detectTrigger = (el: HTMLTextAreaElement) => {
    if (el.selectionStart !== el.selectionEnd) return setMenu(null);
    const before = el.value.slice(0, el.selectionStart);
    const match = /\{\{([a-zA-Z0-9_./-]*)$/.exec(before);
    if (!match) return setMenu(null);
    const start = el.selectionStart - match[1].length;
    const { top, left } = caretCoords(el, start);
    // keep the 288px-wide dropdown inside the editor
    const clampedLeft = Math.max(0, Math.min(left, el.clientWidth - 296));
    setMenu({ start, query: match[1], top, left: clampedLeft, index: 0 });
  };

  const insert = (name: string) => {
    const el = textareaRef.current;
    if (!el || !menu) return;
    const end = menu.start + menu.query.length;
    const closing = value.slice(end).startsWith("}}") ? "" : "}}";
    onChange(value.slice(0, menu.start) + name + closing + value.slice(end));
    setMenu(null);
    const caret = menu.start + name.length + 2;
    requestAnimationFrame(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!menu || visible.length === 0) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const delta = e.key === "ArrowDown" ? 1 : -1;
      setMenu({ ...menu, index: (menu.index + delta + visible.length) % visible.length });
    } else if (e.key === "Enter" || e.key === "Tab") {
      e.preventDefault();
      insert(visible[menu.index].name);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setMenu(null);
    }
  };

  const parts = value.split(VARIABLE_CHIP);

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
          i % 2 === 1 ? (
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
        ref={textareaRef}
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          syncScroll(e.target);
          detectTrigger(e.target);
        }}
        onScroll={(e) => syncScroll(e.currentTarget)}
        onKeyDown={onKeyDown}
        onClick={(e) => detectTrigger(e.currentTarget)}
        onBlur={() => setMenu(null)}
        spellCheck={false}
        className={`absolute inset-0 h-full w-full resize-none bg-transparent text-transparent caret-ink outline-none ${shared}`}
      />
      {menu && visible.length > 0 && (
        <div
          role="listbox"
          className="absolute z-20 max-h-64 w-72 overflow-y-auto rounded-lg border border-line bg-white py-1 shadow-lg"
          style={{ top: menu.top, left: menu.left }}
          // keep textarea focus while clicking options
          onMouseDown={(e) => e.preventDefault()}
        >
          {visible.map((item, i) => (
            <div key={item.name}>
              {(i === 0 || visible[i - 1].group !== item.group) && (
                <div className="px-3 pb-0.5 pt-1.5 text-[11px] font-medium uppercase tracking-wide text-sub">
                  {item.group}
                </div>
              )}
              <button
                type="button"
                role="option"
                aria-selected={i === menu.index}
                onClick={() => insert(item.name)}
                onMouseEnter={() => setMenu({ ...menu, index: i })}
                className={`block w-full px-3 py-1.5 text-left text-[13px] text-ink ${
                  i === menu.index ? "bg-blue-50" : ""
                }`}
              >
                <span className="font-mono">{item.name}</span>
                {item.description && (
                  <span className="mt-0.5 block text-[11px] leading-tight text-sub">
                    {item.description}
                  </span>
                )}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
