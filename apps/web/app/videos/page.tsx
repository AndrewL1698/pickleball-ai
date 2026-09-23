import type { Metadata } from "next";
import { VideoListView } from "@/components/VideoListView";

export const metadata: Metadata = {
  title: "Videos",
};

export default function VideosPage() {
  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Videos</h1>
        <p className="mt-2 text-muted">Everything uploaded so far, newest first.</p>
      </header>
      <VideoListView />
    </div>
  );
}
