/**
 * Inline "failed to load" message with a Retry link. Renders the shared inner
 * markup used by list pages — the caller supplies the wrapper element/padding
 * (a `<div>`, `<td>` or `<p>`) so each page's layout stays byte-identical.
 */
export default function LoadError({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <>
      <span className="text-bad">{error}</span>{" "}
      <button
        onClick={onRetry}
        className="font-medium text-accent-deep hover:underline cursor-pointer"
      >
        Retry
      </button>
    </>
  );
}
