-- KESTREL's direction and volatility-surprise models (desk.stocks.forecast; docs/STOCKS_METHOD.md "Direction and
-- volatility").

-- One weekly fit of one model: the models trained on every row whose outcome was known before ``fit_day``, the
-- out-of-sample evaluation of earlier purged folds (realized before ``fit_day``) and, per horizon, whether the model
-- beat its baseline there (``live_json``). Append-only: a fit day is stored once and never rewritten.
CREATE TABLE stk_forecast_fits (
    model            TEXT NOT NULL,          -- direction | vol_surprise
    version          INTEGER NOT NULL,
    fit_day          INTEGER NOT NULL,
    sha              TEXT NOT NULL,          -- sha256 of the stored models (``stk_forecast_blobs``)
    live_json        TEXT NOT NULL,          -- {horizon: true when it beat the baseline out of sample}
    evaluation_json  TEXT NOT NULL,
    rows             INTEGER NOT NULL,       -- training rows of the final fit (all horizons)
    stocks           INTEGER NOT NULL,
    seconds          REAL NOT NULL,          -- wall time of the whole weekly fit
    peak_mb          REAL,                   -- peak resident memory of the process after the fit
    fitted_at        TEXT NOT NULL,
    PRIMARY KEY (model, version, fit_day)
) WITHOUT ROWID;
CREATE TRIGGER stk_forecast_fits_no_update BEFORE UPDATE ON stk_forecast_fits BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_forecast_fits_no_delete BEFORE DELETE ON stk_forecast_fits BEGIN SELECT RAISE(ABORT, 'append-only'); END;

-- The pickled, compressed models of the newest fits (``forecast.store.KEEP`` per model). Older ones are deleted to
-- bound the disk: a past day whose fit is no longer kept uses the baseline, never a later fit.
CREATE TABLE stk_forecast_blobs (
    model     TEXT NOT NULL,
    version   INTEGER NOT NULL,
    fit_day   INTEGER NOT NULL,
    sha       TEXT NOT NULL,
    blob      BLOB NOT NULL,
    PRIMARY KEY (model, version, fit_day)
) WITHOUT ROWID;
