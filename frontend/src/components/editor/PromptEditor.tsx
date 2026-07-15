"use client";

import { type KeyboardEvent, useLayoutEffect, useMemo, useRef, useState } from "react";

import {
  promptVariables,
  SYSTEM_VARIABLES,
  VARIABLE_NAME_CHARS,
  VARIABLE_PATTERN,
} from "./systemVariables";

const VARIABLE_CHIP = new RegExp(`(${VARIABLE_PATTERN})`, "g");
// Open "{{name-so-far" immediately before the caret.
const TRIGGER = new RegExp(`\\{\\{\\s*(${VARIABLE_NAME_CHARS}*)$`);
// Remainder of a variable the caret sits inside: trailing name chars plus
// up to two closing braces — consumed on insert so completion replaces the
// whole token instead of splicing into it.
const TOKEN_TAIL = new RegExp(`^${VARIABLE_NAME_CHARS}*\\s*\\}{0,2}`);
const TIMEZONE_TOKEN = "[timezone]";

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
  defaultDynamicVariables,
}: {
  value: string;
  onChange: (v: string) => void;
  /** The LLM's default_dynamic_variables — its keys become "Agent" group
   * completions. Passed as the (referentially stable) object so the items
   * memo isn't defeated by a fresh array built on every parent render. */
  defaultDynamicVariables?: Record<string, unknown> | null;
}) {
  const backdropRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [menu, setMenu] = useState<Menu | null>(null);
  // Caret/selection to apply after the next value commit. A layout effect
  // (not requestAnimationFrame) so it runs deterministically even in
  // background/frame-throttled tabs.
  const pendingSelection = useRef<[number, number] | null>(null);

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (!el || !pendingSelection.current) return;
    const [selStart, selEnd] = pendingSelection.current;
    pendingSelection.current = null;
    el.focus();
    el.setSelectionRange(selStart, selEnd);
  }, [value]);

  const menuOpen = menu !== null;
  const items = useMemo<Item[]>(() => {
    if (!menuOpen) return []; // only computed while the picker is showing
    const agent = new Set([
      ...Object.keys(defaultDynamicVariables ?? {}),
      ...promptVariables(value),
    ]);
    return [
      ...[...agent].sort().map((name): Item => ({ name, group: "Agent" })),
      ...SYSTEM_VARIABLES.map(
        (v): Item => ({ name: v.name, description: v.description, group: "System" }),
      ),
    ];
  }, [menuOpen, value, defaultDynamicVariables]);

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
    const match = TRIGGER.exec(before);
    if (!match) return setMenu(null);
    const start = el.selectionStart - match[1].length;
    if (menu && menu.start === start) {
      // Same trigger position: reuse the measured coords instead of
      // rebuilding the DOM mirror on every keystroke of the query.
      setMenu({ ...menu, query: match[1], index: 0 });
      return;
    }
    const { top, left } = caretCoords(el, start);
    // keep the 288px-wide dropdown inside the editor
    const clampedLeft = Math.max(0, Math.min(left, el.clientWidth - 296));
    setMenu({ start, query: match[1], top, left: clampedLeft, index: 0 });
  };

  const insert = (name: string) => {
    if (!menu) return;
    const end = menu.start + menu.query.length;
    // Consume the rest of any variable the caret sits inside (remaining
    // name chars + existing closing braces) so completion replaces the
    // whole token — never "{{customer_name}}_name}}" splices.
    const tail = TOKEN_TAIL.exec(value.slice(end))?.[0] ?? "";
    const tokenAt = name.indexOf(TIMEZONE_TOKEN);
    pendingSelection.current =
      tokenAt >= 0
        ? // Placeholder entries like current_time_[timezone]: select the
          // token so the author overtypes a real IANA zone immediately.
          [menu.start + tokenAt, menu.start + tokenAt + TIMEZONE_TOKEN.length]
        : [menu.start + name.length + 2, menu.start + name.length + 2];
    onChange(value.slice(0, menu.start) + name + "}}" + value.slice(end + tail.length));
    setMenu(null);
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
