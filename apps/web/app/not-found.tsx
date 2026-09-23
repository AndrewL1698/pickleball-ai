import Link from "next/link";

export default function NotFound() {
  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold tracking-tight">Page not found</h1>
      <p className="text-muted">That page does not exist.</p>
      <Link
        href="/"
        className="focus-ring inline-flex min-h-11 items-center rounded-lg border border-border-subtle px-4 font-medium"
      >
        Go to upload
      </Link>
    </div>
  );
}
