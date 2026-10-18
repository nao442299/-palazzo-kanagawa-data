"""PALAZZO 神奈川5店舗 — スマホ向け公開データ集計サイト。
実サイトからのHTML取得は未検証。外部サイトへの自動アクセスは初期状態では無効。
"""
import csv
import io
import base64
import os
import re
import hmac
import asyncio
import sqlite3
import threading
import urllib.request
import urllib.robotparser
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup
import collector as site_collector
import text_import
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response

TZ = ZoneInfo("Asia/Tokyo")
HERE = Path(__file__).resolve().parent
DB = Path(os.getenv("DATABASE_PATH", str(HERE / "palazzo.sqlite3")))
TOKEN = os.getenv("ADMIN_TOKEN", "")
SITE_PASSWORD = os.getenv("SITE_PASSWORD", "")
TERMS = os.getenv("SOURCE_TERMS_ACCEPTED", "0") == "1"
AUTO = os.getenv("AUTO_COLLECT", "0") == "1"
STORES = [
    {"id":"fujisawa","name":"パラッツォ藤沢南店","source_id":"100812"},
    {"id":"tsurugamine","name":"パラッツォ鶴ヶ峰店","source_id":"100943"},
    {"id":"tsurugamine_slot","name":"パラッツォ鶴ヶ峰スロット館","source_id":"100944"},
    {"id":"shonandai","name":"パラッツォ湘南台店","source_id":"100927"},
    {"id":"hadano","name":"パラッツォ秦野渋沢店","source_id":"100956"},
]
for store in STORES:
    store["url"] = "https://daidata.goraggio.com/" + store["source_id"]
STORE_MAP = {s["id"]:s for s in STORES}
LOCK = threading.Lock()
STATE = {"running":False,"started":None,"result":None,"diagnostic":None,"alternatives":None}
HEADERS = {"User-Agent":"PalazzoKanagawaResearch/1.0 (respect robots.txt; low-rate personal research)"}

def now():
    return datetime.now(TZ).isoformat(timespec="seconds")
def today():
    return datetime.now(TZ).date().isoformat()
def conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=20)
    c.row_factory = sqlite3.Row
    return c
