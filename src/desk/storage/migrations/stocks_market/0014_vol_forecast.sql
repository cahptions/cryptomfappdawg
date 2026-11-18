-- KESTREL's volatility forecast (desk.stocks.vol_forecast; docs/STOCKS_METHOD.md "Volatility forecast").

-- One stock's realized variance on one finished session, computed once: from five-minute returns of the archived
-- minute bars plus the overnight (previous close to open) return when the session has enough minutes ('minutes5'),
-- else the squared close-to-close log return ('daily'). ``ret`` is the close-to-close log return (split-adjusted).
CREATE TABLE stk_rv_daily (
    symbol       TEXT NOT NULL,
    day          INTEGER NOT NULL,
    rv           REAL NOT NULL,          -- variance of the session's log return (not annualized)
    ret          REAL NOT NULL,
    source       TEXT NOT NULL,          -- minutes5 | daily
    minutes      INTEGER,                -- regular-session minute bars used (minutes5 only)
    computed_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, day)
) WITHOUT ROWID;
CREATE INDEX stk_rv_daily_day ON stk_rv_daily(day);

-- Each stock's split record when its rows were computed: a newly recorded split recomputes that stock's rows.
CREATE TABLE stk_rv_symbols (
    symbol      TEXT PRIMARY KEY,
    splits_key  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
) WITHOUT ROWID;

-- The pooled HAR-IV regression fitted on one weekly fit day from targets fully realized before it, with the
-- out-of-sample evaluation realized before it and the gate that decides, per horizon, whether the forecast is used.
-- Append-only: a fit day is stored once and never rewritten.
CREATE TABLE stk_vol_fits (
    version          INTEGER NOT NULL,
    fit_day          INTEGER NOT NULL,
    sha              TEXT NOT NULL,
    model_json       TEXT NOT NULL,
    evaluation_json  TEXT NOT NULL,
    rows             INTEGER NOT NULL,
    stocks           INTEGER NOT NULL,
    fitted_at        TEXT NOT NULL,
    PRIMARY KEY (version, fit_day)
) WITHOUT ROWID;

CREATE TRIGGER stk_vol_fits_no_update BEFORE UPDATE ON stk_vol_fits BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_vol_fits_no_delete BEFORE DELETE ON stk_vol_fits BEGIN SELECT RAISE(ABORT, 'append-only'); END;
