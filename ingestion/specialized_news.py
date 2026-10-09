"""Curated, non-paywalled RSS sources for market research.

Feeds are original-source headlines and short publisher-supplied descriptions,
not independently verified numerical market facts. No article scraping.
The legacy Exa path continues to work if any feed is blocked or unavailable.
"""
import datetime as dt
import email.utils
import html
import os
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Source:
    name: str
    feed: str
    asset: str
    category: str
    domain: str
    max_age_days: int = 10
    priority: int = 1


# Checked against the publishers' official RSS index pages. An HTTP 403 or
# changed format is a non-fatal source failure, NOT a reason to fabricate news.
SOURCES = (
    Source("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml",
           "SPY", "monetary_policy", "www.federalreserve.gov", 14, 0),
    Source("BLS", "https://www.bls.gov/feed/bls_latest.rss",
           "SPY", "macro_release", "www.bls.gov", 14, 0),
    Source("Coin Metrics State of the Network", "https://coinmetrics.substack.com/feed",
           "BTC", "onchain_research", "coinmetrics.substack.com", 21, 1),
)

MAX_XML_BYTES = 500_000
ATOM = "{http://www.w3.org/2005/Atom}"


def clean_text(content, limit=360):
    # RSS descriptions may contain embedded markup. Never include their HTML.
    text = html.unescape(re.sub(r"<[^>]*>", " ", content or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    snippet = text[:limit].rsplit(" ", 1)[0]
    return snippet.rstrip(" ,.;:") + "…"


def parse_date(value):
    if not value:
        return None
    try:
        date = email.utils.parsedate_to_datetime(value)
    except (ValueError, TypeError):
        try:
            date = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    if date.tzinfo is None:
        date = date.replace(tzinfo=dt.timezone.utc)
    return date.astimezone(dt.timezone.utc)


def _child_text(item, tags):
    for tag in tags:
        node = item.find(tag)
        if node is not None:
            value = "".join(node.itertext()).strip()
            if value:
                return value
    return ""


def parse_feed(payload, source, *, now=None, limit=5):
    """Allowlisted publisher RSS 2.0 or Atom XML, bounded and date-aware."""
    if len(payload) > MAX_XML_BYTES or b"<!DOCTYPE" in payload.upper() or b"<!ENTITY" in payload.upper():
        raise ValueError("Feed demasiado grande o DTD no permitido")
    now = now or dt.datetime.now(dt.timezone.utc)
    if now.tzinfo is None:
        raise ValueError("UTC-aware now required")
    now = now.astimezone(dt.timezone.utc)
    root = ET.fromstring(payload)
    entries = root.findall("./channel/item")
    if not entries:
        entries = root.findall(f"{ATOM}entry")
    result = []
    for entry in entries[: max(30, limit * 5)]:
        title = clean_text(_child_text(entry, ("title", f"{ATOM}title")), 180)
        desc = clean_text(_child_text(entry, ("description", f"{ATOM}summary",
                                              f"{ATOM}content")), 360)
        url = _child_text(entry, ("link",))
        if not url:
            link = entry.find(f"{ATOM}link")
            url = link.attrib.get("href", "") if link is not None else ""
        # Publisher's feed may have off-site links. Retain only original domain.
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            host == source.domain or host.endswith("." + source.domain.lstrip("www."))
        ):
            continue
        date_raw = _child_text(entry, ("pubDate", f"{ATOM}published", f"{ATOM}updated"))
        stamp = parse_date(date_raw)
        if stamp is None or stamp > now + dt.timedelta(minutes=3):
            continue
        days_old = (now.date() - stamp.date()).days
        if days_old > source.max_age_days:
            continue
        if len(title) < 20 or len(desc) < 50:
            continue
        result.append({
            "title": title,
            "url": url,
            "published": stamp.isoformat(),
            "date": stamp.date().isoformat(),
            "highlight": desc,
            "source": source.name,
            "source_type": "primary" if source.priority == 0 else "research",
            "source_category": source.category,
        })
    result.sort(key=lambda x: x["published"], reverse=True)
    return result[:limit]


def fetch_feed(source, opener=urlopen, now=None):
    """No tokens; constrained HTTP, response and URL domain."""
    req = Request(source.feed, headers={
        "User-Agent": "BTCMarketLabResearch/1.0 (public RSS reader)",
        "Accept": "application/rss+xml, application/atom+xml, application/xml",
    })
    with opener(req, timeout=9) as response:
        final_url = getattr(response, "url", source.feed)
        if urlsplit(final_url).hostname != source.domain:
            raise ValueError("Feed redirigido a un dominio no autorizado")
        body = response.read(MAX_XML_BYTES + 1)
    return parse_feed(body, source, now=now)


def collect_specialized_news(assets, *, now=None, fetcher=fetch_feed):
    """Independent sources never suppress Exa results on failure."""
    if os.getenv("NEWS_RSS_ENABLED", "0") != "1":
        return {asset: [] for asset in assets}
    output = {asset: [] for asset in assets}
    for source in SOURCES:
        if source.asset not in output:
            continue
        try:
            output[source.asset].extend(fetcher(source, now=now))
        except Exception as exc:
            # Exclude source bodies/URL/headers from logs.
            print(f"[news/rss] {source.name}: no disponible ({type(exc).__name__})",
                  file=sys.stderr)
    return output
