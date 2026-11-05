"""Sloučení seznamů kamer → tools/finalni.json (3. 11. 2026).
Přednost: ruční český seznam (import_cz.json) > kamery už ve službě (české názvy) > velký sběr (sber2.json).
Duplicity podle klíče zdroje; nahrané odkazy watch?v se převedou na ytkanal podle kandidati.json.
Spuštění: venv/bin/python tools/sloucit.py <cesta k aktuálnímu cams.json ze služby>
"""
import json
import os
import re
import sys

D = os.path.dirname(os.path.abspath(__file__))
g = {"__file__": os.path.join(D, "sber2.py")}
exec(open(os.path.join(D, "sber2.py"), encoding="utf-8").read().split("async def ytdlp_json")[0], g)
match_z = g["match_z"]


def kanal_id(u):
    m = re.search(r"/channel/(UC[\w-]+)", u)
    return m.group(1) if m else u.rstrip("/").lower()


def klic(c):
    s, t = c["source"], c.get("type")
    if t == "ytkanal":
        return "ytk|%s|%s" % (kanal_id(s), (c.get("match") or "").lower())
    m = re.search(r"[?&]v=([\w-]{11})", s)
    if m:
        return "ytv|" + m.group(1)
    if "skylinewebcams.com" in s:
        return "sky|" + re.sub(r"^.*?/webcam/", "", s).lower()
    if t == "feratel" or "feratel.com" in s:
        m = re.search(r"(\d{3,6})(?:\.mp4|[/?#]|$)", s)
        return "fer|" + ((c.get("match") or "") if (c.get("match") or "").isdigit() else (m.group(1) if m else s))
    return "src|" + s.rstrip("/").lower()


def main():
    ve_sluzbe = json.load(open(sys.argv[1], encoding="utf-8"))
    cz = json.load(open(os.path.join(D, "import_cz.json"), encoding="utf-8"))
    sber = []
    for f in ("sber2.json", "sber2_skyline.json"):
        if os.path.exists(os.path.join(D, f)):
            sber += json.load(open(os.path.join(D, f), encoding="utf-8"))
    kand = {x["video"]: x for x in json.load(open(os.path.join(D, "kandidati.json"), encoding="utf-8"))}
    # sběr z YouTube: země podle kanálu (většina jeho přenosů), pryč nekamery a přenosy bez země i zaměření
    from collections import Counter, defaultdict
    zeme_kanalu = defaultdict(Counter)
    for c in sber:
        if c.get("type") == "ytkanal" and c.get("country"):
            zeme_kanalu[c["source"]][c["country"]] += 1
    smeti = re.compile(r"地震|速報|earthquake|parade|march\b|masquerade|vr180|tiempo|weather radar|radar|noticias|nachrichten|"
                       r"\bnews\b|\btv\b|church|worship|mass\b|sermon|concert|festival|race|racing|nascar|f1\b|election|"
                       r"cámaras|cameras? de |multi ?cam|compilation|best of|stream highlights|systems$", re.I)
    vycisteno, pryc = [], Counter()
    for c in sber:
        if c.get("type") == "ytkanal":
            if smeti.search(c["name"] + " " + (c.get("match") or "")):
                pryc["nekamera"] += 1
                continue
            if not c.get("country") and zeme_kanalu.get(c["source"]):
                kod, n = zeme_kanalu[c["source"]].most_common(1)[0]
                if n >= 1:
                    c["country"] = kod
            if not c.get("country") and not c.get("category"):
                pryc["bez země i zaměření"] += 1
                continue
        vycisteno.append(c)
    for c in vycisteno:                                   # Skyline bez zaměření = pohled na město / náměstí
        if c.get("type") == "skyline" and not c.get("category"):
            c["category"] = "město"
    print("sběr: vyřazeno", dict(pryc))
    sber = vycisteno
    # kamery ve službě: odkaz na video → kanál + název přenosu (stálé), skupina „Evropa/Amerika“ pryč (kategorie = země)
    for c in ve_sluzbe:
        m = re.search(r"[?&]v=([\w-]{11})", c["source"])
        k = kand.get(m.group(1)) if m else None
        if k and k.get("channel_id"):
            c.update(type="ytkanal", source="https://www.youtube.com/channel/" + k["channel_id"], match=match_z(k["titul"]))
        if c.get("group") in ("Evropa", "Amerika", "Asie", "Afrika"):
            c["group"] = "Ostatní"
    out, kl, ids = [], {}, set()
    for zdroj, seznam in (("cz", cz), ("sluzba", ve_sluzbe), ("sber", sber)):
        n = 0
        for c in seznam:
            k = klic(c)
            if k in kl:
                continue
            cid = c["id"]
            i = 2
            while cid in ids:
                cid, i = "%s-%d" % (c["id"][:36], i), i + 1
            c = dict(c, id=cid)
            kl[k] = cid
            ids.add(cid)
            out.append(c)
            n += 1
        print("%-7s %4d přidáno z %d" % (zdroj, n, len(seznam)))
    json.dump(out, open(os.path.join(D, "finalni.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("CELKEM", len(out), "| zapnutých", sum(c.get("enabled", True) for c in out))
    print("typy:", Counter(c.get("type") for c in out).most_common())
    print("země:", Counter(c.get("country") or "—" for c in out).most_common(25))


main()
