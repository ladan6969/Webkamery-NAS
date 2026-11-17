"""YT_4K_V1 (16. 11. 2026) – živý přenos YouTube až ve 2160p jako fragmentované MP4 na stdout.

YouTube dává v HLS nejvýš 1080p; 2160p (VP9) jen jako DASH webovému klientovi s PO tokenem (bgutil server
na 127.0.0.1:4416). yt-dlp s live_from_start vrátí formáty s generátorem fragmentů, ale ten začíná od začátku
přenosu (až 120 h) – fragmenty proto stahujeme sami od živého konce. Obraz (VP9) + zvuk (AAC) → ffmpeg -c copy → fMP4.

Spouští ho služba (yt4k.py) z vlastního venv (yt4k/venv: yt-dlp + yt-dlp-ejs + bgutil-ytdlp-pot-provider):
    yt4k/venv/bin/python yt4k_proud.py <youtube adresa>
Proměnné: VYSKA (2160), ZPET (fragmentů před živým koncem, 3), DENO (cesta k deno), POT_URL.
Návratový kód: 0 konec / odpojení, 3 přenos 4K nemá (nebo není živě), 4 odkaz opakovaně odmítnut, 5 nic nepřichází.
"""
import os
import re
import subprocess
import sys
import threading
import time

import yt_dlp
from yt_dlp.networking import HEADRequest, Request

URL = sys.argv[1]
VYSKA = int(os.environ.get("VYSKA", "2160"))
ZPET = int(os.environ.get("ZPET", "3"))
TICHO_MAX = 60          # s bez nového fragmentu → konec
ODMITNUTI_MAX = 6       # 403 i po obnovení odkazu

O = {"live_from_start": True, "quiet": True, "no_warnings": True, "noprogress": True,
     "extractor_args": {"youtube": {"player_client": ["web"]},
                        "youtubepot-bgutilhttp": {"base_url": [os.environ.get("POT_URL", "http://127.0.0.1:4416")]}}}
if os.environ.get("DENO"):
    O["js_runtimes"] = {"deno": {"path": os.environ["DENO"]}}


def zprava(t):
    sys.stderr.write(t + "\n")
    sys.stderr.flush()


y = yt_dlp.YoutubeDL(O)
try:
    info = y.extract_info(URL, download=False)
except Exception as e:
    zprava("extract: %s" % str(e)[:200])
    sys.exit(3)
if not info.get("is_live"):
    zprava("přenos neběží živě")
    sys.exit(3)


def vyber(inf):
    fs = [f for f in inf.get("formats") or [] if callable(f.get("fragments"))]
    vid = [f for f in fs if (f.get("vcodec") or "none").startswith("vp9") and (f.get("height") or 0) <= VYSKA]
    aud = [f for f in fs if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    if not vid or not aud:
        return None, None
    return (max(vid, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0)),
            max(aud, key=lambda f: f.get("tbr") or 0))


vid, aud = vyber(info)
if not vid or (vid.get("height") or 0) <= 1080:
    zprava("4K formát není (nejvýš %sp)" % (vid or {}).get("height"))
    sys.exit(3)
zprava("obraz %s %sp %s · zvuk %s %s" % (vid["format_id"], vid.get("height"), vid.get("vcodec"), aud["format_id"], aud.get("acodec")))

rv, wv = os.pipe()
ra, wa = os.pipe()
ff = subprocess.Popen(["ffmpeg", "-nostdin", "-v", "fatal", "-i", "/dev/fd/%d" % rv, "-i", "/dev/fd/%d" % ra,
                       "-map", "0:v", "-map", "1:a", "-c", "copy", "-f", "mp4",
                       "-movflags", "frag_keyframe+empty_moov+default_base_moof", "pipe:1"], pass_fds=(rv, ra))
os.close(rv)
os.close(ra)

_zamek = threading.Lock()
_info = {"t": time.time(), "i": info}
posledni = {"t": time.time()}


def obnov(fid):
    """nový odkaz (podpis + PO token platí jen ~35 s živého přenosu) – sdílený pro obraz i zvuk, nejvýš jednou za 10 s"""
    with _zamek:
        if time.time() - _info["t"] > 10:
            _info["i"] = y.extract_info(URL, download=False)
            _info["t"] = time.time()
        return [x for x in _info["i"]["formats"] if x["format_id"] == fid and callable(x.get("fragments"))][0]


def zaklad(f):
    """základní adresa fragmentů a živý konec = první položka generátoru yt-dlp (historii neprocházet – 100 % CPU)"""
    fr = next(f["fragments"]({}))
    return re.sub(r"&sq=\d+", "", fr["url"]), fr["fragment_count"]


def hlava(f, base):
    """X-Head-Seqnum = poslední vydaný fragment (hlavičku vrací i chybová odpověď)"""
    try:
        r = y.urlopen(HEADRequest(base, headers=f.get("http_headers")))
        h = r.headers.get("X-Head-Seqnum")
        r.close()
    except Exception as e:
        resp = getattr(getattr(e, "cause", e), "response", None) or getattr(e, "response", None)
        h = resp.headers.get("X-Head-Seqnum") if resp is not None else None
    return int(h) if h else None


def konec(kod):
    try:
        ff.kill()
    except Exception:
        pass
    os._exit(kod)


def stahuj(f, fd, jm, zac):
    out = os.fdopen(fd, "wb", buffering=0)
    try:
        base, hl = zaklad(f)
    except Exception as e:
        zprava("%s: %s" % (jm, str(e)[:200]))
        konec(3)
    sq = zac[0] if zac[0] is not None else hl - ZPET
    zac[0] = sq
    odmitnuto = 0
    while True:
        try:
            b = y.urlopen(Request(base + "&sq=%d" % sq, headers=f.get("http_headers"))).read()
        except Exception as e:
            h = hlava(f, base)
            if h is not None and sq > h:          # fragment ještě nevyšel (YouTube vrací 403/404)
                time.sleep(1)
                continue
            odmitnuto += 1
            if odmitnuto > ODMITNUTI_MAX:
                zprava("%s: odkaz opakovaně odmítnut (%s)" % (jm, str(e)[:80]))
                konec(4)
            try:
                t0 = time.time()
                f = obnov(f["format_id"])
                base, _ = zaklad(f)
                if os.environ.get("LADIT"):
                    zprava("%s: nový odkaz za %.1f s" % (jm, time.time() - t0))
            except Exception as e2:
                zprava("%s: obnova odkazu: %s" % (jm, str(e2)[:120]))
                time.sleep(3)
            continue
        odmitnuto = 0
        posledni["t"] = time.time()
        try:
            out.write(b)
        except (BrokenPipeError, OSError):        # ffmpeg skončil = divák odpojen
            konec(0)
        sq += 1


zac = [None]
threading.Thread(target=stahuj, args=(vid, wv, "obraz", zac), daemon=True).start()
while zac[0] is None:
    time.sleep(0.1)
threading.Thread(target=stahuj, args=(aud, wa, "zvuk", zac), daemon=True).start()
while ff.poll() is None:
    if time.time() - posledni["t"] > TICHO_MAX:
        zprava("%d s nepřišel žádný fragment" % TICHO_MAX)
        konec(5)
    time.sleep(2)
konec(0)
