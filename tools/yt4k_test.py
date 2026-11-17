"""YT_4K_V1 test: živý přenos YouTube v 2160p. yt-dlp (web klient + PO token bgutil, live_from_start) dá DASH formáty;
fragmenty stahujeme sami od živého konce (ZPET fragmentů), obraz + zvuk → ffmpeg -c copy → MKV na stdout."""
import os, re, sys, threading, subprocess, time
import yt_dlp
from yt_dlp.networking import Request
URL = sys.argv[1]; VYSKA = int(os.environ.get("VYSKA", "2160")); ZPET = int(os.environ.get("ZPET", "3"))
o = {"live_from_start": True, "quiet": True, "no_warnings": True,
     "js_runtimes": {"deno": {"path": "/opt/webcams/venv/bin/deno"}},
     "extractor_args": {"youtube": {"player_client": ["web"]}}}
y = yt_dlp.YoutubeDL(o)
info = y.extract_info(URL, download=False)
fs = [f for f in info["formats"] if callable(f.get("fragments"))]
vid = [f for f in fs if (f.get("vcodec") or "none").startswith("vp9") and (f.get("height") or 0) <= VYSKA]
vid = max(vid, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
aud = [f for f in fs if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
aud = max(aud, key=lambda f: f.get("tbr") or 0)
sys.stderr.write("obraz %s %sp %s, zvuk %s %s\n" % (vid["format_id"], vid.get("height"), vid.get("vcodec"), aud["format_id"], aud.get("acodec")))
rv, wv = os.pipe(); ra, wa = os.pipe()
ff = subprocess.Popen(["ffmpeg", "-v", "error", "-i", "/dev/fd/%d" % rv, "-i", "/dev/fd/%d" % ra, "-map", "0:v", "-map", "1:a",
                       "-c", "copy", "-f", "matroska", "pipe:1"], pass_fds=(rv, ra))
os.close(rv); os.close(ra)

def zaklad(f):
    """základní adresa fragmentů + živý konec = první položka generátoru yt-dlp (historii neprocházíme)"""
    fr = next(f["fragments"]({}))
    return re.sub(r"&sq=\d+", "", fr["url"]), fr["fragment_count"]

def hlava(f, base):
    """X-Head-Seqnum = poslední vydaný fragment (holá adresa bez sq vrací hlavičku i při chybě)"""
    from yt_dlp.networking import HEADRequest
    try:
        r = y.urlopen(HEADRequest(base, headers=f.get("http_headers"))); h = r.headers.get("X-Head-Seqnum"); r.close()
    except Exception as e:
        resp = getattr(getattr(e, "cause", e), "response", None) or getattr(e, "response", None)
        h = resp.headers.get("X-Head-Seqnum") if resp is not None else None
    if os.environ.get("LADIT"): sys.stderr.write("hlava %s\n" % h)
    return int(h) if h else None

_zamek = threading.Lock(); _info = {"t": time.time(), "i": info}
def obnov(fid):
    """nový odkaz (PO token, podpis) – sdílený pro obraz i zvuk, nejvýš jednou za 10 s"""
    with _zamek:
        if time.time() - _info["t"] > 10:
            _info["i"] = y.extract_info(URL, download=False); _info["t"] = time.time()
        return [x for x in _info["i"]["formats"] if x["format_id"] == fid and callable(x.get("fragments"))][0]

def stahuj(f, fd, jm, zac):
    out = os.fdopen(fd, "wb", buffering=0)
    base, konec = zaklad(f)
    sq = (zac[0] if zac[0] is not None else konec - ZPET)
    zac[0] = sq
    while True:
        t = time.time()
        try:
            b = y.urlopen(Request(base + "&sq=%d" % sq, headers=f.get("http_headers"))).read()
        except Exception as e:
            h = hlava(f, base)
            if h is not None and sq > h:          # ještě nevyšel (YouTube vrací 403/404) → počkat
                time.sleep(1); continue
            sys.stderr.write("%s sq %d (hlava %s): %s → nový odkaz\n" % (jm, sq, h, str(e)[:80]))
            t0 = time.time(); f = obnov(f["format_id"]); base, _ = zaklad(f)
            sys.stderr.write("%s obnoveno za %.1f s\n" % (jm, time.time() - t0)); continue
        if os.environ.get("LADIT"): sys.stderr.write("%s sq %d %d kB %.1f s\n" % (jm, sq, len(b) // 1024, time.time() - t))
        try: out.write(b)
        except BrokenPipeError: return
        sq += 1

zac = [None]
tv = threading.Thread(target=stahuj, args=(vid, wv, "obraz", zac), daemon=True); tv.start()
while zac[0] is None: time.sleep(0.1)
threading.Thread(target=stahuj, args=(aud, wa, "zvuk", zac), daemon=True).start()
ff.wait()
