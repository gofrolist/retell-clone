"use client";

import Button from "@/components/ui/Button";
import { Field, TextInput } from "@/components/ui/Field";
import Modal from "@/components/ui/Modal";
import { useClickOutside } from "@/lib/useClickOutside";
import { FileText, Link2, Plus, Upload } from "lucide-react";
import { useCallback, useRef, useState, type CSSProperties } from "react";

export type PendingSource =
  | { kind: "url"; url: string }
  | { kind: "text"; title: string; text: string }
  | { kind: "file"; file: File };

export const MAX_FILE_MB = 20;
const ACCEPT = ".pdf,.doc,.docx,.txt,.md,.html,.csv";

const MENU_ITEMS = [
  {
    key: "url" as const,
    icon: Link2,
    title: "Add Web Pages",
    subtitle: "Crawl and sync your website",
  },
  {
    key: "file" as const,
    icon: Upload,
    title: "Upload Files",
    subtitle: `File size should be less than ${MAX_FILE_MB}MB`,
  },
  {
    key: "text" as const,
    icon: FileText,
    title: "Add Text",
    subtitle: "Add articles manually",
  },
];

/**
 * Retell-style "+ Add" source menu. Emits normalized PendingSources and
 * never calls the API itself, so the create modal can batch them while the
 * detail view posts immediately.
 */
export default function AddSourceMenu({
  onAdd,
  onError,
  label = "Add",
}: {
  onAdd: (sources: PendingSource[]) => void;
  onError?: (message: string) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  const [menuPos, setMenuPos] = useState<CSSProperties | null>(null);
  const [panel, setPanel] = useState<"url" | "text" | null>(null);
  const [urlsText, setUrlsText] = useState("");
  const [textTitle, setTextTitle] = useState("");
  const [text, setText] = useState("");
  const menuRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useClickOutside(
    menuRef,
    useCallback(() => setOpen(false), []),
  );

  function pick(key: "url" | "file" | "text") {
    setOpen(false);
    if (key === "file") fileRef.current?.click();
    else setPanel(key);
  }

  function closePanel() {
    setUrlsText("");
    setTextTitle("");
    setText("");
    setPanel(null);
  }

  function onFiles(list: FileList | null) {
    if (!list?.length) return;
    const files = Array.from(list);
    const oversized = files.filter((f) => f.size > MAX_FILE_MB * 1024 * 1024);
    const ok = files.filter((f) => f.size <= MAX_FILE_MB * 1024 * 1024);
    // Emit adds before errors: consumers may clear their error state on add.
    if (ok.length) onAdd(ok.map((file) => ({ kind: "file" as const, file })));
    if (oversized.length) {
      onError?.(
        `${oversized.map((f) => f.name).join(", ")} exceed${oversized.length === 1 ? "s" : ""} the ${MAX_FILE_MB}MB limit.`,
      );
    }
    if (fileRef.current) fileRef.current.value = "";
  }

  function submitUrls() {
    const urls = urlsText
      .split("\n")
      .map((u) => u.trim())
      .filter(Boolean);
    if (!urls.length) return;
    onAdd(urls.map((url) => ({ kind: "url" as const, url })));
    closePanel();
  }

  function submitText() {
    if (!textTitle.trim() || !text.trim()) return;
    onAdd([{ kind: "text", title: textTitle.trim(), text: text.trim() }]);
    closePanel();
  }

  return (
    <div ref={menuRef} className="relative inline-block">
      <Button
        size="sm"
        onClick={(e) => {
          if (!open) {
            // Fixed positioning escapes overflow-hidden ancestors (e.g. the
            // create-KB modal body), unlike absolute positioning.
            const rect = e.currentTarget.getBoundingClientRect();
            const estimatedMenuHeight = 200;
            const openUp =
              rect.bottom + 4 + estimatedMenuHeight > window.innerHeight;
            setMenuPos(
              openUp
                ? {
                    top: rect.top - 4,
                    left: rect.left,
                    transform: "translateY(-100%)",
                  }
                : { top: rect.bottom + 4, left: rect.left },
            );
          }
          setOpen((v) => !v);
        }}
      >
        <Plus className="size-3.5" /> {label}
      </Button>
      <input
        ref={fileRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => onFiles(e.target.files)}
      />
      {open && menuPos && (
        <div
          style={menuPos}
          className="fixed z-[60] w-72 rounded-xl border border-line bg-white p-1.5 shadow-lg"
        >
          {MENU_ITEMS.map(({ key, icon: Icon, title, subtitle }) => (
            <button
              key={key}
              onClick={() => pick(key)}
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left hover:bg-app cursor-pointer"
            >
              <span className="flex size-9 shrink-0 items-center justify-center rounded-full border border-line text-sub">
                <Icon className="size-4" strokeWidth={1.8} />
              </span>
              <span>
                <span className="block text-[13.5px] font-medium">{title}</span>
                <span className="block text-xs text-sub">{subtitle}</span>
              </span>
            </button>
          ))}
        </div>
      )}

      <Modal
        open={panel === "url"}
        onClose={closePanel}
        title="Add Web Pages"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={closePanel}>
              Cancel
            </Button>
            <Button variant="primary" disabled={!urlsText.trim()} onClick={submitUrls}>
              Add
            </Button>
          </>
        }
      >
        <Field label="URLs" hint="One URL per line.">
          <textarea
            value={urlsText}
            onChange={(e) => setUrlsText(e.target.value)}
            rows={4}
            placeholder={"https://example.com/docs\nhttps://example.com/faq"}
            autoFocus
            className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
          />
        </Field>
      </Modal>

      <Modal
        open={panel === "text"}
        onClose={closePanel}
        title="Add Text"
        width="max-w-md"
        footer={
          <>
            <Button variant="ghost" onClick={closePanel}>
              Cancel
            </Button>
            <Button
              variant="primary"
              disabled={!textTitle.trim() || !text.trim()}
              onClick={submitText}
            >
              Add
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Title">
            <TextInput
              value={textTitle}
              onChange={(e) => setTextTitle(e.target.value)}
              placeholder="e.g. Refund policy"
              autoFocus
            />
          </Field>
          <Field label="Text">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={6}
              placeholder="Paste the content here…"
              className="w-full rounded-lg border border-line bg-white px-3 py-2 text-[13px] outline-none transition-colors focus:border-accent focus:ring-2 focus:ring-accent/15"
            />
          </Field>
        </div>
      </Modal>
    </div>
  );
}
