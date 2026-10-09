"""Bounded public-source clients. Source bodies are archived in SQLite.

No API keys for Binance/Bybit/Farside/BLS. FRED uses the existing FRED key.
Collectors never claim complete historical coverage without inspecting data.
"""
import datetime as dt
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from storage.market_context import raw_payload, store_derivative, store_etf, store_event

SOURCE_URLS={
 "binance":"https://fapi.binance.com",
 "bybit":"https://api.bybit.com",
 "farside":"https://farside.co.uk/bitcoin-etf-flow-all-data/",
 "bls":"https://www.bls.gov/schedule/news_release/bls.ics",
 "fred":"https://api.stlouisfed.org/fred/releases/dates",
}
ALLOWED_HOSTS={urlsplit(x).hostname for x in SOURCE_URLS.values()}

def download(url,params=None,*,opener=urlopen):
    parsed=urlsplit(url)
    if parsed.scheme!="https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username:
        raise ValueError("External source not allowlisted")
    q=urlencode(params or {})
    target=url+("?" + q if q else "")
    req=Request(target,headers={"User-Agent":"BTCMarketLab/2.0 research collector",
                                "Accept":"application/json,text/html,text/calendar"})
    with opener(req,timeout=15) as res:
        final=urlsplit(getattr(res,"url",target))
        if final.scheme!="https" or final.hostname!=parsed.hostname:
            raise ValueError("Source redirected outside approved hostname")
        body=res.read(2_500_001)
        if len(body)>2_500_000 or not body:
            raise ValueError("Source payload oversized or empty")
    # Do not store URL query string containing FRED key in provenance.
    return body

def parse_api_json(body):
    value=json.loads(body)
    if not isinstance(value,(list,dict)):
        raise ValueError("Unexpected API response")
    return value

def _ms(v):
    return dt.datetime.fromtimestamp(int(v)/1000,dt.timezone.utc).isoformat()

def fetch_binance(db,*,fetch=download,history=False,max_pages=2):
    """Funding settled is paginated; OI is 5m snapshot, 30d provider limit."""
    requests=points=0
    oi_url=SOURCE_URLS["binance"]+"/futures/data/openInterestHist"
    oi_params=dict(symbol="BTCUSDT",period="5m",limit=500)
    body=fetch(oi_url,oi_params);requests+=1
    rows=parse_api_json(body)
    if not isinstance(rows,list):raise ValueError("Binance OI is not an array")
    sha=raw_payload(db,"binance",oi_url,body,"application/json")
    for row in rows:
        if not isinstance(row,dict) or row.get("symbol") not in (None,"BTCUSDT"):raise ValueError("Binance symbol mismatch")
        points+=int(store_derivative(db,provider="binance",symbol="BTCUSDT",
             metric="open_interest",observed_utc=_ms(row["timestamp"]),
             interval_label="5m",value=row["sumOpenInterest"],unit="BTC",
             endpoint=oi_url,sha=sha,
             quote_usd=row.get("sumOpenInterestValue")))
    endpoint=SOURCE_URLS["binance"]+"/fapi/v1/fundingRate"
    newest=db.execute("SELECT MAX(observed_utc) FROM market_derivatives WHERE provider='binance' AND metric='funding_settled' AND symbol='BTCUSDT'").fetchone()[0]
    start_ms=(int(dt.datetime.fromisoformat(newest).timestamp()*1000)+1 if newest and not history
              else (int(dt.datetime(2019,1,1,tzinfo=dt.timezone.utc).timestamp()*1000) if history
              else int((dt.datetime.now(dt.timezone.utc)-dt.timedelta(days=45)).timestamp()*1000)))
    for _ in range(max_pages):
        body=fetch(endpoint,{"symbol":"BTCUSDT","startTime":start_ms,"limit":1000});requests+=1
        rows=parse_api_json(body)
        if not isinstance(rows,list):raise ValueError("Binance funding must be array")
        sha=raw_payload(db,"binance",endpoint,body,"application/json")
        last=start_ms
        for row in rows:
            if row.get("symbol")!="BTCUSDT":raise ValueError("Binance funding symbol mismatch")
            stamp=int(row["fundingTime"])
            if stamp<start_ms:raise ValueError("Nonmonotonic funding response")
            last=max(last,stamp)
            points+=int(store_derivative(db,provider="binance",symbol="BTCUSDT",
                metric="funding_settled",observed_utc=_ms(stamp),
                interval_label="settlement",value=row["fundingRate"],unit="fraction",
                endpoint=endpoint,sha=sha))
        if len(rows)<1000 or last<start_ms:break
        start_ms=last+1
    return requests,points

