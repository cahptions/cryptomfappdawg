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
Keep the dashboard's terminal (`desk stocks dashboard`) running throughout setup. If its research collector is
active, click **Pause collector to save** in the Schwab connection form. It interrupts research waits safely, then
**Save privately** becomes available without discarding typed credentials. If you started collection separately,
press Ctrl+C only in the terminal running `desk stocks collect`, `desk stocks paper`, or `desk stocks refresh`.
Ctrl+C in the dashboard terminal shuts down the web server and causes a connection error; Ctrl+C in a browser
does not stop collection. A page left open across a dashboard restart renews an expired session token once
when you submit; a hard reload (Ctrl+Shift+R) also fetches a fresh page/session.

Schwab labels **App key** as **Client ID**, and **App secret** as **Client Secret**. Copy each into its corresponding
field; a secret entered as the Client ID produces an `invalid_client` authorization error. Identical values are
rejected before saving. Choose **Schwab market data** and save it before starting authorization. If an attempt
fails, **Cancel pending authorization** closes that local callback attempt so you can correct the fields and
start again without waiting five minutes. Cancellation waits for the callback thread to finish before allowing
credential replacement. Keep any exposed secret out of future screenshots and replace it through Schwab's portal.

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

## Additional company statistics

The stock-page quote request asks for `quote,fundamental,reference,extended,regular`. If Schwab returns HTTP 400 for
the expanded field selection, the client retries the plain quote request so the chart/quote can still populate.
**Company data sources**, at the bottom of the stock page, lists which sections arrived.

The extra section field names and units remain unverified against an authenticated response. In particular, do not
assume whether `divYield` is a percentage or a fraction, or what units `htbRate` uses. Missing/ambiguous values stay
unavailable. The documentation-shaped fixtures test parser behavior and are not live broker acceptance evidence.
The request adds market-data context only; no account/trading endpoint is used.

## When Test market-data access says the sign-in expired

Schwab's access token lasts about 30 minutes and is refreshed automatically, but the refresh authorization stops
working 7 days after the browser login and only a new login renews it. When that happens Schwab answers the refresh
request with HTTP 400 (or 401). The app then:

- says so in Settings → Authorization status ("Schwab sign-in expired"), with the OAuth error code only (never the
  response text), and highlights **Start browser authorization**;
- stops asking Schwab again until the new login (a `schwab-token.state.json` marker beside the token file);
- counts down the days left in the 7-day window while the login is still valid.

Fix: Settings → **Start browser authorization** → **Open Schwab authorization** → log in, then **Test market-data
access**. The app key and secret are kept. A refused refresh is never retried, whatever its code: Schwab also answers
an expired refresh token with `invalid_client` ([schwab-py](https://schwab-py.readthedocs.io/en/latest/auth.html)),
and asking again on every request cannot help. On a login, `invalid_client` means the key or secret does not match an
app that is Ready For Use; a refused login code means it was used twice, took longer than about 30 seconds, or the
callback URL does not match the app exactly.

### Ended after hours, not 7 days: two computers, one Schwab app

Each computer keeps its own sign-in (`~/.config/desk/schwab-token.json`). When both use the **same app key**, a new
sign-in on one computer is the usual reason Schwab stops accepting the other's within hours. The app tells the two
endings apart: when Schwab refuses a sign-in more than half a day short of its 7 days, Settings, the alert on every
page and `kestrel` say how many hours it lasted and the window in which Schwab stopped accepting it (between the last
refresh it accepted and the refusal). Run `kestrel` on both computers: if one computer's window contains the other's
"signed in here" time, that sign-in ended it.

The fix is one Schwab app per computer (developer.schwab.com → Create App, same Market Data product and callback URL;
enter its App Key and Secret in that computer's Settings once it is Ready For Use, then sign in), or signing in on one
computer only. Copying a token file between computers is not a fix: both would refresh the same token, and schwab-py
warns the slower one gets locked out.

## Logging in from another computer over SSH

The dashboard (8767) and the OAuth callback listener (8182) run on the laptop and listen on 127.0.0.1 only. After
login Schwab redirects *your browser* to `https://127.0.0.1:8182/oauth/callback?...`; on another computer that
address means that computer, so the page cannot load and the login is not saved. Forward both ports in one SSH
session (Windows PowerShell, macOS and Linux all accept this):

```bash
ssh -N -L 8767:127.0.0.1:8767 -L 8182:127.0.0.1:8182 you@laptop
```

Keep that window open, browse to http://127.0.0.1:8767, choose **Cancel pending authorization** if one is still
waiting, then **Start browser authorization → Open Schwab authorization**, accept the self-signed certificate for
`127.0.0.1:8182` only, log in, and finish within about 30 seconds of Schwab's redirect. Alternative without the
second forward: stop the dashboard, run `uv run desk stocks connect --manual` in the SSH terminal, open the printed
URL on the other computer, and paste the final (failed-to-load) redirect address into the hidden prompt.
