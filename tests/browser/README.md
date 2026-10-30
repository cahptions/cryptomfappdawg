These optional checks use the production React build and FastAPI application with disposable, offline test data. They do not start market workers, contact providers, or load operator secrets.

Build the UI first with `scripts/build_ui.sh`. Install the browser driver outside the checkout:

```bash
npm install --prefix /tmp/kestrel-browser --cache /tmp/kestrel-browser-npm-cache --no-audit --no-fund playwright@1.56.1
```

Use a local Chromium installation, or install Chromium through Playwright and set `CHROMIUM_EXECUTABLE` to its executable. Start the fixture server in one terminal:

```bash
uv run python -m tests.browser.fixture_server --scenario empty --port 8877
```

In another terminal:

```bash
KESTREL_BROWSER_SCENARIO=empty node tests/browser/smoke.mjs
```

Restart the fixture server with `--scenario populated` and run with `KESTREL_BROWSER_SCENARIO=populated` to check candidates, position and strategy dossiers, and recorded trade downloads. Screenshots and JSON evidence go to `/tmp/kestrel-browser-results` by default. `PLAYWRIGHT_MODULE`, `KESTREL_BROWSER_URL`, and `KESTREL_BROWSER_OUTPUT` can override the driver, server address, and artifact directory.

The heap and connection checks cover a short sample and repeated navigation. They do not replace laptop resource measurements or the required long-duration forward runs.

For the separate Stocks PAPER Learning workspace, start
`python -m tests.browser.stocks_fixture_server --scenario learning-workflows --port 8891` and run
`tests/browser/stocks_learning_workflows.mjs` with Playwright available and
`KESTREL_STOCKS_URL=http://127.0.0.1:8891`. This fixture creates actual engine-generated synthetic fills and funding in a
disposable private ledger; the browser checks desktop/mobile controls, accounting, order history, experiments and downloads.

`stocks_paper_workflows.mjs` also checks archived session playback, date-scoped holdings and exact chart tooltips against
the `paper-workflows` fixture. `stocks_paper_sparse.mjs` uses the same disposable server with synthetic three-observation
responses to verify honest ranges and automatic forward-record polling without navigation. It also checks the separate
Options discovery lanes and source-capability guides in Settings. Neither fixture contacts a provider.

`stocks_play_freshness.mjs` uses `stocks_fixture_server --scenario plays-history` and synthetic intercepted responses
to check an Oct 28 estimate becoming an Oct 29 quote without reload, preserving quantity/range, retaining values on
refresh errors and stopping polling after navigation. Run it with `KESTREL_STOCKS_URL` set to that disposable server.
The Python quote-freshness regressions separately test real saved-tick selection and live-before-repair ordering.

The Learning workflow also verifies that completed outcomes remain visible while the formal model is version 0, with
explicit block/day thresholds and provisional result cards at desktop and phone widths. The retail workflow verifies
that evidence counts refresh even if account balances and model version stay unchanged.
