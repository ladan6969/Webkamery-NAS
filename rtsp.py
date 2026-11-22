"""RTSP_V1 (21. 11. 2026) – veřejné kamery rtsp:// pro přehrávač ve správě (prohlížeč RTSP neumí).

ffmpeg si RTSP stáhne přes TCP a bez překódování obrazu z něj dělá krátké živé HLS (2 s segmenty, 6 v seznamu)
do dočasné složky. Proces běží, jen dokud se někdo dívá: 60 s bez požadavku → ukončí se a složka se smaže.
Kodi / Enigma2 / VLC dostanou místo toho rovnou MPEG-TS (main.py _ts_pres_ffmpeg).
Zvuk IP kamer (G.711, PCM…) HLS/TS neunese → vždy AAC. Obraz H.265 prohlížeč většinou nepřehraje (Kodi ano).
"""
import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
from urllib.parse import quote

log = logging.getLogger("webcams")

KOREN = os.path.join(tempfile.gettempdir(), "webcams-rtsp")
NECINNOST = 60
BEZI = {}            # cid → {"p": proces, "dir": složka, "posledni": čas, "url": zdroj}
_uklid = {"task": None}


def ffmpeg_vstup(url):
    return ["-rtsp_transport", "tcp", "-timeout", "15000000", "-fflags", "+genpts+discardcorrupt", "-i", url]


ZVUK = ["-c:a", "aac", "-b:a", "96k", "-ac", "2"]


async def _ukoncit(cid):
    z = BEZI.pop(cid, None)
    if not z:
        return
    if z["p"].returncode is None:
        try:
            z["p"].kill()
        except ProcessLookupError:
            pass
    try:
        await asyncio.wait_for(z["p"].wait(), 5)
    except asyncio.TimeoutError:
        pass
    shutil.rmtree(z["dir"], ignore_errors=True)
    log.info("rtsp %s: HLS ukončeno", cid)


async def _uklizec():
    while BEZI:
        await asyncio.sleep(15)
        for cid, z in list(BEZI.items()):
            if time.time() - z["posledni"] > NECINNOST or z["p"].returncode is not None:
                await _ukoncit(cid)
    _uklid["task"] = None


async def spustit(cfg, cid, url):
    """→ složka s i.m3u8, nebo výjimka (RuntimeError s textem pro uživatele)"""
    z = BEZI.get(cid)
    if z and (z["p"].returncode is not None or z["url"] != url):
        await _ukoncit(cid)
        z = None
    if not z:
        if len(BEZI) >= int(cfg.get("rtsp_web_max", 3)):
            raise RuntimeError("moc souběžných RTSP náhledů")
        os.makedirs(KOREN, exist_ok=True)
        d = tempfile.mkdtemp(prefix=re.sub(r"[^a-z0-9_-]", "", cid) + "-", dir=KOREN)
        args = (["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"] + ffmpeg_vstup(url) +
                ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"] + ZVUK +
                ["-f", "hls", "-hls_time", "2", "-hls_list_size", "6", "-hls_flags", "delete_segments+omit_endlist+independent_segments",
                 "-hls_segment_filename", os.path.join(d, "s%06d.ts"), os.path.join(d, "i.m3u8")])
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        z = BEZI[cid] = {"p": p, "dir": d, "posledni": time.time(), "url": url}
        log.info("rtsp %s: HLS spuštěno", cid)
        if not _uklid["task"]:
            _uklid["task"] = asyncio.create_task(_uklizec())
    z["posledni"] = time.time()
    m3u = os.path.join(z["dir"], "i.m3u8")
    konec = time.time() + 30
    while not os.path.exists(m3u):
        if z["p"].returncode is not None:
            err = (await z["p"].stderr.read()).decode(errors="replace").strip().splitlines()
            await _ukoncit(cid)
            raise RuntimeError((err[-1] if err else "ffmpeg skončil")[:200])
        if time.time() > konec:
            await _ukoncit(cid)
            raise RuntimeError("kamera do 30 s neposlala obraz")
        await asyncio.sleep(0.5)
    return z["dir"]


def playlist(cid, d, key):
    """i.m3u8 s odkazy na segmenty přes službu (s klíčem)"""
    with open(os.path.join(d, "i.m3u8"), encoding="utf-8") as f:
        text = f.read()
    return re.sub(r"^(s\d+\.ts)$", lambda m: "%s?k=%s" % (m.group(1), quote(key)), text, flags=re.M)


def segment(cid, name):
    """cesta k segmentu běžícího náhledu nebo None"""
    z = BEZI.get(cid)
    if not z or not re.fullmatch(r"s\d{6}\.ts", name):
        return None
    z["posledni"] = time.time()
    p = os.path.join(z["dir"], name)
    return p if os.path.exists(p) else None
