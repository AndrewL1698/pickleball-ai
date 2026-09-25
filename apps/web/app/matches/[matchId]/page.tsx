import type { Metadata } from "next";
import { MatchDetailView } from "@/components/MatchDetailView";

export const metadata: Metadata = {
  title: "Match",
};

/**
 * `params` is a Promise in Next 16 and must be awaited; synchronous access was
 * removed, not just deprecated. `PageProps` is a generated global, so it needs
 * no import.
 */
export default async function MatchPage(props: PageProps<"/matches/[matchId]">) {
  const { matchId } = await props.params;
  return <MatchDetailView matchId={matchId} />;
}
