"""Parse pasted slot-report tables for a single chosen store and date.

The user copies rows manually. This module performs no network requests.
Expected: rank, unit number, machine name, diff, games, optional BB/RB.
"""
import re

def number(text):
    s=str(text or "").strip().replace(",","").replace("＋","+").replace("−","-").replace("－","-")
    if not s or s in {"-","--","—","N/A"}:
        return None
    if not re.fullmatch(r"[+-]?\d+",s):
        return None
    return int(s)

def fields(line):
    if "\t" in line:
        return [c.strip() for c in line.split("\t")]
    if "|" in line:
        return [c.strip() for c in line.strip().strip("|").split("|")]
    return [c.strip() for c in re.split(r"\s{2,}",line.strip())]

def parse_row(line, store_id, day):
    chunks=fields(line)
    if len(chunks)>=5:
        rank,unit,machine,diff,games=chunks[:5]
        if not re.fullmatch(r"\d{1,4}",rank) or not re.fullmatch(r"\d{2,6}",unit):
            return None
        g=number(games)
        if g is None or not 0<=g<=150000 or not 1<len(machine)<=120:
            return None
        d=number(diff)
        bb=number(chunks[6]) if len(chunks)>6 else None
        rb=number(chunks[7]) if len(chunks)>7 else None
        if bb is not None and not 0<=bb<=500:
            bb=None
        if rb is not None and not 0<=rb<=500:
            rb=None
        return {"store_id":store_id,"date":day,"unit_no":unit,
                "machine":machine[:100],"games":g,"bb":bb,"rb":rb,"diff":d}
    # A whitespace-only pasted line can have single spacing between columns.
    m=re.match(r"^\s*(\d{1,4})\s+(\d{2,6})\s+(.{2,120}?)\s+([+－−-]?\d[\d,]*)\s+(\d[\d,]*)\s+\d+(?:\.\d+)?%",line)
    if m:
        rank,unit,machine,diff,games=m.groups()
        g=number(games)
        if g is not None and 0<=g<=150000:
            return {"store_id":store_id,"date":day,"unit_no":unit,"machine":machine[:100],
                    "games":g,"diff":number(diff),"bb":None,"rb":None}
    return None

def parse_text(text, store_id, day):
    if not isinstance(text,str) or not text.strip():
        raise ValueError("貼り付けたデータが空です")
    if len(text)>650000:
        raise ValueError("テキストが大きすぎます")
    marker=re.search(r"全台データ一覧(?:・差枚ランキング)?",text)
    if marker:
        text=text[marker.end():]
    data={}
    for line in text.splitlines():
        if line.strip().startswith(("平均","合計","順位","評価","---")):
            continue
        rec=parse_row(line,store_id,day)
        if not rec:
            continue
        data[(rec["machine"],rec["unit_no"])]=rec
        if len(data)>2000:
            raise ValueError("2,000台以上のデータは登録できません")
    if not data:
        raise ValueError("台番号・機種・差枚・G数の一覧表を読み取れませんでした。見出しと台別の表を一緒にコピーしてください")
    values=list(data.values())
    notes=[]
    available=[v["diff"] for v in values if v["diff"] is not None]
    # Some report snapshots use +1 as a dummy value for most machines.
    if len(available)>=8:
        fake=sum(1 for d in available if abs(d)<=1)
        if fake/len(available)>=.7:
            for row in values:
                row["diff"]=None
            notes.append("差枚が±1に偏っているため、元サイトの仮値の可能性があります。差枚は欠損値として扱いました")
    return values,notes
