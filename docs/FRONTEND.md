# Frontend

The Next.js app added in Phase 1 checkpoint 2, moved from "Videos" to
"Matches" in Phase 2 checkpoint 1, showing decoded video metadata since
checkpoint 2, and calibrating the court since checkpoint 5. It covers uploading a
match's video, following the processing job that results, reading what it found,
and calibrating the court. The backend it talks to is described in `BACKEND.md`.

## What It Does and Does Not Do

It does: take a video by file picker or drag-and-drop, validate it before and
after submission, upload it as a new match, list every match, and follow one
match's status and its job through `queued`, `running`, `ready` and `failed` —
polling only while the answer can still change, and surviving a refresh or a
revisit because the server is the only source of truth — show the video
metadata the worker decoded, start a new job where the API would accept one, and
calibrate the court on a still from the video.

It does not play video or show analysis results. Nothing serves the uploaded file
back, and processing reads the video without analysing it. The UI says so, on the
page, in as many words.

## Structure

```text
apps/web/
├── app/
│   ├── layout.tsx              # shell: skip link, nav, main landmark, footer
│   ├── globals.css             # Tailwind v4 import, colour tokens, focus ring
│   ├── page.tsx                # "/" upload page
│   ├── matches/page.tsx        # "/matches" list
│   ├── matches/[matchId]/page.tsx # "/matches/:id" match status, awaits `params`
│   ├── matches/[matchId]/calibrate/page.tsx # "/matches/:id/calibrate"
│   ├── error.tsx               # route error boundary (Next 16 `retry` prop)
│   └── not-found.tsx
├── components/
│   ├── SiteHeader.tsx          # nav, marks the current section
│   ├── UploadForm.tsx          # the picker, drop zone, validation, in-flight lock
│   ├── MatchListView.tsx       # loading / empty / error / populated
│   ├── MatchDetailView.tsx     # the polling page
│   ├── CalibrationView.tsx     # placing landmarks, and checking the result
│   ├── FrameStage.tsx          # a calibration image with an SVG layer, in image pixels
│   ├── TopDownCourt.tsx        # the court from above, in court feet
│   ├── StatusBadge.tsx         # job and match badges: word + glyph + colour, never colour alone
│   ├── ScopeNotice.tsx         # "this build reads the video and calibrates the court"
│   ├── Notice.tsx              # boxed message, caller chooses the ARIA role
│   ├── RefreshButton.tsx       # shared, so its aria-disabled handling cannot drift
│   └── Spinner.tsx
├── hooks/usePolledResource.ts  # the polling loop
├── lib/
│   ├── api.ts                  # the only module that calls the backend
│   ├── types.ts                # hand-written mirrors of the Pydantic schemas
│   ├── court.ts                # landmark names and positions, court lines, a position in words
│   ├── homography.ts           # one pixel through a saved calibration, for a preview
│   ├── files.ts                # client-side upload validation
│   ├── format.ts               # timestamps, durations, media length, average fps
│   └── status.ts               # the words used for each job and match status
├── next.config.ts              # includes the legacy /videos redirects
├── scripts/check-contract.mjs  # fails if lib/types.ts drifts from the API
├── vitest.shared.mts           # the settings both suites must agree on
└── tests/                      # Vitest; tests/live/ is opt-in
```

Pages are thin Server Components that render a client component: `metadata`
cannot be exported from a `"use client"` module, so each route keeps its
metadata in the server shell.

## Running It

The API must be running first; the app is a browser client and does nothing
useful without it.

```bash
cd apps/web
npm install
cp .env.example .env.local     # defaults already match a local API
npm run dev                    # http://localhost:3000
```

In two other terminals, from the repository root:

```bash
docker compose up -d postgres redis
uv run pbapi                   # http://127.0.0.1:8000
uv run pbworker                # or jobs sit in `queued` for ever
```

Everything in Docker instead:

```bash
docker compose --profile app up -d --build
```

