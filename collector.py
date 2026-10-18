"""Public-page collector for personal use, fail closed on robots and access restrictions.

Never logs in, bypasses access controls, or posts agreement on the user's behalf.
Only URLs linked by the site's public store/model pages are considered.
"""
from __future__ import annotations
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from bs4 import BeautifulSoup

BASE = "https://daidata.goraggio.com"
HOST = "daidata.goraggio.com"
UA = "PalazzoKanagawaPersonalReader/0.2 (small-scale private research; robots.txt compliant)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"}
MAX_BYTES = 1_200_000
MIN_DELAY = 3.0
MAX_REQUESTS_PER_STORE = 26

class CollectionError(RuntimeError):
    pass

def normalized_text(x):
    return re.sub(r"\s+", "", str(x or "")).strip()

def parse_int(x):
    s = normalized_text(x).replace(",", "").replace("−", "-").replace("－", "-").replace("＋", "+")
    if not re.fullmatch(r"[+]?-?\d+", s):
        return None
    return int(s)

def url_allowed(url: str, store_number: str) -> bool:
    try:
        parts=urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == HOST
                and parts.port in (None, 443)
                and (parts.path == "/" + store_number
                     or parts.path.startswith("/" + store_number + "/")))
    except ValueError:
        return False

def robots_policy(timeout: int=12):
    """Robots retrieval failure is not an invitation to continue."""
    url=BASE+"/robots.txt"
    req=urllib.request.Request(url,headers=HEADERS)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:
            if response.status != 200:
                raise CollectionError("robots.txt のHTTP "+str(response.status))
            body=response.read(150_000)
            if len(body)>=150_000:
                raise CollectionError("robots.txt が大きすぎます")
            text=body.decode("utf-8","replace")
            if not text.strip():
                raise CollectionError("robots.txt の内容が空です。安全のため取得を停止します")
    except urllib.error.HTTPError as e:
        raise CollectionError("robots.txt がHTTP "+str(e.code)+"を返しました")
    except (urllib.error.URLError,TimeoutError,OSError) as e:
        raise CollectionError("robots.txt に接続できません: "+str(e)[:140])
    parser=urllib.robotparser.RobotFileParser()
    parser.parse(text.splitlines())
    return parser

class Reader:
    def __init__(self, robots, store_number, limit=MAX_REQUESTS_PER_STORE):
        self.robots=robots
        self.store_number=store_number
        self.limit=limit
        self.requests=0
        self.last=0.0
        self.visited=set()

    def get(self,url):
        if not url_allowed(url,self.store_number):
            raise CollectionError("店舗外URLにはアクセスしません")
        if not self.robots.can_fetch(UA,url):
            raise CollectionError("robots.txt で取得が認められていません")
        if self.requests >= self.limit:
            raise CollectionError("1店舗あたりのアクセス上限に到達")
        if url in self.visited:
            raise CollectionError("同じページへの重複アクセス")
        delta=MIN_DELAY-(time.monotonic()-self.last)
        if self.last and delta>0:
            time.sleep(delta)
        self.requests+=1
        self.last=time.monotonic()
        self.visited.add(url)
        req=urllib.request.Request(url,headers=HEADERS)
        try:
            with urllib.request.urlopen(req,timeout=18) as response:
                actual=response.geturl()
                if not url_allowed(actual,self.store_number):
                    raise CollectionError("許可した店舗外へ転送されました（ログイン・同意画面の可能性）")
                if response.status != 200:
                    raise CollectionError("HTTP "+str(response.status))
                body=response.read(MAX_BYTES+1)
                if len(body)>MAX_BYTES:
                    raise CollectionError("HTMLサイズ上限")
                encoding=response.headers.get_content_charset() or "utf-8"
                return body.decode(encoding,"replace")
        except urllib.error.HTTPError as e:
            if e.code in (401,403,429):
                raise CollectionError("HTTP "+str(e.code)+"。アクセスが制限されています。再試行や回避は行いません")
            raise CollectionError("HTTP "+str(e.code))
        except (urllib.error.URLError,TimeoutError,OSError) as e:
            raise CollectionError("接続失敗: "+str(e)[:140])

def linked_pages(soup,store_number):
    """Select only linked same-store pages; never guess undocumented parameters."""
    result=[]
    seen=set()
    for anchor in soup.select("a[href]"):
        href=anchor.get("href") or ""
        url=urllib.parse.urljoin(BASE+"/"+store_number+"/",href)
        if not url_allowed(url,store_number):
            continue
        parsed=urllib.parse.urlsplit(url)
        path=parsed.path.lower()
        if re.search(r"/(unit_list|model_list|machine_list|model_search|slot|search|unit_search|machine_search)$",path) or (
            "model=" in parsed.query and "unit" in path):
            if url not in seen:
                result.append(url)
                seen.add(url)
    return result

def infer_model(soup,url):
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    model=(query.get("model") or query.get("machine") or [""])[0].strip()
    if model:
        return model[:100]
    for selector in ("h1","h2","h3",".model_name",".machine_name"):
        header=soup.select_one(selector)
        if header:
            text=header.get_text(" ",strip=True)
            if text and "台データ" not in text and "店舗" not in text:
                return text[:100]
    return ""

