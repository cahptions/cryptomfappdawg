-- Superseded chain snapshots remain available for as-of decision reads.
CREATE TABLE stk_option_chain_history (
    underlying TEXT NOT NULL,
    day INTEGER NOT NULL,
    expiration INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    underlying_price REAL,
    contracts INTEGER NOT NULL,
    chain_zlib BLOB NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (underlying, day, expiration, observed_at)
) WITHOUT ROWID;
CREATE INDEX stk_option_chain_history_day ON stk_option_chain_history(day);
CREATE INDEX stk_option_chains_day_cutoff ON stk_option_chains(day, observed_at);

CREATE TRIGGER stk_option_chain_capture BEFORE INSERT ON stk_option_chains
BEGIN
    INSERT OR IGNORE INTO stk_option_chain_history
        (underlying, day, expiration, observed_at, underlying_price, contracts, chain_zlib, source)
    SELECT underlying, day, expiration, observed_at, underlying_price, contracts, chain_zlib, source
    FROM stk_option_chains
    WHERE underlying=NEW.underlying AND day=NEW.day AND expiration=NEW.expiration
      AND observed_at<NEW.observed_at;
END;