| Script | What it does |
|---|---|
| `npm run dev` | Development server on port 3000 |
| `npm run build` | Production build |
| `npm start` | Serve the production build |
| `npm test` | Vitest, hermetic: no API, no network |
| `npm run test:live` | Opt-in suite against a running API and worker |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run check:contract` | Compare `lib/types.ts` against the live OpenAPI schema (`API=` overrides the URL) |

## Pages

| Route | Purpose |
|---|---|
| `/` | Upload a match. Drag-and-drop or file picker, validation, links to the new match |
| `/matches` | Every match, newest first: name, file, upload date, and match status |
| `/matches/:id` | One match: its status, its video and decoded metadata, its calibration, its latest job's state, earlier attempts, a manual refresh, and **Extract metadata** / **Make calibration images** / **Try again** when usable |
| `/matches/:id/calibrate` | Place landmarks on a still, save, and check the result |
| `/videos` | Redirects (307) to `/matches` |
| `/videos/:id` | Redirects (307) to `/matches/:id` |

`/matches/:id` polls `GET /api/matches/{id}` rather than `GET /api/jobs/{id}`
because that single response carries the match, its video, its latest job and
every earlier attempt, so the page needs one request per tick — and there is no
endpoint that lists jobs.

The `/videos` redirects are declared in `next.config.ts` (and tested in
`tests/redirects.test.ts`) rather than kept as pages, so there is only one
implementation of each view. `/videos/:id` maps straight onto `/matches/:id`
because migration 0003 gave each Phase 1 video a match with the same id. They
are temporary redirects so no browser caches them permanently.

A match is named after its upload's filename, without the extension, and that
name is the page title and the list link. The original filename is shown as
metadata under **Video**.

### Video metadata

The **Video metadata** section shows what the worker decoded, or says which of
the reasons for having nothing applies. It never renders a blank or a zero:

| State | What it says |
|---|---|
| Decoded | Resolution (as displayed), rotation, estimated duration, frame rate labelled "average", frame count, codec (or "Not named by the file"), and when it was extracted |
| Job queued or running | Metadata is being extracted |
| Latest job failed | No metadata was saved, because processing failed — with **Try again** |
| Job finished before extraction existed | Processed before metadata extraction was added — with **Extract metadata** |

Resolution is in display orientation, which is what the API returns: a
portrait phone clip stored sideways reads 1080 × 1920, with "90°, applied to the
resolution above" beside it. A note under the values says the frame rate is an
average and that durations derived from it are approximate.

The button appears only when `can_extract_metadata` is true, so the server
decides and the page never offers an action that would answer 409. Pressing it
calls `POST /api/matches/{id}/metadata-jobs`, then reloads the match, and polling
resumes because the new job is queued. It follows the upload form's rules: a
ref blocks a double submission, `aria-disabled` keeps focus on the button, and a
refusal or queue failure is shown beside it, linked with `aria-describedby`.

### Court calibration

The **Court calibration** section of the match page summarises a saved calibration
and anything to review about it, links to the calibration page when
`can_calibrate` is true, and otherwise says why not, offering **Make calibration
images** or **Try again** when `can_extract_reference_frames` is true.

`/matches/:id/calibrate` reads the match once and does not poll: nothing there
changes unless the page asks, and a poll could only overwrite work in progress.

**Placing.** A click places the selected landmark and moves to the next one still
needing an answer. A placed landmark can be dragged, or reselected from the list.
**Not visible** skips one, since not every camera sees all twelve. Arrow keys move
the selected landmark by one image pixel, or ten with Shift, from the image or the
list, and Enter on the image drops it in the middle, so the whole job can be done
from the keyboard. A click only resolves to a screen pixel, about two image pixels
on a laptop, and at the far baseline an image pixel can be half a foot, so the page
recommends the arrow keys there. The instructions say to click the middle of the
painted lines, and warn that from a low camera the top of the net can look like the far
baseline. A magnifier follows the pointer or the selected landmark, and a small top-down
court shows where the selected landmark is. Choosing another image keeps the landmarks,
which are in the same pixels. Saving does nothing while the landmarks and image are the
ones already saved, and the page says so.

**A moved camera.** When `video.camera_jumps` lists a move, a warning above the landmarks
says when it happened and that the errors cannot show the damage. Advice about a moved
camera stays under the image picker unless the check found no move, so a video that was
never checked (`null`) is not implied to be steady.

**Checking.** Saving shows the errors, labelled as how well the landmarks agree
rather than how accurate a position will be, what one pixel is worth at the far
baseline, the API's warnings, and the API's
projected lines over any of the images, including a single frame with players in it.
A click on one puts the point on the top-down court, and in words. A point above the
horizon is said not to be on the court plane, because the sum would otherwise produce
a confident number for a point in the sky, and the page says all this is valid for
points on the ground only.

The browser fits nothing and decides nothing: the API fits the homography, projects
the lines, chooses the warnings and refuses what cannot work. The one calculation
here is projecting a clicked pixel for display (`lib/homography.ts`), and nothing
is recorded from it.

The image is a plain `<img>`, not `next/image`: it has to be these exact pixels from
a private API, and an optimizer's resize would only make landmarks harder to place.
An SVG layer with the image's own size as its `viewBox` sits on top, so anything
drawn lines up at any page width.

## Environment Variables

Both are `NEXT_PUBLIC_`, which means they are **inlined into the browser bundle
at build time**. They are public by definition; never put a secret behind that
prefix. It also means a Docker image is built for one API URL and changing it
requires a rebuild, which is why the compose service passes them as build args.

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | `http://localhost:8000` | Where the **browser** reaches the API |
| `NEXT_PUBLIC_MAX_UPLOAD_BYTES` | `2147483648` | Mirrors the API's limit, for the client-side check |

