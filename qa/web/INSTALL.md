# Web QA harness — install guide

The web QA harness (`qa/web/`) uses [Playwright](https://playwright.dev/python/)
to drive a real Chromium browser against the FastAPI server.  Playwright
and its Chromium binary are **not** installed as part of the app's
`requirements.txt` (they are QA-only tooling).

---

## 1. Install Playwright

Playwright must be installed into the same virtualenv that runs the app
and tests (`.venv`).

```powershell
# From the repo root, with the venv active:
pip install playwright
```

## 2. Install the Chromium browser binary

```powershell
playwright install chromium
```

This downloads the Chromium binary that Playwright manages independently
of any system-installed Chrome.  No admin rights required.

## 3. Verify

```powershell
python - <<'EOF'
from playwright.sync_api import sync_playwright
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    p = b.new_page()
    p.goto("data:text/html,<h1>ok</h1>")
    print(p.title())
    b.close()
EOF
```

Expected output: `` (empty title, no errors).

## 4. Run the smoke test

```powershell
# Start the FastAPI server in one terminal:
python -m app.web.main

# Then run the smoke test in another:
python -m qa.web.smoke_test
```

Or pass `--url` to target an already-running server:

```powershell
python -m qa.web.smoke_test --url http://127.0.0.1:8765
```

## 5. Run the web batch runner

```powershell
# All scenarios (Phase 1: all are SKIP because no drivers are ported yet):
python -m qa.web._batch

# Check scenario-map parity:
python scripts/check_qa_parity.py --phase 1
```

## CI

The main CI job (`pytest`) does **not** install Playwright — it runs
unit tests only (layer 1).  A separate `web-qa` CI job (Phase 2+) will:

1. `pip install playwright && playwright install chromium`
2. Start the FastAPI server
3. Run `python -m qa.web._batch`

Until the `web-qa` job is wired in (Phase 2), Playwright-dependent code
degrades gracefully to SKIP in any environment where it is not installed.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ImportError: No module named 'playwright'` | Run `pip install playwright` |
| `playwright install` fails (no internet) | Download the browser archive offline; see Playwright docs |
| `TimeoutError` in a driver | Increase `timeout` kwarg; check the server is running and reachable |
| `StrictModeViolationError` on `get_by_test_id` | Two elements share the same `data-testid`; use a more specific locator |
