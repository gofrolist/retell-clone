import { useEffect, type RefObject } from "react";

/** Calls `onClose` when a mousedown lands outside the referenced element. */
export function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
): void {
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (!ref.current?.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [ref, onClose]);
}
