-- The lotto replay on real prices: for each name and weekly expiry, the out-of-the-money contracts Massive listed two
-- weeks before it, and the ones chosen for download. Their daily prices go in stk_option_bars like the replay's.
CREATE TABLE stk_lotto_lists (
    symbol     TEXT NOT NULL,
    expiry     INTEGER NOT NULL,
    as_of      INTEGER NOT NULL,
    listed     INTEGER NOT NULL,
    contracts  TEXT NOT NULL,
    note       TEXT,
    listed_at  TEXT NOT NULL,
    PRIMARY KEY (symbol, expiry)
) WITHOUT ROWID;