`NEXT_PUBLIC_API_BASE_URL` stays `localhost` even when everything runs in
Docker: it is resolved in the visitor's browser, not inside the container, so
the compose service name `api` would not resolve.

The app calls the API directly from the browser, so the API's CORS list must
include the app's origin. `http://localhost:3000` and `http://127.0.0.1:3000`
are allowed by default; a different port needs `PICKLEBALL_CORS_ORIGINS`
changed. Almost no custom request headers are sent, deliberately: the API's CORS
configuration allows only `Content-Type`, and `multipart/form-data` is
CORS-safelisted, so an upload is a simple request with no preflight. Saving a
calibration is the exception: it sends JSON, so it is preflighted, which that
configuration allows.

## The API Boundary

`lib/api.ts` is the only module that calls the backend. Every call funnels
through one `request` helper so that the four ways a call can fail — the server
is unreachable, it answered 4xx/5xx, it answered with something that is not
JSON, or the caller aborted — all reach the UI as one `ApiError` carrying a
sentence worth showing a person.

Types in `lib/types.ts` are hand-written rather than generated: the contract is
a handful of shapes and a code generator would be more machinery than it earns. The
guard against drift is `npm run check:contract`, which reads the OpenAPI schema
the running API serves and compares field names and enum values (`JobStatus`,
`JobStage`, `MatchStatus`, `ReferenceFrameKind`, `CalibrationSource`, `CourtLandmark`)
against the declarations, and checks that the routes the client calls exist. Run it after any change to the API's schemas.

The client calls `listMatches`, `getMatch`, `createMatch` (the upload),
`startMetadataJob`, `startReferenceFrameJob`, `createCalibration` and `getJob`, and
`referenceFrameUrl` builds an image's address. The two job calls send a `POST` with
no body and no headers, so they stay CORS simple requests.

## Polling

`usePolledResource` runs the whole loop inside one effect, with its timer and
abort controller as ordinary local variables, so the teardown closes over
exactly the loop it started. That is what makes unmounting, navigating away,
and React's development StrictMode double-effect each cancel their own loop and
nothing else.

- The next request is scheduled after the previous one settles, never on a
  fixed interval, so requests cannot overlap.
- A failed refresh keeps the last good value on screen and raises a separate
  `staleError`. A dropped packet must not replace a rendered job with an error
  page, and a transport failure is not a job failure. "Have we ever had a
  value" is tracked in a ref rather than a loop-local, because `refresh()`
  restarts the effect and a loop-local would reset — which is exactly the bug
  the checkpoint-3 review found.
- `isRefreshing` is true only for a fetch the person asked for. A background
  poll deliberately touches no UI-visible flag, so the refresh button does not
  flicker disabled every couple of seconds.
- Repeated failures back off, up to 30 seconds.
- A 404 stops the loop, because it will not fix itself.
- Polling stops on a terminal status, and the page says so.

## Honesty About What Was Analysed

`CLAUDE.md` says not to hide uncertainty from the frontend and to prefer
"unknown" over a confidently wrong label. A green **Ready** badge on a
video-analysis product would otherwise read as "your match has been analysed",
when all that happened is that its metadata was read.

So: the status page carries a permanent notice of what this build does; a `ready`
job is headed **Processing finished**, with a sentence saying processing covers video
metadata and calibration images and no match analysis was performed, never "Analysis
complete"; there is no results link; the unimplemented pipeline
stages are not drawn as a progress checklist; the frame rate is labelled an
average; and metadata the server does not have is explained rather than shown as
blank fields or zeros.

The match status follows the same rules. A processed match is **Needs
calibration**, in amber rather than green, and a calibrated one is **Court
calibrated**, with a sentence saying tracking and statistics are not available yet.
There is no "Ready" match badge, because the API has no such status. Tests assert
that no match status label or description reads as a finished analysis.

## Accessibility

The decisions worth knowing, because they are easy to undo by accident:

- The file input is a real `<input type="file">` styled `sr-only` inside its
  `<label>` — never `hidden` or `display:none`, which would remove it from the
  tab order and the accessibility tree. The drop zone is layered on the same
  input, so dragging is an enhancement and never the only way in.
