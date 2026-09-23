# Web app

The Next.js frontend for uploading pickleball footage and following its
processing job.

```bash
npm install
cp .env.example .env.local
npm run dev        # http://localhost:3000
```

The API must be running too, or every page shows a connection error:

```bash
# from the repository root
docker compose up -d postgres redis
uv run pbapi
uv run pbworker    # without this, uploads stay queued for ever
```

| Script | |
|---|---|
| `npm run dev` | Development server |
| `npm run build` | Production build |
| `npm test` | Tests. No API or network needed |
| `npm run test:live` | Opt-in tests against a running API and worker |
| `npm run lint` | ESLint |
| `npm run typecheck` | `tsc --noEmit` |
| `npm run check:contract` | Check `lib/types.ts` against the live API schema |

Full documentation — structure, environment variables, the polling design,
accessibility decisions and current limitations — is in
[`docs/FRONTEND.md`](../../docs/FRONTEND.md).
