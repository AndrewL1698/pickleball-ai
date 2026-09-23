# Frontend

The Next.js app added in Phase 1 checkpoint 2. It covers uploading a video and
following the processing job that results. The backend it talks to is described
in `BACKEND.md`.

## What This Checkpoint Does and Does Not Do

It does: take a video by file picker or drag-and-drop, validate it before and
after submission, upload it, list what has been uploaded, and follow one
video's job through `queued`, `running`, `ready` and `failed` — polling only
while the answer can still change, and surviving a refresh or a revisit because
the server is the only source of truth.

It does not play video, show analysis results, or mention matches. There is no
`Match` entity yet, nothing serves the uploaded file back, and processing is a
placeholder that records a checksum. The UI says so, on the page, in as many
words.

## Structure

```text
apps/web/
├── app/
│   ├── layout.tsx              # shell: skip link, nav, main landmark, footer
│   ├── globals.css             # Tailwind v4 import, colour tokens, focus ring
│   ├── page.tsx                # "/" upload page
│   ├── videos/page.tsx         # "/videos" list
│   ├── videos/[videoId]/page.tsx  # "/videos/:id" status, awaits `params`
│   ├── error.tsx               # route error boundary (Next 16 `retry` prop)
│   └── not-found.tsx
├── components/
│   ├── SiteHeader.tsx          # nav, marks the current section
│   ├── UploadForm.tsx          # the picker, drop zone, validation, in-flight lock
│   ├── VideoListView.tsx       # loading / empty / error / populated
│   ├── VideoStatusView.tsx     # the polling page
│   ├── StatusBadge.tsx         # word + glyph + colour, never colour alone
│   ├── PlaceholderNotice.tsx   # "this build does not analyse video yet"
│   ├── Notice.tsx              # boxed message, caller chooses the ARIA role
│   └── Spinner.tsx
├── hooks/usePolledResource.ts  # the polling loop
├── lib/
│   ├── api.ts                  # the only module that calls the backend
│   ├── types.ts                # hand-written mirrors of the Pydantic schemas
│   ├── files.ts                # client-side upload validation
│   ├── format.ts               # timestamps and durations
│   └── status.ts               # the words used for each job status
├── scripts/check-contract.mjs  # fails if lib/types.ts drifts from the API
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
| `npm run check:contract` | Compare `lib/types.ts` against the live OpenAPI schema |

## Pages

| Route | Purpose |
|---|---|
| `/` | Upload a video. Drag-and-drop or file picker, validation, links to the new video |
| `/videos` | Everything uploaded, newest first, each with its latest job status |
| `/videos/:id` | One video: its details, its job's state, and a manual refresh |

`/videos/:id` is the job page. It polls `GET /api/videos/{id}` rather than
`GET /api/jobs/{id}` because that single response carries the video, its latest
job and every earlier attempt, so the page needs one request per tick — and
there is no endpoint that lists jobs.

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
changed. No custom request headers are sent, deliberately: the API's CORS
configuration allows only `Content-Type`, and `multipart/form-data` is
CORS-safelisted, so an upload is a simple request with no preflight.

## The API Boundary

`lib/api.ts` is the only module that calls the backend. Every call funnels
through one `request` helper so that the four ways a call can fail — the server
is unreachable, it answered 4xx/5xx, it answered with something that is not
JSON, or the caller aborted — all reach the UI as one `ApiError` carrying a
sentence worth showing a person.

Types in `lib/types.ts` are hand-written rather than generated: the contract is
two entities and a code generator would be more machinery than it earns. The
guard against drift is `npm run check:contract`, which reads the OpenAPI schema
the running API serves and compares field names and enum values against the
declarations. Run it after any change to the API's schemas.

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
  page, and a transport failure is not a job failure.
- Repeated failures back off, up to 30 seconds.
- A 404 stops the loop, because it will not fix itself.
- Polling stops on a terminal status, and the page says so.

## Honesty About the Placeholder

`CLAUDE.md` says not to hide uncertainty from the frontend and to prefer
"unknown" over a confidently wrong label. A green **Ready** badge on a
video-analysis product would otherwise read as "your match has been analysed",
when all that happened is a checksum.

So: the status page carries a permanent notice that the build does not analyse
video; `ready` is headed **File check complete**, never "Analysis complete";
there is no results link; the unimplemented pipeline stages are not drawn as a
progress checklist; and the metadata the server does not have is stated as not
extracted rather than shown as blank fields or zeros.

Nothing is called a "match". The backend stores videos, and `Match` arrives in
checkpoint 3.

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
- Each page has one always-mounted `role="status"` region, rendered empty on
  first paint: assistive technology registers a live region when it is inserted
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
GET requests work, and renders the components against the video the first file
uploaded.

## Known Limitations

- **No upload progress bar.** `fetch` cannot report upload progress; only
  `XMLHttpRequest` can. The form shows an in-flight state and a warning that
  large files take minutes, but not a percentage. A fake bar would be worse
  than none.
- **The whole file is uploaded before a bad one is rejected.** The API's
  format check reads the first bytes, but the ASGI server has already buffered
  the body by then, so a 1.5 GB file that is not really a video still takes a
  full upload to fail.
- **No pagination.** The list requests the default page. `VideoList.count` is
  the length of that page and not a total, so no total is shown.
- **No retry for a failed job**, because the API exposes no endpoint for it.
- **No delete, no playback, no authentication**, matching the backend.
- **No browser-driven test.** The live suite drives the real components against
  the real API in jsdom, which covers the data flow, but nothing here has
  clicked the real drop zone in a real browser. Checkpoint 3 should add that.
