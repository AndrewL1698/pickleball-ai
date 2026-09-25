import { Notice } from "./Notice";

/**
 * The most important text in the application.
 *
 * A green "Ready" badge on a video-analysis product means "we analysed your
 * match". Here it means "we read your file". CLAUDE.md's rule is explicit --
 * do not hide uncertainty from the frontend -- so this says so plainly, on
 * every page where a job's outcome is visible, and cannot be dismissed.
 */
export function PlaceholderNotice() {
  return (
    <Notice tone="info" title="This build does not analyse video yet.">
      <p>
        Processing currently verifies the uploaded file and records a checksum. Court
        calibration is the next step for every match, but it is not available here yet, and
        player tracking, ball tracking, rally detection and statistics are not implemented.
      </p>
    </Notice>
  );
}
