# photo-manager frontend

React 19 + Vite 6 + Tailwind 4 + TanStack Query/Table/Virtual.

## Dev

```bash
# Start the backend first (port 8765):
PHOTO_MANAGER_DEV_MODE=1 python -m app.web.main

# Start the Vite dev server (port 5173):
npm run dev
```

All `/api/*` requests from the dev server are proxied to `http://127.0.0.1:8765`
via the `server.proxy` config in `vite.config.ts` — no CORS configuration needed
on the browser side. The backend adds CORS headers only when
`PHOTO_MANAGER_DEV_MODE=1`.

## Build

```bash
npm run build   # output in dist/
```

## Test

```bash
npm run test            # single run (vitest)
npm run test:watch      # watch mode
npm run test:coverage   # with v8 coverage
```

## Lint / Type-check

```bash
npm run lint       # ESLint 9 flat config
npm run typecheck  # tsc --noEmit
```

## testids.ts — GENERATED, do not hand-edit

`src/testids.ts` is auto-generated from `qa/web/testid_constants.py`. To
regenerate after editing the Python constants:

```bash
python scripts/gen_testid_ts.py
```

CI enforces parity via `tests/test_testid_parity.py` (runs under the existing
`tests.yml` pytest job). The file is in `.prettierignore` so Prettier reformatting
does not break parity checks.