- `onDragOver` calls `preventDefault()`. Without it the drop event never fires
  and the browser navigates away to open the file, losing the page.
- Validation errors are tied to the input with `aria-describedby` and
  `aria-invalid`, and submitting an invalid form moves focus to the input.
- All three views keep one always-mounted `role="status"` region, rendered
  empty on first paint and present in the loading and error branches too: assistive technology registers a live region when it is inserted
  and announces later changes, so a region that arrives together with its first
  message is frequently missed. Its text is derived from the status alone, with
  nothing that ticks, so polling an unchanged status re-announces nothing.
- Badges pair colour with the status word and a distinct glyph.
- Buttons use `aria-disabled` rather than `disabled`, because a focused element
  that becomes disabled drops focus to the body. Duplicate submission is
  additionally blocked by a ref, since two Enter presses can both run before
  React re-renders.
- List rows are real links, so they can be tabbed to and opened in a new tab.
- One focus style, `focus-visible`, applied everywhere; nothing removes an
  outline without replacing it.
- Everything honours `prefers-reduced-motion`, and the only animation is a
  spinner that is purely decorative.

## Testing

```bash
npm test          # hermetic; no API, no network, no video files
npm run test:live # needs the API, the worker, PostgreSQL and Redis
```

The hermetic suite stubs `fetch` and nothing else, so validation, focus
management, live regions and the polling loop are all the real code.

The live suite is split by environment for a reason worth knowing: jsdom
provides its own `File` and `FormData`, which Node's `fetch` does not recognise
as a file part, so a multipart upload from jsdom is serialised as a plain text
field and the API answers 422. A real browser has no such problem. So
`01-upload.live.test.ts` runs in a Node environment where the globals behave
like a browser's, and `02-status.live.test.tsx` runs in jsdom, where ordinary
GET requests work, and renders the components against the match the first file
uploaded.

## Troubleshooting

**Every page says "Could not reach the API".** The API is not running, or
`NEXT_PUBLIC_API_BASE_URL` points somewhere else, or the API's CORS list does
not include this origin. Check `curl http://localhost:8000/health` first. Note
that `NEXT_PUBLIC_` values are inlined at build time, so changing one needs a
restart of `npm run dev` — and a rebuild of the Docker image.

**An upload returns 415 for a file that really is a video.** The API checks the
first bytes for an ISO base-media `ftyp` box. Some older QuickTime `.mov` files
begin with `moov` instead and are refused.

**A calibration image never loads.** The file is missing from the API's upload
directory, which happens when the API and worker resolved different ones. See
`BACKEND.md`.

**Every new upload ends "Processing failed" with `unreadable_video`.** The file
is not a video OpenCV can decode. The live suite's upload is deliberately just an
mp4 header, so it ends here too; a real clip ends at **Needs calibration**. If a
real video fails, check the worker's log.

**The job never leaves "Queued".** No worker is running. Start `uv run pbworker`
from the repository root.

**Changes to `lib/types.ts` pass locally but break against the API.** Run
`npm run check:contract` with the API up; it compares the declared fields and
enum values against the live OpenAPI schema.

**`npm run typecheck` complains about `PageProps` or `LayoutProps`.** Those are
generated into `.next/types` by `next dev`, `next build` or `next typegen`. Run
one of them once on a clean checkout.

## Known Limitations

- **No upload progress bar.** `fetch` cannot report upload progress; only
  `XMLHttpRequest` can. The form shows an in-flight state and a warning that
  large files take minutes, but not a percentage. A fake bar would be worse
  than none.
- **The whole file is uploaded before a bad one is rejected.** The API's
  format check reads the first bytes, but the ASGI server has already buffered
  the body by then, so a 1.5 GB file that is not really a video still takes a
  full upload to fail.
- **No pagination.** The list requests the default page of 100 and shows no
  total, because `MatchList.count` is the length of that page rather than a
  count of all rows. Past 100 matches the oldest simply do not appear.
- **No renaming.** A match keeps its filename-derived name; the API has no
  endpoint to change it or to set `recorded_at`.
- **Retry is limited to extraction that never succeeded.** **Try again** starts a new job;
  once metadata exists there is no way to extract it again.
- **No delete, no playback, no authentication**, matching the backend.
- **No browser-driven test in the repository.** The live suite drives the real
  components against the real API in jsdom. Calibration was also driven once in real
  Chrome, with results in `ROADMAP.md`, using a script that was not kept.
- **The magnifier does not help on a touch screen**, where nothing hovers, though it
  does follow the selected landmark. The page lays out at phone width, but placing
  landmarks there is not practical.