def fetch_bybit(db,*,fetch=download,max_pages=2):
    """OI Bybit BTCUSDT linear raw units BTC; funding settled not projected."""
    requests=points=0
    root=SOURCE_URLS["bybit"]
    for metric,endpoint,params in (
      ("open_interest",root+"/v5/market/open-interest",
       {"category":"linear","symbol":"BTCUSDT","intervalTime":"5min","limit":200}),
      ("funding_settled",root+"/v5/market/funding/history",
       {"category":"linear","symbol":"BTCUSDT","limit":200})
    ):
        cursor=None; end=None;seen=set()
        for _ in range(max_pages):
            query=dict(params)
            if cursor and metric=="open_interest":query["cursor"]=cursor
            if end is not None and metric=="funding_settled":query["endTime"]=end
            body=fetch(endpoint,query);requests+=1
            result=parse_api_json(body)
            if not isinstance(result,dict) or result.get("retCode")!=0:
                raise ValueError("Bybit nonzero code or malformed response")
            part=result.get("result")
            if not isinstance(part,dict) or part.get("category") not in (None,"linear") or part.get("symbol") not in (None,"BTCUSDT"):
                raise ValueError("Bybit unexpected result metadata")
            rows=part.get("list")
            if not isinstance(rows,list):raise ValueError("Bybit missing list")
            sha=raw_payload(db,"bybit",endpoint,body,"application/json")
            oldest=None
            for row in rows:
                stamp=int(row["timestamp"] if metric=="open_interest" else row["fundingRateTimestamp"])
                if stamp in seen: continue
                seen.add(stamp)
                oldest=stamp if oldest is None else min(stamp,oldest)
                points+=int(store_derivative(db,provider="bybit",symbol="BTCUSDT",
                   metric=metric,observed_utc=_ms(stamp),
                   interval_label="5m" if metric=="open_interest" else "settlement",
                   value=row["openInterest"] if metric=="open_interest" else row["fundingRate"],
                   unit="BTC" if metric=="open_interest" else "fraction",endpoint=endpoint,sha=sha))
            if not rows:break
            if metric=="open_interest":
                nxt=part.get("nextPageCursor")
                if not nxt or nxt==cursor:break
                cursor=nxt
            else:
                if oldest is None or (end is not None and oldest>=end):break
                end=oldest-1
                if len(rows)<200:break
    return requests,points


