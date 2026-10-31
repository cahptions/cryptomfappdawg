-- Real daily option prices from Massive (Options Basic: the last 2 years) for the contracts historical replays trade.
CREATE TABLE stk_option_bars (
    occ     TEXT NOT NULL,
    day     INTEGER NOT NULL,
    open    REAL,
    high    REAL,
    low     REAL,
    close   REAL NOT NULL,
    volume  REAL,
    PRIMARY KEY (occ, day)
) WITHOUT ROWID;

-- What was asked for each contract and through which day, so a quiet or unlisted contract is not asked again.
CREATE TABLE stk_option_bar_fetches (
    occ         TEXT PRIMARY KEY,
    first       INTEGER NOT NULL,
    last        INTEGER NOT NULL,
    bars        INTEGER NOT NULL,
    fetched_at  TEXT NOT NULL
) WITHOUT ROWID;
