import type { Metadata } from "next";
import { MatchListView } from "@/components/MatchListView";

export const metadata: Metadata = {
  title: "Matches",
};

export default function MatchesPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Matches</h1>
        <p className="mt-2 text-muted">Every uploaded match, newest first.</p>
      </header>
      <MatchListView />
    </div>
  );
}