class TableReader(HTMLParser):
    """Collect HTML table cells without copying rendered scripts as values."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables=[];self.table=None;self.row=None;self.cell=None
        self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in ("script","style"):self.hidden+=1
        if tag=="table":self.table=[] 
        if self.table is not None and tag=="tr":self.row=[]
        if self.row is not None and tag in ("th","td"):self.cell=[]
    def handle_data(self,data):
        if not self.hidden and self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ("script","style"):self.hidden=max(0,self.hidden-1)
        if tag in ("th","td") and self.cell is not None and self.row is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell=None
        if tag=="tr" and self.row is not None and self.table is not None:
            self.table.append(self.row);self.row=None
        if tag=="table" and self.table is not None:
            self.tables.append(self.table);self.table=None

def _flow_num(cell):
    raw=cell.strip().replace(",","").replace("\u2212","-")
    if raw in ("","-","N/A","n/a","—"):return None
    if raw.startswith("(") and raw.endswith(")"):raw="-"+raw[1:-1]
    try:
        val=float(raw)
    except ValueError as exc:
        raise ValueError("Non numeric ETF cell") from exc
    if not -1000000<=val<=1000000:raise ValueError("Suspicious ETF amount")
    return val

def parse_farside(body):
    reader=TableReader();reader.feed(body.decode("utf-8"))
    for table in reader.tables:
        if len(table)<2:continue
        hdr=[h.upper().strip() for h in table[0]]
        if hdr[0]!="DATE" or "IBIT" not in hdr or "FBTC" not in hdr or "TOTAL" not in hdr:
            continue
        tickers=hdr[1:]
        if any(not re.fullmatch(r"[A-Z0-9]{2,8}",x) for x in tickers):
            raise ValueError("Unrecognized Farside ETF columns")
        results=[]
        for row in table[1:]:
            if not row:continue
            try:
                day=dt.datetime.strptime(row[0].strip(),"%d %b %Y").date()
            except ValueError:
                continue # total footer/summary, not a transaction day
            if len(row)!=len(hdr):raise ValueError("Incomplete Farside daily row")
            parsed={name:_flow_num(cell) for name,cell in zip(tickers,row[1:])}
            # TOTAL is independently sourced; do not synthesize missing cells.
            for ticker,value in parsed.items():
                if value is not None and ticker!="TOTAL":
                    results.append((day.isoformat(),ticker,value))
        if not results:raise ValueError("Empty Farside data table")
        return results
    raise ValueError("ETF source table not found or changed")

def fetch_farside(db,*,fetch=download):
    url=SOURCE_URLS["farside"];body=fetch(url)
    rows=parse_farside(body)
    sha=raw_payload(db,"farside",url,body,"text/html")
    return 1,sum(int(store_etf(db,provider="farside",trade_date=date,
                   ticker=ticker,amount_m=value,state="preliminary",sha=sha))
                 for date,ticker,value in rows)


def _unfold_ical(text):
    out=[]
    for line in text.replace("\r\n","\n").split("\n"):
        if line.startswith((" ","\t")) and out:
            out[-1]+=line[1:]
        else:out.append(line)
    return out

def parse_bls_calendar(body):
    text=body.decode("utf-8-sig")
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise ValueError("No valid iCalendar")
    entries=[];event=None
    for line in _unfold_ical(text):
        if line=="BEGIN:VEVENT":event={}
        elif line=="END:VEVENT":
            if event:
                uid=event.get("UID");title=event.get("SUMMARY","").replace("\\, ",", ")
                entry=event.get("DTSTART")
                if uid and title and entry:
                    key,value=entry
                    precision="unconfirmed";stamp=None
                    if "VALUE=DATE" in key or re.fullmatch(r"\d{8}",value):
                        date=dt.datetime.strptime(value[:8],"%Y%m%d").date()
                        precision="date_only"
                    elif value.endswith("Z"):
                        parsed=dt.datetime.strptime(value,"%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
                        stamp=parsed.isoformat();date=parsed.date();precision="utc"
                    elif "TZID=America/New_York" in key or "TZID=US/Eastern" in key:
                        local=dt.datetime.strptime(value,"%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo("America/New_York"))
                        utc=local.astimezone(dt.timezone.utc)
                        stamp=utc.isoformat();date=utc.date();precision="utc"
                    else:
                        # Unknown timezone MUST NOT be guessed.
                        date=dt.datetime.strptime(value[:8],"%Y%m%d").date()
                        precision="unconfirmed"
                    state="cancelled" if event.get("STATUS")=="CANCELLED" else "scheduled"
                    entries.append((uid,title,date.isoformat(),stamp,precision,state))
            event=None
        elif event is not None and ":" in line:
            k,v=line.split(":",1)
            event[k]=v
            if k.startswith("DTSTART"):event["DTSTART"]=(k,v)
    if not entries:raise ValueError("Calendar contains no verifiable events")
    return entries

def fetch_bls(db,*,fetch=download):
    url=SOURCE_URLS["bls"];body=fetch(url)
    events=parse_bls_calendar(body)
    sha=raw_payload(db,"bls",url,body,"text/calendar")
    return 1,sum(int(store_event(db,provider="bls",uid=uid,title=title,
         event_date=date,scheduled_utc=stamp,precision=precision,
         source_url="https://www.bls.gov/schedule/news_release/",sha=sha,state=state))
         for uid,title,date,stamp,precision,state in events)

def fetch_fred(db,*,fetch=download,api_key=None):
    if not api_key:return 0,0
    url=SOURCE_URLS["fred"]
    # FRED releases have source calendar dates, NOT confirmed release hours.
    body=fetch(url,{"api_key":api_key,"file_type":"json","limit":1000,
                    "sort_order":"desc","include_release_dates_with_no_data":"true"})
    result=parse_api_json(body)
    if not isinstance(result,dict) or not isinstance(result.get("release_dates"),list):
        raise ValueError("FRED dates malformed")
    sha=raw_payload(db,"fred",url,body,"application/json")
    events=0
    for row in result["release_dates"]:
        when=row.get("date","")
        try: day=dt.date.fromisoformat(when)
        except (TypeError,ValueError):continue
        title=str(row.get("release_name",""))
        rid=row.get("release_id")
        if not title or not isinstance(rid,int):continue
        events+=int(store_event(db,provider="fred",uid=str(rid)+":"+day.isoformat(),
                    title=title,event_date=day.isoformat(),scheduled_utc=None,
                    precision="date_only",
                    source_url="https://fred.stlouisfed.org/docs/api/fred/releases_dates.html",sha=sha))
    return 1,events
