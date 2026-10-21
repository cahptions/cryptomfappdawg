-- stocks-market.db: what KESTREL Stocks collected (docs/STOCKS.md). Days are proleptic ordinals (date.toordinal()).
-- Observations are append-only or insert-once; the listing registry logs its changes in stk_listing_events.

CREATE TABLE stk_listings (
    symbol      TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    exchange    TEXT NOT NULL,
    kind        TEXT NOT NULL,
    etf         INTEGER NOT NULL CHECK (etf IN (0, 1)),
    test        INTEGER NOT NULL CHECK (test IN (0, 1)),
    cik         INTEGER,
    active      INTEGER NOT NULL CHECK (active IN (0, 1)),
    first_seen  TEXT NOT NULL,
    last_seen   TEXT NOT NULL
);
CREATE INDEX stk_listings_cik ON stk_listings(cik);

CREATE TABLE stk_listing_events (
    id            INTEGER PRIMARY KEY,
    symbol        TEXT NOT NULL,
    at            TEXT NOT NULL,
    event         TEXT NOT NULL,
    content_json  TEXT NOT NULL
);

-- SEC companies: submissions metadata and the XBRL company facts the desk uses (zlib JSON; every value keeps the
-- date it was filed, so any past day can be rebuilt from what was public then)
CREATE TABLE stk_companies (
    cik              INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    sic              TEXT,
    sic_description  TEXT,
    sector           TEXT,
    fiscal_year_end  TEXT,
    category         TEXT,
    submissions_at   TEXT,
    facts_at         TEXT,
    facts_sha256     TEXT,
    facts_zlib       BLOB
);

CREATE TABLE stk_filings (
    accession         TEXT PRIMARY KEY,
    cik               INTEGER NOT NULL,
    form              TEXT NOT NULL,
    filed             INTEGER NOT NULL,
    accepted_at       TEXT,
    report_date       INTEGER,
    items             TEXT NOT NULL,
    primary_document  TEXT NOT NULL,
    first_seen        TEXT NOT NULL
);
CREATE INDEX stk_filings_cik ON stk_filings(cik, filed);
CREATE INDEX stk_filings_form ON stk_filings(form, filed);

-- end-of-day bars, stored unadjusted; splits are applied when reading
CREATE TABLE stk_bar_days (
    day          INTEGER PRIMARY KEY,
    source       TEXT NOT NULL,
    rows         INTEGER NOT NULL,
    received_at  TEXT NOT NULL
);

CREATE TABLE stk_bars (
    symbol  TEXT NOT NULL,
    day     INTEGER NOT NULL,
    open    REAL NOT NULL,
    high    REAL NOT NULL,
    low     REAL NOT NULL,
    close   REAL NOT NULL,
    volume  REAL NOT NULL,
    vwap    REAL,
    trades  INTEGER,
    PRIMARY KEY (symbol, day)
) WITHOUT ROWID;
CREATE INDEX stk_bars_day ON stk_bars(day);

CREATE TABLE stk_splits (
    symbol          TEXT NOT NULL,
    execution_date  INTEGER NOT NULL,
    split_from      REAL NOT NULL,
    split_to        REAL NOT NULL,
    received_at     TEXT NOT NULL,
    PRIMARY KEY (symbol, execution_date)
) WITHOUT ROWID;

CREATE TABLE stk_dividends (
    symbol       TEXT NOT NULL,
    ex_date      INTEGER NOT NULL,
    cash         REAL NOT NULL,
    pay_date     INTEGER,
    frequency    INTEGER,
    kind         TEXT,
    received_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, ex_date, cash)
) WITHOUT ROWID;

CREATE TABLE stk_insider_trades (
    accession    TEXT NOT NULL,
    owner_cik    INTEGER NOT NULL,
    line         INTEGER NOT NULL,
    issuer_cik   INTEGER NOT NULL,
    symbol       TEXT NOT NULL,
    owner        TEXT NOT NULL,
    director     INTEGER NOT NULL,
    officer      INTEGER NOT NULL,
    title        TEXT,
    ten_percent  INTEGER NOT NULL,
    day          INTEGER NOT NULL,
    code         TEXT NOT NULL,
    shares       REAL NOT NULL,
    price        REAL,
    acquired     INTEGER NOT NULL,
    owned_after  REAL,
    direct       INTEGER NOT NULL,
    planned      INTEGER NOT NULL,
    filed        INTEGER NOT NULL,
    PRIMARY KEY (accession, owner_cik, line)
);
CREATE INDEX stk_insider_symbol ON stk_insider_trades(symbol, filed);
CREATE INDEX stk_insider_filed ON stk_insider_trades(filed);

