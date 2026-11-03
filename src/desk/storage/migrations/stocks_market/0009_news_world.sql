-- Company news: each article once (Massive's market-wide news), when it was received, and the stocks it names with
-- Massive's sentiment for each. Point-in-time: research reads only articles received by its analysis time.
CREATE TABLE stk_news (
    id            TEXT PRIMARY KEY,
    published_at  TEXT NOT NULL,
    received_at   TEXT NOT NULL,
    source        TEXT,
    title         TEXT NOT NULL,
    url           TEXT NOT NULL,
    summary       TEXT
);
CREATE INDEX stk_news_published ON stk_news(published_at);
CREATE TABLE stk_news_tickers (
    news_id    TEXT NOT NULL,
    symbol     TEXT NOT NULL,
    sentiment  TEXT,
    reasoning  TEXT,
    PRIMARY KEY (news_id, symbol)
) WITHOUT ROWID;
CREATE INDEX stk_news_symbol ON stk_news_tickers(symbol);

-- World events: GDELT's 15-minute event files (news in 65+ languages), kept as daily totals per country and kind
-- (conflict, threats, sanctions, unrest, coercion) with the most-covered source articles of each.
CREATE TABLE stk_world_files (
    name         TEXT PRIMARY KEY,
    received_at  TEXT NOT NULL,
    events       INTEGER NOT NULL
);
CREATE TABLE stk_world_daily (
    day        INTEGER NOT NULL,
    country    TEXT NOT NULL,
    kind       TEXT NOT NULL,
    events     INTEGER NOT NULL,
    articles   INTEGER NOT NULL,
    goldstein  REAL NOT NULL,
    PRIMARY KEY (day, country, kind)
) WITHOUT ROWID;
CREATE TABLE stk_world_sources (
    day       INTEGER NOT NULL,
    country   TEXT NOT NULL,
    kind      TEXT NOT NULL,
    url       TEXT NOT NULL,
    articles  INTEGER NOT NULL,
    place     TEXT,
    PRIMARY KEY (day, country, kind, url)
) WITHOUT ROWID;

-- Presidential documents from the Federal Register (executive orders, proclamations, memoranda), with the trade,
-- sanctions, sector and country tags read from their titles and abstracts.
CREATE TABLE stk_policy_docs (
    number     TEXT PRIMARY KEY,
    published  INTEGER NOT NULL,
    signed     TEXT,
    title      TEXT NOT NULL,
    url        TEXT NOT NULL,
    subtype    TEXT,
    body       TEXT NOT NULL
);
CREATE INDEX stk_policy_published ON stk_policy_docs(published);
