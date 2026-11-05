"""Jednorázový sběr kandidátů (3. 11. 2026): YouTube hledání jen živých přenosů po zemích → ověření resolverem služby.
Šetrně: dotazy postupně s pauzou. Výstup: tools/kandidati.json (ruční výběr, pak do služby přes /admin/api/cams).
Spuštění: cd ~/webcams && venv/bin/python tools/najdi_kamery.py
"""
import asyncio
import json
import os
import re
import sys
import time
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolver  # noqa: E402
import store     # noqa: E402

YTDLP = resolver.YTDLP
# (dotaz, země, kategorie, kolik nejvýš)
DOTAZY = [
    ("praha live webcam", "CZ", "město", 6), ("česko webkamera živě", "CZ", "", 6), ("krkonoše sněžka live", "CZ", "hory", 3),
    ("šumava lipno live kamera", "CZ", "hory", 3), ("brno live webcam", "CZ", "město", 3), ("czech railway live", "CZ", "železnice", 3),
    ("bratislava live webcam", "SK", "město", 3), ("tatry live kamera", "SK", "hory", 3), ("slovensko live kamera", "SK", "", 3),
    ("austria alps live webcam", "AT", "hory", 4), ("zermatt matterhorn live", "CH", "hory", 2), ("dolomites live webcam", "IT", "hory", 2),
    ("croatia live cam", "HR", "moře", 4), ("venice live cam", "IT", "město", 3), ("rome live cam", "IT", "město", 2),
    ("paris eiffel tower live", "FR", "město", 2), ("amsterdam live cam", "NL", "město", 2), ("london live cam", "GB", "město", 2),
    ("hamburg port live", "DE", "přístav", 2), ("berlin live cam", "DE", "město", 2), ("tenerife live cam", "ES", "moře", 2),
    ("santorini live cam", "GR", "moře", 2), ("iceland volcano live", "IS", "příroda", 2), ("norway northern lights live", "NO", "příroda", 2),
    ("times square live", "US", "město", 2), ("las vegas live cam", "US", "město", 2), ("florida beach live cam", "US", "moře", 2),
    ("yellowstone live cam", "US", "příroda", 2), ("alaska bear cam live", "US", "zvířata", 2), ("tokyo shibuya live", "JP", "město", 3),
    ("mount fuji live", "JP", "hory", 2), ("namibia waterhole live", "NA", "zvířata", 2), ("kenya wildlife live cam", "KE", "zvířata", 2),
    ("airport live planespotting", "", "letiště", 3), ("sydney harbour live", "AU", "přístav", 2),
]
VYNECHAT = re.compile(r"(\d{3,}\s*(top|best)|around the world|music|radio|lofi|relax(ing)? music|compilation|replay|highlights|news|"
                      r"stream highlights|best of|\bvs\b|football|game|gaming|minecraft|fortnite|pray|church service|mass\b)", re.I)


async def hledej(dotaz):
    url = "https://www.youtube.com/results?search_query=%s&sp=EgJAAQ%%253D%%253D" % quote(dotaz)
    p = await asyncio.create_subprocess_exec(YTDLP, "--flat-playlist", "-J", "--no-warnings", "--playlist-end", "25", url,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    out, _ = await asyncio.wait_for(p.communicate(), 60)
    return (json.loads(out or b"{}").get("entries") or [])


def cisty_nazev(t):
    t = re.sub(r"[^\w\s\-–,.:'()/&!+]", " ", t, flags=re.U)            # emoji pryč
    t = re.sub(r"\b(4K|8K|HD|UHD|LIVE|Live|live|24/7|24 7|LIVESTREAM|Livestream|webcam|Webcam|WEBCAM|cam|Cam|stream|Stream)\b", " ", t)
    t = re.sub(r"\s*[|•]\s*.*$", "", t)                                # za svislítkem obvykle reklama
    t = re.sub(r"\s{2,}", " ", t).strip(" -–:,.")
    return t[:70]


async def main():
    cfg = store.config()
    videa, kanaly = {}, {}
    for dotaz, zeme, kat, kolik in DOTAZY:
        try:
            e = await hledej(dotaz)
        except Exception as ex:
            print("!!", dotaz, ex); continue
        n = 0
        for x in e:
            if x.get("live_status") != "is_live" or not x.get("id") or VYNECHAT.search(x.get("title") or ""):
                continue
            kanaly.setdefault(x.get("channel_id"), set()).add(x["id"])
            if x["id"] in videa or n >= kolik:
                continue
            videa[x["id"]] = {"video": x["id"], "channel_id": x.get("channel_id"), "kanal": x.get("channel"),
                              "titul": x.get("title"), "country": zeme, "category": kat, "dotaz": dotaz}
            n += 1
        print("%-32s %d" % (dotaz, n), flush=True)
        await asyncio.sleep(3)
    print("kandidátů:", len(videa), flush=True)
    out = []
    for v in videa.values():
        jediny = len(kanaly.get(v["channel_id"], ())) == 1
        src = ("https://www.youtube.com/channel/%s/live" % v["channel_id"]) if jediny and v["channel_id"] else \
              ("https://www.youtube.com/watch?v=%s" % v["video"])
        cam = {"id": "x", "source": src, "type": "ytdlp", "headers": {}}
        try:
            url, _ = await resolver.resolve(cfg, cam)
            ok, err = True, None
        except Exception as ex:
            url, ok, err = None, False, str(ex)[:120]
        v.update(source=src, kanal_live=jediny, ok=ok, err=err, nazev=cisty_nazev(v["titul"] or ""))
        out.append(v)
        print(("OK " if ok else "-- ") + v["country"].ljust(3) + v["nazev"][:60], flush=True)
        await asyncio.sleep(2)
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "kandidati.json"), "w"), ensure_ascii=False, indent=1)
    print("hotovo", sum(1 for x in out if x["ok"]), "/", len(out))


asyncio.run(main())
