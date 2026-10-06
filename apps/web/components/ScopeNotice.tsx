import { Notice } from "./Notice";

/**
 * What this build does and does not do, on every page where a job's outcome
 * is visible.
 *
 * A green "Ready" badge on a video-analysis product reads as "we analysed your
 * match". Here it means "we read the video's metadata". CLAUDE.md is explicit
 * -- do not hide uncertainty from the frontend -- so this says so plainly and
 * cannot be dismissed.
 */
export function ScopeNotice() {
  return (
    <Notice tone="info" title="This build reads video metadata only.">
      <p>
        Processing extracts the video&rsquo;s resolution, rotation, duration, frame rate and
        codec. Court calibration is the next step for every match, but it is not available here
        yet, and player tracking, ball tracking, rally detection and statistics are not
        implemented.
      </p>
    </Notice>
  );
}
