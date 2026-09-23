/**
 * An activity indicator that is decorative only: it is always accompanied by
 * real text, and `motion-safe` means someone who asked for less motion gets a
 * static ring rather than a frozen fragment of an animation.
 */
export function Spinner() {
  return (
    <span
      aria-hidden="true"
      className="inline-block size-4 shrink-0 rounded-full border-2 border-border-subtle border-t-accent motion-safe:animate-spin"
    />
  );
}