def init():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS units (
         store_id TEXT NOT NULL,date TEXT NOT NULL,machine TEXT NOT NULL,
         unit_no TEXT NOT NULL,games INTEGER NOT NULL DEFAULT 0,
         bb INTEGER,rb INTEGER,diff INTEGER,observed_at TEXT NOT NULL,
         source TEXT NOT NULL DEFAULT 'csv',
         PRIMARY KEY(store_id,date,machine,unit_no))""")
        c.execute("""CREATE TABLE IF NOT EXISTS logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,store_id TEXT NOT NULL,
          status TEXT NOT NULL,records INTEGER NOT NULL,message TEXT NOT NULL)""")
def rows(sql, params=()):
    with conn() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
def store_rows(data, src="csv"):
    records=[]
    for r in data:
        records.append((r["store_id"],r["date"],r["machine"],str(r["unit_no"]),r["games"],
        r.get("bb"),r.get("rb"),r.get("diff"),now(),src))
    with conn() as c:
        c.executemany("""INSERT INTO units
          (store_id,date,machine,unit_no,games,bb,rb,diff,observed_at,source)
          VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(store_id,date,machine,unit_no)
          DO UPDATE SET games=excluded.games,bb=excluded.bb,rb=excluded.rb,
          diff=excluded.diff,observed_at=excluded.observed_at,source=excluded.source""",records)
    return len(records)
def log_run(s,status,count=0,message=""):
    with conn() as c:
        c.execute("INSERT INTO logs(created_at,store_id,status,records,message) VALUES(?,?,?,?,?)",
            (now(),s,status,count,str(message)[:800]))
def basic_ok(auth):
    if not SITE_PASSWORD or not auth or not auth.startswith("Basic "):
        return False
    try:
        decoded=base64.b64decode(auth[6:],validate=True).decode("utf-8")
        username,password=decoded.split(":",1)
        return hmac.compare_digest(username,"palazzo") and hmac.compare_digest(password,SITE_PASSWORD)
    except (ValueError,UnicodeDecodeError):
        return False
def check_token(auth):
    if basic_ok(auth):
        return
    if not TOKEN:
        raise HTTPException(503,"管理認証が設定されていません")
    if not auth or not auth.startswith("Bearer ") or not hmac.compare_digest(auth[7:],TOKEN):
        raise HTTPException(403,"管理者認証が必要です")

def valid_date(value):
    try:
        return datetime.strptime(value,"%Y-%m-%d").date().isoformat()
    except Exception:
        raise HTTPException(400,"日付はYYYY-MM-DD形式で指定してください")
def cleaned_number(v):
    v = str(v or "").replace(",","").replace("－","-").strip()
    if not v or v in ("--","-"): return None
    if not re.fullmatch(r"-?\d+",v): return None
    return int(v)
def fetch_public(url):
    p = urllib.robotparser.RobotFileParser()
    p.set_url("https://daidata.goraggio.com/robots.txt")
    try:
        p.read()
    except Exception as exc:
        raise RuntimeError("robots.txtを確認できないため取得を停止: "+str(exc))
    if not p.can_fetch(HEADERS["User-Agent"],url):
        raise RuntimeError("robots.txtによりアクセス禁止")
    req = urllib.request.Request(url,headers=HEADERS)
    with urllib.request.urlopen(req,timeout=15) as res:
        if res.status != 200: raise RuntimeError("HTTP "+str(res.status))
        data = res.read(1_500_001)
        if len(data)>1_500_000: raise RuntimeError("ページサイズ超過")
        return data.decode("utf-8","replace")

def parse_tables(html, store_id):
    """ヘッダーが明確なテーブルのみ収集。不明なHTMLを推測で取り込まない。"""
    soup=BeautifulSoup(html,"html.parser")
    result=[]
    for table in soup.select("table"):
        headers=[re.sub(r"\s+","",t.get_text(" ",strip=True)) for t in table.select("tr:first-child th,tr:first-child td")]
        def position(words):
            return next((i for i,h in enumerate(headers) if any(w in h for w in words)),None)
        unit=position(["台番号","台番"]); games=position(["総回転","ゲーム数","G数","回転数"])
        bb=position(["BB","BIG"]); rb=position(["RB","REG"]); diff=position(["差枚"])
        if unit is None or games is None or (bb is None and rb is None and diff is None):
            continue
        title=table.find_previous(["h2","h3","h4"])
        machine=title.get_text(" ",strip=True) if title else ""
        if not machine:continue
        for tr in table.select("tr")[1:]:
            t=[x.get_text(" ",strip=True) for x in tr.select("td")]
            if len(t)<=max(unit,games):continue
            n=re.sub(r"[^0-9]","",t[unit])
            g=cleaned_number(t[games])
            if not n or g is None or g<0 or g>150000:continue
            result.append({"store_id":store_id,"date":today(),"machine":machine[:100],
                "unit_no":n,"games":g,
                "bb":cleaned_number(t[bb]) if bb is not None and bb<len(t) else None,
                "rb":cleaned_number(t[rb]) if rb is not None and rb<len(t) else None,
                "diff":cleaned_number(t[diff]) if diff is not None and diff<len(t) else None})
    return result

def collect_all():
    """Collect only reachable public store/model pages after checking robots.txt."""
    results=[]
    try:
        robots=site_collector.robots_policy()
    except Exception as exc:
        reason=str(exc)[:260]
        for store in STORES:
            log_run(store["id"],"blocked",0,reason)
            results.append({"store":store["name"],"status":"blocked","message":reason})
        return results
    for store in STORES:
        try:
            if not robots.can_fetch(site_collector.UA,store["url"]):
                raise site_collector.CollectionError("robots.txtでこの店舗ページへのアクセスは禁止されています")
            scan=site_collector.scan_one(store,robots,today())
            values=scan["rows"]
            if not values:
                raise site_collector.CollectionError(
                    scan["note"]+"（訪問"+str(scan["scanned"])+"ページ）")
            count=store_rows(values,"collector")
            detail=str(scan["scanned"])+"ページ解析"
            log_run(store["id"],"ok",count,detail)
            results.append({"store":store["name"],"status":"ok",
                            "records":count,"pages":scan["scanned"]})
        except Exception as exc:
            reason=str(exc)[:260]
            log_run(store["id"],"blocked" if "robots.txt" in reason or "HTTP 403" in reason
                    or "HTTP 401" in reason or "HTTP 429" in reason else "error",0,reason)
            results.append({"store":store["name"],"status":"error","message":reason})
        import time
        time.sleep(3)
    return results

def job():
    try: STATE["result"]=collect_all()
    finally:
        STATE["running"]=False
        LOCK.release()
def start_job():
    if not LOCK.acquire(False):return False
    STATE.update(running=True,started=now(),result=None)
    threading.Thread(target=job,daemon=True).start()
    return True
async def automatic():
    last=None
    while True:
        n=datetime.now(TZ)
        key=(n.date().isoformat(),n.hour)
        if n.hour in (12,23) and key != last:
            if start_job():last=key
        await asyncio.sleep(180)
@asynccontextmanager
async def lifespan(app):
    init()
    def probe():
        result=site_collector.diagnostics()
        STATE["diagnostic"]=dict(result,checked_at=now())
        print("[collector-diagnostic] "+ ("OK " if result.get("ok") else "BLOCKED ") + str(result.get("message",""))[:220],flush=True)
        log_run("system","robots_ok" if result.get("ok") else "robots_error",0,result.get("message",""))
        alt=site_collector.alternative_diagnostics()
        STATE["alternatives"]=dict(alt,checked_at=now())
        print("[alternative-diagnostic] "+str(alt)[:800],flush=True)
        log_run("system","alternative_probe",0,str(alt)[:800])
    threading.Thread(target=probe,daemon=True).start()
    task=asyncio.create_task(automatic()) if TERMS and AUTO else None
    yield
    if task:task.cancel()

app=FastAPI(title="PALAZZO DATA 神奈川5店舗",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
@app.middleware("http")
async def restrict_access(request:Request,call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if SITE_PASSWORD and not basic_ok(request.headers.get("Authorization")):
        return Response("認証が必要です",status_code=401,
                headers={"WWW-Authenticate":'Basic realm="PALAZZO DATA"'})
    return await call_next(request)

@app.get("/")
def index():
    return HTMLResponse((HERE/"index.html").read_text(encoding="utf-8"))
@app.get("/health")
def health():return {"status":"ok"}
@app.get("/api/status")
def status():
    counts=rows("SELECT store_id,COUNT(*) units,COUNT(DISTINCT date) days,MAX(date) latest FROM units GROUP BY store_id")
    latest={x["store_id"]:x for x in counts}
    return {"date":today(),"running":STATE["running"],"last_result":STATE["result"],
    "terms_confirmed":TERMS,"auto_collect":AUTO,"admin_ready":bool(TOKEN or SITE_PASSWORD),"diagnostic":STATE["diagnostic"],"alternatives":STATE["alternatives"],
    "stores":[dict(s,units=latest.get(s["id"],{}).get("units",0),
    days=latest.get(s["id"],{}).get("days",0),
    latest_date=latest.get(s["id"],{}).get("latest")) for s in STORES]}
@app.get("/api/data")
def data(day:str|None=None,store:str="all"):
    day=valid_date(day or today())
    if store != "all" and store not in STORE_MAP:raise HTTPException(400,"不明な店舗")
    condition=" WHERE date=?";args=[day]
    if store!="all":condition+=" AND store_id=?";args.append(store)
    units=rows("SELECT * FROM units"+condition+" ORDER BY store_id,machine,unit_no",args)
    groups=defaultdict(lambda:{"count":0,"games":0,"bb":0,"rb":0,"diff":0,"diff_count":0})
    ends=defaultdict(lambda:{"count":0,"games":0,"bb":0,"rb":0,"diff":0,"diff_count":0})
    for u in units:
        k=(u["store_id"],u["machine"])
        g=groups[k];g["count"]+=1;g["games"]+=u["games"];g["bb"]+=u["bb"] or 0;g["rb"]+=u["rb"] or 0
        if u["diff"] is not None:g["diff"]+=u["diff"];g["diff_count"]+=1
        if u["unit_no"].isdigit():
            e=ends[int(u["unit_no"])%10];e["count"]+=1;e["games"]+=u["games"];e["bb"]+=u["bb"] or 0;e["rb"]+=u["rb"] or 0
            if u["diff"] is not None:e["diff"]+=u["diff"];e["diff_count"]+=1
    machines=[dict(x,store_id=k[0],store_name=STORE_MAP[k[0]]["name"],machine=k[1],
              rb_rate=round(x["games"]/x["rb"],1) if x["rb"] else None,
              total_rate=round(x["games"]/(x["bb"]+x["rb"]),1) if x["bb"]+x["rb"] else None)
              for k,x in groups.items()]
    machines.sort(key=lambda x:x["games"],reverse=True)
    return {"date":day,"units":units,"machines":machines,
       "suffixes":[dict(ends[i],suffix=i) for i in range(10)],
       "total_units":len(units),"total_games":sum(r["games"] for r in units),
       "total_machines":len(machines),
       "note":"設定推定と全台系判定は未実装です。見かけの確率だけで高設定と断定しません。"}
@app.get("/api/logs")
def logs():
    return {"rows":rows("SELECT * FROM logs ORDER BY id DESC LIMIT 100")}
@app.get("/api/history")
def history(store:str="all"):
    sql="SELECT date,store_id,COUNT(*) units,SUM(games) games,SUM(diff) diff FROM units"
    args=[]
    if store != "all":
        if store not in STORE_MAP:raise HTTPException(400,"不明な店舗")
        sql+=" WHERE store_id=?";args=[store]
    return {"rows":rows(sql+" GROUP BY date,store_id ORDER BY date DESC LIMIT 200",args)}
@app.get("/api/export")
def export(day:str|None=None):
    d=valid_date(day or today())
    data=rows("SELECT store_id,date,machine,unit_no,games,bb,rb,diff FROM units WHERE date=? ORDER BY store_id,machine,unit_no",(d,))
    output=io.StringIO();fields=["store_id","date","machine","unit_no","games","bb","rb","diff"]
    w=csv.DictWriter(output,fieldnames=fields);w.writeheader();w.writerows(data)
    return StreamingResponse(iter([output.getvalue().encode("utf-8-sig")]),media_type="text/csv",
        headers={"Content-Disposition":f'attachment; filename="palazzo-{d}.csv"'})
@app.post("/api/import-csv")
async def import_csv(request:Request,authorization:str|None=Header(None)):
    check_token(authorization)
    raw=await request.body()
    if len(raw)>2_000_000:raise HTTPException(413,"CSVが大きすぎます")
    try:reader=csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    except Exception:raise HTTPException(400,"UTF-8 CSVで入力してください")
    incoming=[]
    for r in reader:
        if len(incoming)>=10000:raise HTTPException(400,"上限10,000行")
        if r.get("store_id") not in STORE_MAP:raise HTTPException(400,"店舗IDが不正")
        d=valid_date(r.get("date"))
        machine=(r.get("machine") or "").strip()[:100]
        no=(r.get("unit_no") or "").strip()
        if not machine or not no.isdigit():raise HTTPException(400,"機種か台番が不正")
        games=cleaned_number(r.get("games"))
        if games is None or games<0 or games>150000:raise HTTPException(400,"G数が不正")
        incoming.append({"store_id":r["store_id"],"date":d,"machine":machine,"unit_no":no,
            "games":games,"bb":cleaned_number(r.get("bb")),"rb":cleaned_number(r.get("rb")),
            "diff":cleaned_number(r.get("diff"))})
    return {"saved":store_rows(incoming)}
@app.get("/api/diagnose")
def diagnose(authorization:str|None=Header(None)):
    check_token(authorization)
    result=site_collector.diagnostics()
    return {"checked_at":now(),**result}

@app.post("/api/collect")
def collect(authorization:str|None=Header(None)):
    check_token(authorization)
    if not TERMS:
        raise HTTPException(409,"私的利用およびデータ取得元の利用条件を確認するまで収集を開始できません")
    if not start_job():raise HTTPException(409,"すでに収集中です")
    return {"message":"利用制限を尊重し、公開ページのみの取得を開始しました","started_at":STATE["started"]}

@app.post("/api/manual")
async def manual(request:Request,authorization:str|None=Header(None)):
    check_token(authorization)
    try:
        obj=await request.json()
    except Exception:
        raise HTTPException(400,"JSON形式で入力してください")
    if not isinstance(obj,dict):
        raise HTTPException(400,"登録内容が不正です")
    store=obj.get("store_id")
    if store not in STORE_MAP:raise HTTPException(400,"店舗を選択してください")
    day=valid_date(obj.get("date"))
    machine=str(obj.get("machine") or "").strip()[:100]
    unit=str(obj.get("unit_no") or "").strip()
    games=cleaned_number(obj.get("games"))
    if not machine or len(machine)<2:raise HTTPException(400,"機種名を入力してください")
    if not unit.isdigit() or len(unit)>8:raise HTTPException(400,"台番号が不正です")
    if games is None or games<0 or games>150000:raise HTTPException(400,"ゲーム数が不正です")
    values={"store_id":store,"date":day,"machine":machine,"unit_no":unit,"games":games}
    for k in ("bb","rb","diff"):
        value=cleaned_number(obj.get(k))
        if value is not None and (value<-150000 or value>150000 or (k in ("bb","rb") and value<0)):
            raise HTTPException(400,k+"の数字が不正です")
        values[k]=value
    return {"saved":store_rows([values],"manual")}

@app.post("/api/import-text")
async def import_text(request:Request,authorization:str|None=Header(None)):
    check_token(authorization)
    if int(request.headers.get("content-length","0") or 0)>700000:
        raise HTTPException(413,"テキストが大きすぎます")
    try:
        data=await request.json()
    except Exception:
        raise HTTPException(400,"入力内容を確認してください")
    if not isinstance(data,dict):
        raise HTTPException(400,"入力内容が正しくありません")
    store_id=data.get("store_id")
    if store_id not in STORE_MAP:
        raise HTTPException(400,"店舗を指定してください")
    day=valid_date(data.get("date"))
    try:
        records,notes=text_import.parse_text(data.get("text",""),store_id,day)
    except ValueError as exc:
        raise HTTPException(400,str(exc))
    saved=store_rows(records,"manual-paste")
    log_run(store_id,"manual_paste",saved,"台番別の表を手動登録")
    return {"saved":saved,"notes":notes,"date":day,"store_id":store_id}
