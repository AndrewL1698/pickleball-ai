"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Upload" },
  { href: "/matches", label: "Matches" },
] as const;

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="border-b border-border-subtle">
      <div className="mx-auto flex max-w-3xl flex-wrap items-center justify-between gap-2 px-4 py-4">
        <Link href="/" className="focus-ring flex min-h-11 items-center rounded-sm font-semibold tracking-tight">
          Pickleball AI
        </Link>
        <nav aria-label="Main">
          <ul className="flex gap-1">
            {LINKS.map(({ href, label }) => {
              // `/matches/<id>` should still mark Matches as the current section.
              const isCurrent =
                href === "/" ? pathname === "/" : pathname.startsWith(href);
              return (
                <li key={href}>
                  <Link
                    href={href}
                    aria-current={isCurrent ? "page" : undefined}
                    className={`focus-ring flex min-h-11 items-center rounded-md px-3 text-sm font-medium aria-[current=page]:bg-surface aria-[current=page]:underline aria-[current=page]:underline-offset-4 ${
                      isCurrent ? "text-foreground" : "text-muted hover:text-foreground"
                    }`}
                  >
                    {label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </div>
    </header>
  );
}
