import { Spinner } from "./Spinner";

/**
 * "Fetch it again."
 *
 * Shared because the accessibility decisions in it are easy to get subtly
 * different in a second copy: `aria-disabled` rather than `disabled`, because
 * a focused element that becomes disabled drops focus to the body; the guard
 * repeated in the handler, because `aria-disabled` does not stop a click; and
 * the 44px minimum target.
 */
export function RefreshButton({
  label,
  isRefreshing,
  onRefresh,
}: {
  label: string;
  isRefreshing: boolean;
  onRefresh: () => void;
}) {
  return (
    <button
      type="button"
      aria-disabled={isRefreshing}
      onClick={() => {
        if (!isRefreshing) onRefresh();
      }}
      className="focus-ring inline-flex min-h-11 items-center gap-2 rounded-lg border border-border-subtle px-4 font-medium aria-disabled:cursor-default aria-disabled:opacity-60"
    >
      {isRefreshing ? <Spinner /> : null}
      {label}
    </button>
  );
}
