"""Převod ručního seznamu (tools/seznam_cz.py) do formátu služby → tools/import_cz.json (3. 11. 2026).
YouTube watch?v: zjistí kanál a název přenosu → ytkanal (přežije restart přenosu); nevysílající (sezónní) zůstane ytdlp.
Spuštění: cd ~/webcams && venv/bin/python tools/import_cz.py
"""
import asyncio
import json
import os
import sys

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(D))
sys.path.insert(0, D)
import resolver      # noqa: E402
from seznam_cz import S   # noqa: E402

g = {"__file__": os.path.join(D, "sber2.py")}
exec(open(os.path.join(D, "sber2.py"), encoding="utf-8").read().split("async def ytdlp_json")[0], g)
match_z = g["match_z"]


async def info(url):
    p = await asyncio.create_subprocess_exec(resolver.YTDLP, "-J", "--no-warnings", "--no-playlist", "--no-cache-dir", "--skip-download",
                                             url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), 60)
    except asyncio.TimeoutError:
        p.kill()
        return None, "timeout"
    if p.returncode != 0:
        return None, (err.decode(errors="replace").strip().splitlines() or ["?"])[-1][:120]
    try:
        return json.loads(out), None
    except ValueError:
        return None, "neplatný JSON"


async def main():
    out = []
    for cid, nazev, kraj, kat, src, ext, fer in S:
        c = {"id": cid, "name": nazev, "group": kraj, "country": "CZ", "category": kat, "source": src, "type": "auto",
             "match": "", "headers": {}, "mode": "auto", "enabled": True, "regex": "", "page": "", "note": ""}
        if ext == "feratel":
            c.update(type="feratel", match=fer)
        elif ext in ("skyline", "ipcamlive", "mjpeg"):
            c["type"] = ext
        elif ext == "sniffer":
            c.update(enabled=False, note="čeká na sniffer (fáze 2)", page=src)
        elif ext == "youtube":
            c["type"] = "ytdlp"
            if "watch?v=" in src or "Lužná" in nazev:
                d, chyba = await info(src)
                if d and d.get("live_status") == "is_live" and d.get("channel_id"):
                    c.update(type="ytkanal", source="https://www.youtube.com/channel/" + d["channel_id"], match=match_z(d.get("title") or ""),
                             page=src, note="kanál %s" % (d.get("channel") or ""))
                    print("ytkanal  %-40s ← %s" % (nazev[:40], c["match"]))
                else:
                    c["note"] = "teď nevysílá (%s) – asi sezónní, kontrola ji skryje a na jaře vrátí" % (chyba or d.get("live_status") if d else chyba)
                    print("ytdlp    %-40s   %s" % (nazev[:40], c["note"][:60]))
                await asyncio.sleep(1)
        out.append(c)
    json.dump(out, open(os.path.join(D, "import_cz.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("HOTOVO", len(out), "kamer,", sum(1 for c in out if c["enabled"]), "zapnutých")


asyncio.run(main())