CREATE TABLE stk_short_interest (
    symbol         TEXT NOT NULL,
    settlement     INTEGER NOT NULL,
    short_shares   REAL NOT NULL,
    previous       REAL,
    avg_volume     REAL,
    days_to_cover  REAL,
    received_at    TEXT NOT NULL,
    PRIMARY KEY (symbol, settlement)
) WITHOUT ROWID;

CREATE TABLE stk_short_volume (
    symbol        TEXT NOT NULL,
    day           INTEGER NOT NULL,
    short_volume  REAL NOT NULL,
    exempt        REAL NOT NULL,
    total         REAL NOT NULL,
    PRIMARY KEY (symbol, day)
) WITHOUT ROWID;

CREATE TABLE stk_fails (
    symbol      TEXT NOT NULL,
    settlement  INTEGER NOT NULL,
    cusip       TEXT NOT NULL,
    quantity    REAL NOT NULL,
    price       REAL,
    PRIMARY KEY (symbol, settlement)
) WITHOUT ROWID;

-- crowd mentions, one snapshot an hour per source (hour = epoch hours, UTC)
CREATE TABLE stk_mentions (
    source        TEXT NOT NULL,
    hour          INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    mentions      INTEGER NOT NULL,
    upvotes       INTEGER NOT NULL,
    rank_24h      INTEGER,
    mentions_24h  INTEGER,
    PRIMARY KEY (source, hour, symbol)
) WITHOUT ROWID;
CREATE INDEX stk_mentions_symbol ON stk_mentions(symbol, hour);

CREATE TABLE stk_rates (
    series  TEXT NOT NULL,
    day     INTEGER NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (series, day)
) WITHOUT ROWID;

-- option chains as collected (zlib JSON; raw chains are pruned after 45 days, daily summaries are kept forever)
CREATE TABLE stk_option_chains (
    underlying        TEXT NOT NULL,
    day               INTEGER NOT NULL,
    expiration        INTEGER NOT NULL,
    observed_at       TEXT NOT NULL,
    underlying_price  REAL,
    contracts         INTEGER NOT NULL,
    chain_zlib        BLOB NOT NULL,
    PRIMARY KEY (underlying, day, expiration)
) WITHOUT ROWID;

CREATE TABLE stk_option_daily (
    underlying    TEXT NOT NULL,
    day           INTEGER NOT NULL,
    observed_at   TEXT NOT NULL,
    summary_json  TEXT NOT NULL,
    PRIMARY KEY (underlying, day)
) WITHOUT ROWID;

CREATE TABLE stk_cusips (
    cusip      TEXT PRIMARY KEY,
    symbol     TEXT,
    mapped_at  TEXT NOT NULL
);

-- what each collection job last did (the System page and doctor read this)
CREATE TABLE stk_jobs (
    name         TEXT PRIMARY KEY,
    last_run_at  TEXT,
    last_ok_at   TEXT,
    last_error   TEXT,
    detail_json  TEXT NOT NULL DEFAULT '{}'
);

-- frozen, versioned methodology: a changed rule needs a new version
CREATE TABLE stk_specs (
    spec_sha256    TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    version        INTEGER NOT NULL,
    registered_at  TEXT NOT NULL,
    content_json   TEXT NOT NULL,
    UNIQUE (name, version)
);

CREATE TRIGGER stk_listing_events_no_update BEFORE UPDATE ON stk_listing_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_listing_events_no_delete BEFORE DELETE ON stk_listing_events BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_bars_no_update BEFORE UPDATE ON stk_bars BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_insider_no_update BEFORE UPDATE ON stk_insider_trades BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_specs_no_update BEFORE UPDATE ON stk_specs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER stk_specs_no_delete BEFORE DELETE ON stk_specs BEGIN SELECT RAISE(ABORT, 'append-only'); END;
