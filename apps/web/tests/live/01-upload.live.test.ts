// @vitest-environment node
/**
 * The real upload path against the real API.
 *
 * This file runs in a Node environment rather than jsdom on purpose: jsdom
 * supplies its own `File` and `FormData`, which Node's `fetch` does not
 * recognise as a file part, so a multipart upload is serialised as a plain
 * text field and the API answers 422. A real browser has no such problem, so
 * faking one here would test the fake. Node's globals are the closest thing to
 * a browser's that this process can produce.
 *
 * Opt-in; needs the whole stack running. See the README.
 */

import { beforeAll, expect, it } from "vitest";
import { API_BASE_URL, createMatch } from "@/lib/api";

/** A genuine ISO base-media header, which is what the API's sniff looks for. */
function realVideoFile(name = "live-check.mp4"): File {
  const ftyp = new Uint8Array([
    0x00, 0x00, 0x00, 0x20, 0x66, 0x74, 0x79, 0x70, 0x69, 0x73, 0x6f, 0x6d,
    0x00, 0x00, 0x02, 0x00, 0x69, 0x73, 0x6f, 0x6d, 0x69, 0x73, 0x6f, 0x32,
    0x61, 0x76, 0x63, 0x31, 0x6d, 0x70, 0x34, 0x31,
  ]);
  return new File([ftyp, new Uint8Array(4096)], name, { type: "video/mp4" });
}

beforeAll(async () => {
  const response = await fetch(`${API_BASE_URL}/health`).catch(() => null);
  if (response === null || !response.ok) {
    throw new Error(`The API is not running at ${API_BASE_URL}. Start it with: uv run pbapi`);
  }
});

it("uploads a video and gets back a match with its video and a queued job", async () => {
  const created = await createMatch(realVideoFile());

  expect(created.id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-/);
  expect(created.name).toBe("live-check");
  expect(created.status).toBe("uploaded");
  expect(created.video?.original_filename).toBe("live-check.mp4");
  expect(created.video?.content_type).toBe("video/mp4");
  expect(created.video?.byte_size).toBeGreaterThan(0);
  expect(created.jobs).toHaveLength(1);
  expect(created.latest_job?.status).toBe("queued");
  expect(created.latest_job?.match_id).toBe(created.id);
  // The key the file is stored under must never reach the browser.
  expect(JSON.stringify(created)).not.toContain("storage_key");
});

it("is rejected by the server when the bytes are not a video", async () => {
  const notAVideo = new File([new TextEncoder().encode("plain text")], "fake.mp4", {
    type: "video/mp4",
  });
  await expect(createMatch(notAVideo)).rejects.toMatchObject({
    status: 415,
    code: "unsupported_file_type",
  });
});

it("is rejected by the server when the extension is not allowed", async () => {
  await expect(createMatch(realVideoFile("notes.txt"))).rejects.toMatchObject({ status: 415 });
});
