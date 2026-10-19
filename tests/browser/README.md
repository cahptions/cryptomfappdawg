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