def parse_page(html, store_id, model_name, date):
    """Recognize explicit table headers; do not infer counts from unrelated cells."""
    soup=BeautifulSoup(html,"html.parser")
    result=[]
    for table in soup.select("table"):
        lines=table.select("tr")
        if not lines:
            continue
        headers=[normalized_text(x.get_text(" ",strip=True)) for x in lines[0].find_all(["td","th"],recursive=False)]
        def find(candidates):
            return next((i for i,h in enumerate(headers) if any(c in h for c in candidates)), None)
        col_unit=find(("台番","台番号"))
        col_games=find(("累計スタート","総回転","総ゲーム","通常時G","ゲーム数","総G数"))
        col_big=find(("BB","BIG"))
        col_reg=find(("RB","REG"))
        col_diff=find(("差枚数","差枚"))
        if col_unit is None or col_games is None or all(x is None for x in (col_big,col_reg,col_diff)):
            continue
        model=model_name
        if not model:
            h=table.find_previous(["h2","h3","h4"])
            model=h.get_text(" ",strip=True)[:100] if h else ""
        if not model:
            continue
        for line in lines[1:]:
            cells=[c.get_text(" ",strip=True) for c in line.find_all("td",recursive=False)]
            if len(cells)<=max(col_unit,col_games):
                continue
            unit=re.sub(r"[^0-9]","",cells[col_unit])
            games=parse_int(cells[col_games])
            if not unit or games is None or games<0 or games>150000:
                continue
            def val(col):
                return parse_int(cells[col]) if col is not None and col<len(cells) else None
            big=val(col_big);reg=val(col_reg);diff=val(col_diff)
            if (big is not None and not 0<=big<=400) or (reg is not None and not 0<=reg<=400):
                continue
            result.append({"store_id":store_id,"date":date,"machine":model,"unit_no":unit,
                           "games":games,"bb":big,"rb":reg,"diff":diff})
    # Keep only unique records.
    unique={}
    for row in result:
        key=(row["store_id"],row["date"],row["machine"],row["unit_no"])
        unique[key]=row
    return list(unique.values())

def scan_one(store, robots, date):
    reader=Reader(robots,store["source_id"])
    root=store["url"]
    html=reader.get(root)
    if "利用規約に同意" in html and not re.search(r"unit_list|model_list|機種",html):
        raise CollectionError("利用規約の同意画面です。自動で同意操作は行いません")
    soup=BeautifulSoup(html,"html.parser")
    to_check=linked_pages(soup,store["source_id"])
    direct=parse_page(html,store["id"],"",date)
    if direct:
        return {"rows":direct,"scanned":reader.requests,"linked":len(to_check),
                "note":"店舗ページに台データを確認"}
    if not to_check:
        raise CollectionError("店舗ページから機種別へのリンクを見つけられません。HTML確認が必要です")
    results=[]
    queued=set(to_check)
    idx=0
    while idx<len(to_check) and reader.requests<reader.limit:
        url=to_check[idx]
        idx+=1
        html=reader.get(url)
        new_soup=BeautifulSoup(html,"html.parser")
        model=infer_model(new_soup,url)
        hits=parse_page(html,store["id"],model,date)
        results.extend(hits)
        for link in linked_pages(new_soup,store["source_id"]):
            if link not in queued and link not in reader.visited and len(to_check)<75:
                to_check.append(link);queued.add(link)
    unique={(r["machine"],r["unit_no"]):r for r in results}
    return {"rows":list(unique.values()),"scanned":reader.requests,"linked":len(to_check),
            "note":"到達できた機種別ページを収集" if unique else "該当ヘッダーの台データ表が見つかりません"}

def alternative_diagnostics():
    """Check public robots.txt without requesting report pages."""
    results={}
    for host in ("slodata-lab.com","www.slodata-lab.com","ana-slo.com","slo-navi.com"):
        url="https://"+host+"/robots.txt"
        req=urllib.request.Request(url,headers={"User-Agent":UA})
        try:
            with urllib.request.urlopen(req,timeout=10) as response:
                code=response.status
                body=response.read(150000).decode("utf-8","replace")
                if code!=200:
                    results[host]={"status":"blocked","message":"HTTP "+str(code)}
                    continue
                if not body.strip():
                    results[host]={"status":"unknown","message":"robots.txtが空です"}
                    continue
                policy=urllib.robotparser.RobotFileParser()
                policy.parse(body.splitlines())
                sample="https://"+host+"/pages/reports/report-slot-2026-07-26-3251615-6eb51694.html"
                results[host]={"status":"ok","can_fetch_reports":policy.can_fetch(UA,sample)}
        except urllib.error.HTTPError as error:
            results[host]={"status":"blocked","message":"HTTP "+str(error.code)}
        except Exception as error:
            results[host]={"status":"error","message":str(error)[:140]}
    return results

def diagnostics():
    """A single robots.txt request. No page access or bypass."""
    try:
        policy=robots_policy()
        allowed={str(s["source_id"]):policy.can_fetch(UA,s["url"]) for s in [{"source_id":"100812","url":BASE+"/100812"},
                                 {"source_id":"100943","url":BASE+"/100943"},
                                 {"source_id":"100944","url":BASE+"/100944"},
                                 {"source_id":"100927","url":BASE+"/100927"},
                                 {"source_id":"100956","url":BASE+"/100956"}]}
        return {"ok":True,"message":"robots.txt取得済み","allowed":allowed}
    except Exception as exc:
        return {"ok":False,"message":str(exc)[:220],"allowed":{}}
