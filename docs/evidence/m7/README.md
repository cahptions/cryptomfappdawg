# M7 browser evidence

Captured 2026-10-19 in the managed cloud environment using Chromium 154.0.8037.57.
The empty and populated books are disposable offline fixtures, not the operator's account or market results.

The production React build and FastAPI application passed navigation, keyboard, dossiers, downloads, STOP,
CSP and JavaScript checks at 1920×1080, 1366×768 and 390×844. Both retained one SSE connection through
24 view changes. JSON reports record a **12-second** post-GC heap sample; this cannot establish the absence
of a long-duration leak or performance on the Dell laptop. Screenshot values are fixtures.

Reproduce with [tests/browser/README.md](../../../tests/browser/README.md). Real Firefox/laptop performance,
accessibility review and power behavior remain acceptance work.

## Paper trading tab update (2026-10-19)

`empty-browser.json` and `populated-browser.json` were re-recorded with the extended harness in Chromium
141.0.7390.37 (Playwright 1.56.1): all nine views, the Paper trading tab (`g t` shortcut, plain-English feed and
filters, exit levels, funnel, fees, moonshot watch, position dossier) and no horizontal overflow at 390 px, plus
every earlier check. `paper-desktop.png`, `paper-mobile.png` and `paper-empty-mobile.png` show the same offline
fixtures ("FIXTURE" is a disposable test token); none of these values are market or account results.

