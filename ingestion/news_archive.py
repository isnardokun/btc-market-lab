"""Archive only provenance metadata of discovered news for historical queries.

This does NOT claim old RSS/Exa history exists. Do not save copyrighted full
article text or private API responses. News is a discovery lead, not verified
economic data and must retain source type and observed time.
"""
import datetime as dt
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit
from scripts.market_r10e11_writer_exclusion import hold_writer_lock

GLOBAL_LOCK_NAMESPACE = "btc-research-global-writer-v1"

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_news_archive (
    report_date TEXT NOT NULL,
    asset TEXT NOT NULL,
    url TEXT NOT NULL,
    published_label TEXT,
    title TEXT NOT NULL,
    source TEXT,
    source_type TEXT,
    discovered_at_utc TEXT NOT NULL,
    PRIMARY KEY(report_date, asset, url)
);
CREATE INDEX IF NOT EXISTS idx_news_history_asset_date
ON research_news_archive(asset, report_date);
"""


def archive_news(db_path, report_date, grouped_news, *, at=None, lock_directory=None):
    """Archive discovery URLs; optionally hold the shared cooperative lock.

    Lock contention raises WriterLockBusy BEFORE checking SQLite existence.
    Do not convert that exception into a numeric count or silently fall back
    to writing unlocked. Library callers without the opt-in remain legacy.
    """
    if lock_directory is None:
        return _archive_news_unlocked(db_path, report_date, grouped_news, at=at)
    with hold_writer_lock(lock_directory, namespace=GLOBAL_LOCK_NAMESPACE):
        return _archive_news_unlocked(db_path, report_date, grouped_news, at=at)


def _archive_news_unlocked(db_path, report_date, grouped_news, *, at=None):
    if not Path(db_path).is_file():
        raise FileNotFoundError("No SQLite local disponible")
    stamp = (at or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat()
    count = 0
    with sqlite3.connect(db_path) as db:
        db.executescript(SCHEMA)
        for asset, news in grouped_news.items():
            if asset not in ("BTC","SPY","GOLD","MACRO"):
                continue
            for item in news:
                url = str(item.get("url") or "").strip()
                parts = urlsplit(url)
                if (parts.scheme != "https" or not parts.netloc
                        or parts.username or parts.password or len(url) > 2048):
                    continue
                title = str(item.get("title") or "").strip()[:350]
                if not title:
                    continue
                cur=db.execute(
                    "INSERT OR IGNORE INTO research_news_archive "
                    "(report_date,asset,url,published_label,title,source,source_type,discovered_at_utc) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (str(report_date),asset,url,str(item.get("date") or "")[:80],
                     title,str(item.get("source") or "")[:120],
                     str(item.get("source_type") or "discovery")[:48],stamp))
                count += cur.rowcount
    return count
