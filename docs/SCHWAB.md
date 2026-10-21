# Schwab market data: free local HTTPS, optional Tailscale

This integration is market-data-only. It does not request accounts, positions or orders and cannot place a
brokerage trade. The Stocks ledger remains PAPER. Broker approval, API access and data entitlements still need
verification on the operator's laptop. No real authorization was performed in the cloud.

## Is 127.0.0.1 blocked?

Do not assume a blanket ban. The maintained schwab-py authentication documentation fetched on 2026-10-21 still
shows `https://127.0.0.1` in its example and explains that Schwab requires HTTPS callbacks:
https://github.com/alexgolec/schwab-py/blob/master/docs/auth.rst

The official Schwab developer portal was proxy-denied from this workspace. This is third-party SDK evidence,
not verification of what the operator's current app-registration form accepts. `http://127.0.0.1`, `localhost`,
HTTPS, an explicit port and the path are different callback registrations. Use the exact approved HTTPS URL.
If the portal rejects a URL, record its actual message instead of attributing it to an unverified 2024 rule.

## Default: no domain and no Tailscale

Try registering **`https://127.0.0.1:8182/oauth/callback`**. If the app accepts and approves that exact URL,
KESTREL's callback listener provides local HTTPS itself. It generates a self-signed certificate and a private
key beside secrets.env; it does not buy a domain, install a certificate authority, or change the browser's trust
store. Your browser will warn about the self-signed certificate. An exception is appropriate only for your exact
local callback address while your own listener is running. Never disable certificate checks for Schwab.

Start `uv run desk stocks dashboard --open`, open Settings, choose Schwab, save the app key/secret and approved
callback, then click **Start browser authorization → Open Schwab authorization**. The page is local HTTP on
port8767; the separate OAuth callback is local **HTTPS** on8182. They have different jobs. The app key and secret
are password fields, blanks preserve existing values, and changing app credentials invalidates old tokens.
Stop the Stocks worker before changing its data connection; the Settings page enforces that lock.

Terminal alternative after privately saving credentials:

```bash
cd ~/kestrel
uv run desk stocks connect
uv run desk stocks doctor --online
```

`connect` selects Schwab as the option provider and listens for up to five minutes. It does not place orders.
If you prefer no listener, `uv run desk stocks connect --manual` accepts the complete final redirect privately
in a hidden terminal prompt, even if that address failed to load in the browser. Do not paste it into chat.
A different callback must match the approved URL exactly; use `--callback-url` or Settings.

## Tailscale

Use this fallback only if a loopback registration is rejected or a tailnet hostname is otherwise useful.
The free personal Tailscale option may provide the `.ts.net` hostname/certificate without buying a domain;
its account eligibility and HTTPS setting still need to be checked by the operator.

Tailscale Serve can terminate trusted HTTPS on the laptop's full `machine.tailnet.ts.net` hostname and proxy
only the dedicated callback listener. The browser performing login must be able to reach that tailnet device.
Schwab redirects the browser; the browser makes the callback request. Private Serve is normally enough for
that network path; provider registration policy may still reject a particular hostname. Do not claim approval
until the exact URL is accepted in the Schwab app. Funnel is not required or enabled by KESTREL.

Official Tailscale CLI source (Serve vs public Funnel and HTTPS proxy options):
https://github.com/tailscale/tailscale/blob/main/cmd/tailscale/cli/serve_v2.go

On the laptop, inspect `tailscale status` to find its full `.ts.net` hostname and check that HTTPS certificates
are enabled for the tailnet. If port443 Serve is not already in use, the callback setup is:

```bash
tailscale serve --bg http://127.0.0.1:8788
tailscale serve status
```

Register `https://YOUR-FULL-TAILSCALE-HOSTNAME/oauth/callback` in the Schwab app and use that exact URL in
KESTREL Settings or `desk stocks connect --callback-url URL --listen-port 8788`. Never substitute the literal
placeholder. This command does not start the trading app on another machine or keep a sleeping laptop online.
Inspect existing Serve routes first; do not reset another application's configuration. To remove this root
Serve proxy later, `tailscale serve --https=443 off` removes that listener. HTTPS hostnames can appear in public
certificate-transparency logs even though Serve traffic is tailnet-private.

## Privacy and operator authorization

Store `SCHWAB_APP_KEY` and `SCHWAB_APP_SECRET` in the existing private secrets.env or save them in the local
Settings form. Never paste them into this conversation. The app secret is never returned to the browser.
Tokens live beside secrets.env in `schwab-token.json`, owner-only0600, replaced atomically after refresh.
Authorization requests use fresh random state, exact callback matching and one decoding pass. Market requests
receive fresh bearer headers; token POSTs bypass raw evidence storage and refuse redirects. Token response bodies
and failed HTTP exception URLs are never printed. Concurrent token refreshes share a private file lock.

Access lifetimes come from the token response (commonly30minutes). Refresh authorization is commonly documented
as about7days, but this app does not pretend refreshing access renews that original authorization. A revoked or
expired refresh requires a fresh login. A data entitlement or approval failure is reported; it does not silently
switch from real-time to delayed data. Endpoints and the conservative90requests/min budget remain `to_verify`
until tested with the operator's approved app. Options with nonstandard deliverables/multipliers are excluded.

Massive grouped end-of-day history remains the broad price source. Selecting Schwab supplies stock quotes and
option chains to the existing research/PAPER data contracts; it does not magically replace a whole-universe
historical archive or provide historical option chains. Scores are research scores, not profit probabilities.

Endpoint paths were also checked against the maintained SDK's `schwab/client/base.py`, methods `get_quotes`,
`get_option_chain`, and `get_option_expiration_chain`:
https://github.com/alexgolec/schwab-py/blob/master/schwab/client/base.py
The parser fixtures are documentation-shaped synthetic examples, not captured authenticated broker responses.
Explicitly delayed Schwab chains are refused rather than silently mixed with the current execution model.
