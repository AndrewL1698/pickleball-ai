import type { Metadata } from "next";
import { CalibrationView } from "@/components/CalibrationView";

export const metadata: Metadata = {
  title: "Calibrate court",
};

/** `params` is a Promise in Next 16; see the match page. */
export default async function CalibratePage(props: PageProps<"/matches/[matchId]/calibrate">) {
  const { matchId } = await props.params;
  return <CalibrationView matchId={matchId} />;
}
