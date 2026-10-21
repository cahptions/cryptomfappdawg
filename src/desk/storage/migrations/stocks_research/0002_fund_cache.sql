-- Point-in-time fundamentals that do not depend on the price, per company and day. A value for a past day depends
-- only on filings made before it, so a cached day never needs recomputing.
CREATE TABLE stk_fund_cache (
    cik        INTEGER NOT NULL,
    asof       INTEGER NOT NULL,
    base_json  TEXT NOT NULL,
    PRIMARY KEY (cik, asof)
) WITHOUT ROWID;
