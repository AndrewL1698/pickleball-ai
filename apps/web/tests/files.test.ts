/** Client-side upload validation. */

import { describe, expect, it } from "vitest";
import { MAX_UPLOAD_BYTES, formatBytes, validateVideoFile } from "@/lib/files";
import { videoFile } from "./fixtures";

describe("validateVideoFile", () => {
  it.each([["match.mp4"], ["clip.MOV"], ["game.m4v"], ["UPPER.MP4"]])(
    "accepts %s",
    (name) => {
      expect(validateVideoFile(videoFile(name))).toBeNull();
    },
  );

  it.each([["clip.avi"], ["notes.txt"], ["movie.mkv"], ["noextension"], ["match.mp4.txt"]])(
    "rejects %s and names the supported formats",
    (name) => {
      const problem = validateVideoFile(videoFile(name));
      expect(problem).toContain(name);
      expect(problem).toContain(".mp4");
    },
  );

  it("rejects a file over the limit, quoting both sizes", () => {
    const problem = validateVideoFile(videoFile("huge.mp4", MAX_UPLOAD_BYTES + 1));
    expect(problem).toMatch(/limit is/);
    expect(problem).toContain("huge.mp4");
  });

  it("accepts a file of exactly the limit, matching the server's strict comparison", () => {
    expect(validateVideoFile(videoFile("exact.mp4", MAX_UPLOAD_BYTES))).toBeNull();
  });

  it("rejects an empty file", () => {
    expect(validateVideoFile(videoFile("empty.mp4", 0))).toBe("That file is empty.");
  });

  it("asks for a file when none was chosen", () => {
    expect(validateVideoFile(null)).toBe("Choose a video file to upload.");
  });
});

describe("formatBytes", () => {
  it.each([
    [512, "512 B"],
    [1024, "1.0 KiB"],
    [1536, "1.5 KiB"],
    [1024 * 1024 * 3.5, "3.5 MiB"],
    [2 * 1024 * 1024 * 1024, "2.0 GiB"],
    [1024 * 1024 * 20, "20 MiB"],
  ])("renders %i as %s", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });
});
