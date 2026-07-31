import { useEffect, type RefObject } from "react";

/** Calls `onClose` when a mousedown lands outside the referenced element. */
export function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  /**
   * A second element that also counts as "inside". For a panel rendered into
   * `document.body` through a portal: it is inside the component as far as the
   * user is concerned, but `ref` cannot contain it, so without this every
   * click in the panel would close it.
   */
  alsoInside?: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (ref.current?.contains(target) || alsoInside?.current?.contains(target)) return;
      onClose();
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [ref, onClose, alsoInside]);
}
