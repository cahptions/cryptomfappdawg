# Validation evidence

`checks.json` records the frozen software verification. All screenshots, data and browser outcomes here are
**synthetic fixtures**, not live offers, provider acceptance or business profit. `resources-synthetic.json`
measures a cloud process, not the Dell laptop. Five opt-in live-provider tests were excluded.

From the repository root:

```bash
uv run pytest -q
uv run ruff check src tests
uv run mypy
uv run python research/arbitrage/validation/benchmark.py
```

For the browser test, use a development machine with Chromium and Playwright 1.56.1. The runtime app does not
require Playwright or Node. In one terminal start the isolated fixture server:

```bash
uv run python research/arbitrage/validation/browser_server.py
```

In another terminal:

```bash
npm install --prefix /tmp/kestrel-browser playwright@1.56.1
node research/arbitrage/validation/browser_check.mjs
```

The script defaults to `/usr/bin/chromium` and the Playwright module under `/tmp/kestrel-browser`. A different
module path can be supplied with `KESTREL_PLAYWRIGHT_MODULE`. It checks the server's explicit fixture-identity
endpoint **before any form writes**, refusing a normal dashboard. The fixture server uses a disposable temporary
home, paused collection, synthetic credential markers and no real provider calls. Stop only that test server
when finished. No operator data, credentials or background collectors are involved.

The suite includes a safe 6,000-combination stress fixture: route calculations abstain before evaluation, the
observation board remains visible, and pausing a route restores bounded work. A separate catalog-turnover fixture
confirms that protected/manual/financial data remains intact when untouched API cache candidates are evicted.
