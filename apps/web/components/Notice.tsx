import type { ReactNode } from "react";

type Tone = "info" | "error";

const TONES: Record<Tone, string> = {
  info: "border-border-subtle bg-surface text-foreground",
  error:
    "border-red-300 bg-red-50 text-red-900 dark:border-red-400/40 dark:bg-red-400/10 dark:text-red-100",
};

/**
 * A boxed message.
 *
 * `role` is the caller's choice on purpose: a problem the person just caused
 * by pressing a button should not interrupt them (they are looking at it), but
 * one that appeared on its own -- a background refresh failing -- has to
 * announce itself. Passing "alert" is how a caller says which it is.
 */
export function Notice({
  tone = "info",
  title,
  role,
  code,
  children,
}: {
  tone?: Tone;
  title?: string;
  role?: "alert" | "status";
  /** A machine-readable reason, shown small and last. */
  code?: string | null;
  children: ReactNode;
}) {
  return (
    <div role={role} className={`rounded-lg border px-4 py-3 text-sm ${TONES[tone]}`}>
      {title ? <p className="font-semibold">{title}</p> : null}
      <div className={title ? "mt-1" : undefined}>{children}</div>
      {code ? <p className="mt-1 text-xs opacity-80">Error code: {code}</p> : null}
    </div>
  );
}
