# M7 browser evidence

Captured 2026-10-19 in the managed cloud environment using Chromium 154.0.8037.57.
The empty and populated books are disposable offline fixtures, not the operator's account or market results.

The production React build and FastAPI application passed navigation, keyboard, dossiers, downloads, STOP,
CSP and JavaScript checks at 1920×1080, 1366×768 and 390×844. Both retained one SSE connection through
24 view changes. JSON reports record a **12-second** post-GC heap sample; this cannot establish the absence
of a long-duration leak or performance on the Dell laptop. Screenshot values are fixtures.

Reproduce with [tests/browser/README.md](../../../tests/browser/README.md). Real Firefox/laptop performance,
accessibility review and power behavior remain acceptance work.
