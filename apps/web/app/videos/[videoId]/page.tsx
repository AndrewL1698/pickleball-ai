import type { Metadata } from "next";
import { VideoStatusView } from "@/components/VideoStatusView";

export const metadata: Metadata = {
  title: "Processing job",
};

/**
 * `params` is a Promise in Next 16 and must be awaited; synchronous access was
 * removed, not just deprecated. `PageProps` is a generated global, so it needs
 * no import.
 */
export default async function VideoPage(props: PageProps<"/videos/[videoId]">) {
  const { videoId } = await props.params;
  return <VideoStatusView videoId={videoId} />;
}
