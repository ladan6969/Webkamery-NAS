"""smycka.py – kamery, které nevysílají živě, ale dávají jen poslední klip (feratel webTV: ~1 min MP4, obnovuje se průběžně).

Při sledování server klip stáhne, bez překódování přebalí na HLS segmenty (ffmpeg -c copy) a podává ho jako nekonečný
„živý“ playlist (posuvné okno podle času, #EXT-X-DISCONTINUITY na konci smyčky). Klip se obnoví po 10 min.
Data jen v /tmp (PrivateTmp služby), drží se poslední 2 verze na kameru. Bez diváků se nic nestahuje.
Typ „snimek“ (7. 11. 2026): z obrázku (JPG/PNG) udělá ffmpeg 20s video (H.264, 2 snímky/s), obnova po 2 min.
"""
import asyncio
import math
import os
import re
import shutil
import time

import hls

KOREN = "/tmp/webcams-smycka"
OBNOVA = 600
OBNOVA_SNIMEK = 120
_stav, _zamky = {}, {}


def _zamek(cid):
    if cid not in _zamky:
        _zamky[cid] = asyncio.Lock()
    return _zamky[cid]


async def priprav(cfg, cid, url, headers=None, snimek=False):
    obnova = OBNOVA_SNIMEK if snimek else OBNOVA
    st = _stav.get(cid)
    if st and time.time() - st["t"] < obnova:
        return st
    async with _zamek(cid):
        st = _stav.get(cid)
        if st and time.time() - st["t"] < obnova:
            return st
        ver = str(int(time.time()))
        d = os.path.join(KOREN, cid, ver)
        os.makedirs(d, exist_ok=True)
        klip = os.path.join(d, "klip.mp4")
        zakl = {} if snimek else {"Referer": "https://webtvfc.feratel.com/"}
        async with hls.client(cfg).stream("GET", url, headers=hls.req_headers(cfg, dict(zakl, **(headers or {})))) as r:
            r.raise_for_status()
            with open(klip, "wb") as f:
                async for ch in r.aiter_bytes(262144):
                    f.write(ch)
        if snimek:     # stojící obraz: 20 s, 2 snímky/s, klíčový snímek každé 2 s; rozměry sudé, nejvýš 1920 px na šířku
            vstup = ["-loop", "1", "-framerate", "2", "-i", klip, "-t", "20",
                     "-vf", "scale='min(1920,iw)':-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                     "-tune", "stillimage", "-r", "2", "-g", "4", "-an"]
        else:
            vstup = ["-i", klip, "-c", "copy"]
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", *vstup, "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
            "-hls_segment_filename", os.path.join(d, "s%03d.ts"), os.path.join(d, "index.m3u8"),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(p.communicate(), 90)
        except asyncio.TimeoutError:
            p.kill()
            raise RuntimeError("smyčka: ffmpeg timeout")
        os.unlink(klip)
        if not os.path.exists(os.path.join(d, "index.m3u8")):
            raise RuntimeError("smyčka: ffmpeg selhal " + err.decode(errors="replace")[-200:])
        segs, dur = [], None
        for l in open(os.path.join(d, "index.m3u8")).read().splitlines():
            m = re.match(r"#EXTINF:([\d.]+)", l)
            if m:
                dur = float(m.group(1))
            elif l.endswith(".ts") and dur:
                segs.append((l, dur))
                dur = None
        if not segs:
            raise RuntimeError("smyčka: z klipu nevznikly segmenty " + err.decode(errors="replace")[-200:])
        st = {"ver": ver, "segs": segs, "t": time.time(), "avg": sum(s[1] for s in segs) / len(segs),
              "max": max(s[1] for s in segs)}
        _stav[cid] = st
        # starší verze pryč (poslední 2 zůstávají – přehrávač může dohrávat předchozí)
        verze = sorted(os.listdir(os.path.join(KOREN, cid)))
        for v in verze[:-2]:
            shutil.rmtree(os.path.join(KOREN, cid, v), ignore_errors=True)
        return st


def playlist(cfg, cid, st, key, okno=5):
    segs, n = st["segs"], len(st["segs"])
    k = int(time.time() // st["avg"])
    od = k - okno + 1
    out = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:%d" % math.ceil(st["max"]),
           "#EXT-X-MEDIA-SEQUENCE:%d" % od, "#EXT-X-DISCONTINUITY-SEQUENCE:%d" % (od // n)]
    for j in range(od, k + 1):
        i = j % n
        if i == 0 and j != od:
            out.append("#EXT-X-DISCONTINUITY")
        out.append("#EXTINF:%.3f," % segs[i][1])
        out.append("%s/smycka/%s/%s/%s?k=%s" % (cfg["public_base"], cid, st["ver"], segs[i][0], key))
    return "\n".join(out) + "\n"


def soubor(cid, ver, nazev):
    if not (re.fullmatch(r"[a-z0-9_-]+", cid) and ver.isdigit() and re.fullmatch(r"s\d{3}\.ts", nazev)):
        return None
    p = os.path.join(KOREN, cid, ver, nazev)
    return p if os.path.exists(p) else None
