"""resolver.py – zjištění aktuální stream URL kamery.

type: direct (m3u8 bez tokenu) | ytdlp (YouTube, Twitch, SkylineWebcams… – jen HLS, ne DASH) |
      regex (m3u8 vytažené ze stránky) | auto (direct → ytdlp → regex; použitý typ se uloží do state.json)
Místo pro pozdější „sniffer“ (headless prohlížeč): další typ v RESOLVERS.
"""
import asyncio
import email.utils
import json
import logging
import os
import re
import sys
import time

import hls

log = logging.getLogger("webcams")
YTDLP = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
# YouTube vyžaduje vyřešení JS výzvy. Běh je paměťově náročný (Deno ~325 MB), proto vždy jen jeden naráz.
_YTDLP_ZAMEK = asyncio.Semaphore(1)


def _js_runtime():
    """--js-runtimes s prostředím ve venv (Deno, na armv7 QuickJS); bez něj část přenosů YouTube nejde načíst"""
    slozka = os.path.dirname(sys.executable)
    for nazev, soubor in (("deno", "deno"), ("quickjs", "qjs")):
        cesta = os.path.join(slozka, soubor)
        if os.access(cesta, os.X_OK):
            return ["--js-runtimes", "%s:%s" % (nazev, cesta)]
    return []
DEFAULT_RE = r"""https?://[^"'\s<>\\]+?\.m3u8[^"'\s<>\\]*"""


class ResolveError(Exception):
    pass


class NeniZive(ResolveError):
    """video YouTube už nevysílá; kanál a název z údajů videa → hledat nový přenos na stejném kanálu"""
    def __init__(self, msg, kanal=None, nazev=None):
        super().__init__(msg)
        self.kanal, self.nazev = kanal, nazev


# kamery, které se samy přepnuly na jiný přenos (od startu služby) – administrace je ukazuje jako varování
# cid → {"kdy", "proc", "kanal", "nazev"}
zmeny = {}
_naucene = {}     # cid → (kanál, match) pro pevné video, které skončilo – příště rovnou kanál
_SLOVA_PRYC = {"live", "stream", "livestream", "webcam", "webcams", "webkamera", "kamera", "camera", "cam", "cams", "online",
               "hd", "uhd", "fhd", "now", "24h", "the", "and", "with", "from"}


async def _direct(cfg, cam):
    src = cam["source"]
    text, final = await hls.fetch_text(cfg, src, cam.get("headers"))
    if not text.lstrip().startswith("#EXTM3U"):
        raise ResolveError("direct: odpověď není m3u8")
    return final


async def _ytdlp(cfg, cam):
    args = [YTDLP, "-J", "--no-warnings", "--no-playlist", "--no-cache-dir", "--socket-timeout", "15"] + _js_runtime()
    for k, v in (cam.get("headers") or {}).items():
        args += ["--add-header", "%s:%s" % (k, v)]
    args.append(cam["source"])
    async with _YTDLP_ZAMEK:
        p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            out, err = await asyncio.wait_for(p.communicate(), timeout=cfg["resolve_timeout"])
        except asyncio.TimeoutError:
            p.kill()
            await p.wait()
            raise ResolveError("yt-dlp: timeout %d s" % cfg["resolve_timeout"])
    if p.returncode != 0:
        msg = (err.decode(errors="replace").strip().splitlines() or ["?"])[-1]
        raise ResolveError("yt-dlp: " + msg[:300])
    d = json.loads(out)
    if d.get("_type") == "playlist" and d.get("entries"):
        d = d["entries"][0]
    if d.get("is_live") is False or d.get("live_status") in ("was_live", "not_live", "post_live"):
        raise NeniZive("yt-dlp: nevysílá živě", d.get("channel_url") or d.get("uploader_url"), d.get("title"))
    hlsf = [f for f in d.get("formats") or [] if "m3u8" in (f.get("protocol") or "")]
    if not hlsf:
        raise ResolveError("yt-dlp: žádný HLS formát")
    best = max(hlsf, key=lambda f: ((f.get("height") or 0), (f.get("tbr") or 0)))
    # master (manifest_url) – pro Kodi a Enigmu se zkrátí na nejvyšší variantu, VLC dostane celý
    return best.get("manifest_url") or best["url"]


async def _skyline(cfg, cam):
    """SkylineWebcams: stránka kamery obsahuje jednorázový token (livee.m3u8?a=…) → hd-auth.skylinewebcams.com/live.m3u8"""
    r = await hls.client(cfg).get(cam["source"], headers=hls.req_headers(cfg, cam.get("headers")))
    r.raise_for_status()
    m = re.search(r"livee?\.m3u8\?a=(\w+)", r.text)
    if m:
        return "https://hd-auth.skylinewebcams.com/live.m3u8?a=" + m.group(1)
    # část kamer Skyline nemá vlastní stream – stránka vkládá video z YouTube (aktuální ID je vždy na stránce)
    y = re.search(r"(?:youtube(?:-nocookie)?\.com/embed/|videoId[\"': ]+|ytid[\"':= ]+)([\w-]{11})", r.text)
    if y:
        return await _ytdlp(cfg, dict(cam, source="https://www.youtube.com/watch?v=" + y.group(1), headers={}))
    raise ResolveError("skyline: na stránce není stream ani video z YouTube")


FERATEL_H = {"Referer": "https://webtvfc.feratel.com/"}       # bez něj feratel přesměruje na webovou stránku


async def _feratel(cfg, cam):
    """feratel webTV: jen poslední klip (~1 min MP4) – služba z něj dělá smyčku (smycka.py). ID kamery z adresy (…-2101)
    nebo z pole match."""
    m = re.search(r"(\d{3,6})\.mp4", cam["source"]) or re.search(r"-(\d{3,6})(?:[/?#]|$)", cam["source"])
    cid = (cam.get("match") or "").strip() if (cam.get("match") or "").strip().isdigit() else (m.group(1) if m else "")
    if not cid:
        raise ResolveError("feratel: číslo kamery nenalezeno (adresa …-2101 nebo pole Název přenosu = 2101)")
    url = "https://stsfc001.feratel.com/streams/latest/1/%s.mp4?dcsdesign=WTP_feratel.com" % cid
    async with hls.client(cfg).stream("GET", url, headers=hls.req_headers(cfg, dict(FERATEL_H, **(cam.get("headers") or {})))) as r:
        if r.status_code != 200 or "video" not in r.headers.get("content-type", ""):
            raise ResolveError("feratel: klip kamery %s není (HTTP %s)" % (cid, r.status_code))
    return url


SNIMEK_MAX_STARI = 6 * 3600       # starší obrázek = kamera zamrzla (Last-Modified)


async def _snimek(cfg, cam):
    """kamera, která dává jen obrázek (JPG/PNG, obnovovaný po minutách – ČHMÚ, holidayinfo…): ověří, že je to obrázek
    a není zamrzlý; služba z něj dělá video smyčku (smycka.py)"""
    async with hls.client(cfg).stream("GET", cam["source"], headers=hls.req_headers(cfg, cam.get("headers"))) as r:
        typ = r.headers.get("content-type", "")
        if r.status_code != 200 or not typ.startswith("image/"):
            raise ResolveError("snímek: není obrázek (HTTP %s, %s)" % (r.status_code, typ or "?"))
        lm = r.headers.get("last-modified")
    if lm:
        try:
            stari = time.time() - email.utils.parsedate_to_datetime(lm).timestamp()
        except (TypeError, ValueError):
            stari = 0
        if stari > SNIMEK_MAX_STARI:
            raise ResolveError("snímek: obrázek se neobnovuje (naposledy před %d h)" % (stari // 3600))
    return cam["source"]


async def _ipcamlive(cfg, cam):
    """ipcamlive.com: alias z adresy přehrávače → API → aktuální server a ID streamu"""
    m = re.search(r"alias=([\w-]+)", cam["source"])
    if not m:
        raise ResolveError("ipcamlive: v adrese chybí alias=")
    r = await hls.client(cfg).get("https://www.ipcamlive.com/player/getcamerastreamstate.php?alias=" + m.group(1),
                                  headers=hls.req_headers(cfg, {"Referer": "https://www.ipcamlive.com/"}))
    d = (r.json() or {}).get("details") or {}
    if not d.get("streamavailable") or not d.get("streamid"):
        raise ResolveError("ipcamlive: kamera %s teď nevysílá" % m.group(1))
    return d["address"].replace("http://", "https://", 1) + "streams/" + d["streamid"] + "/stream.m3u8"


async def _mjpeg(cfg, cam):
    """MJPEG (IP kamery Axis apod.): přímá adresa – Kodi a VLC ji přehrají samy, služba jen přesměruje"""
    async with hls.client(cfg).stream("GET", cam["source"], headers=hls.req_headers(cfg, cam.get("headers"))) as r:
        ct = r.headers.get("content-type", "").lower()
        if r.status_code != 200 or not ("multipart" in ct or "jpeg" in ct or "mjpeg" in ct):
            raise ResolveError("mjpeg: odpověď HTTP %s %s" % (r.status_code, ct[:40]))
    return cam["source"]


_kanaly = {}      # url kanálu → (čas, [(video_id, název)]) – výpis živých přenosů držet 10 min (kontrola projde víc kamer kanálu)


async def zive_kanalu(cfg, kanal):
    kanal = kanal.rstrip("/")
    for k in ("/live", "/streams", "/videos", "/featured"):
        if kanal.endswith(k):
            kanal = kanal[: -len(k)]
    c = _kanaly.get(kanal)
    if c and time.time() - c[0] < 600:
        return c[1]
    async with _kanal_zamek(kanal):                    # souběžné kontroly stejného kanálu = jeden výpis
        c = _kanaly.get(kanal)
        if c and time.time() - c[0] < 600:
            return c[1]
        return await _vypis_kanalu(cfg, kanal)


_zamky_kanalu = {}


def _kanal_zamek(k):
    if k not in _zamky_kanalu:
        _zamky_kanalu[k] = asyncio.Lock()
    return _zamky_kanalu[k]


async def _vypis_kanalu(cfg, kanal):
    p = await asyncio.create_subprocess_exec(YTDLP, "--flat-playlist", "-J", "--no-warnings", "--no-cache-dir", "--playlist-end", "80",
                                             kanal + "/streams", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout=cfg["resolve_timeout"])
    except asyncio.TimeoutError:
        p.kill()
        raise ResolveError("ytkanal: timeout výpisu kanálu")
    if p.returncode != 0:
        raise ResolveError("ytkanal: " + ((err.decode(errors="replace").strip().splitlines() or ["?"])[-1])[:200])
    zive = [(e["id"], e.get("title") or "") for e in (json.loads(out).get("entries") or []) if e.get("live_status") == "is_live"]
    _kanaly[kanal] = (time.time(), zive)
    return zive


def _shoda(match, nazev):
    if not match:
        return True
    if match.startswith("re:"):
        return re.search(match[3:], nazev, re.I) is not None
    # uložený název je očištěný (bez „LIVE“, emoji, „4K“…) → stačí, když název přenosu obsahuje všechna jeho slova
    slova = re.findall(r"\w{2,}", match.lower())
    if len(match) >= 40 and len(slova) > 1:            # sběr názvy zkracoval na 40 znaků – poslední slovo bývá useknuté
        slova = slova[:-1]
    t = set(re.findall(r"\w{2,}", nazev.lower()))
    return bool(slova) and all(w in t for w in slova)


async def _ytkanal(cfg, cam):
    """kanál YouTube + název přenosu (pole match): přežije restart přenosu s novým video_id i kanály s víc kamerami"""
    zive = await zive_kanalu(cfg, cam["source"])
    vhodne = [v for v in zive if _shoda(cam.get("match") or "", v[1])]
    if not vhodne and len(zive) == 1:
        # přenos přejmenovaný: kanál vysílá jediný živý přenos → ten (u kanálu s víc kamerami by šlo o hádání, proto jen jeden)
        vhodne = zive
        zmeny[cam.get("id") or "?"] = {"kdy": time.strftime("%Y-%m-%d %H:%M"), "proc": "název přenosu se změnil",
                            "kanal": cam["source"], "nazev": zive[0][1]}
        log.warning("cam %s: přenos „%s“ na kanálu není, beru jediný živý „%s“", cam.get("id"), cam.get("match"), zive[0][1])
    elif vhodne and cam.get("type") == "ytkanal":
        zmeny.pop(cam.get("id"), None)              # původní přenos zase sedí
    if not vhodne:
        raise ResolveError("ytkanal: na kanálu teď nevysílá přenos „%s“ (živých %d)" % (cam.get("match") or "*", len(zive)))
    return await _ytdlp(cfg, dict(cam, source="https://www.youtube.com/watch?v=" + vhodne[0][0]))


async def _udaje_videa(cfg, url):
    """kanál a název ze stránky videa – YouTube je uvádí i u ukončeného přenosu, který yt-dlp už nenačte"""
    r = await hls.client(cfg).get(url, headers=hls.req_headers(cfg, {"Accept-Language": "cs"}))
    m = re.search(r'"videoDetails":\{"videoId":"[\w-]{11}".{0,4000}?"title":"((?:[^"\\]|\\.)*)".{0,4000}?"channelId":"(UC[\w-]{22})"', r.text)
    if not m:
        m2 = re.search(r'"videoDetails":\{"videoId":"[\w-]{11}".{0,4000}?"channelId":"(UC[\w-]{22})".{0,4000}?"title":"((?:[^"\\]|\\.)*)"', r.text)
        if not m2:
            return None, None
        return "https://www.youtube.com/channel/" + m2.group(1), json.loads('"%s"' % m2.group(2))
    return "https://www.youtube.com/channel/" + m.group(2), json.loads('"%s"' % m.group(1))


def _slova(t):
    return {w.lower() for w in re.findall(r"[^\W\d_]{2,}", t or "")} - _SLOVA_PRYC   # i „SV“/„JZ“ (směr kamery)


async def _novy_prenos(cfg, cam, kanal, nazev):
    """nový živý přenos na kanálu místo ukončeného videa: jediný živý, nebo jednoznačně nejpodobnější název
    (≥ 80 % slov; při shodě vyhraje název s méně slovy navíc; stále shoda = nehádat)"""
    zive = await zive_kanalu(cfg, kanal)
    if len(zive) == 1:
        vybrany = zive[0]
    else:
        hledana = _slova(nazev)
        skore = sorted(((len(hledana & _slova(t)) / max(1, len(hledana)), -len(_slova(t) - hledana), vid, t) for vid, t in zive), reverse=True)
        if not skore or skore[0][0] < 0.8 or (len(skore) > 1 and skore[1][:2] == skore[0][:2]):
            raise ResolveError("na kanálu nový přenos „%s“ není (živých %d)" % ((nazev or "?")[:60], len(zive)))
        vybrany = skore[0][2:]
    return await _ytdlp(cfg, dict(cam, source="https://www.youtube.com/watch?v=" + vybrany[0])), vybrany[1]


_YT_ID_RE = re.compile(r"(?:youtube(?:-nocookie)?\.com/(?:embed/|watch\?v=|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")
_ze_stranky = {}                 # id kamery → aktuální watch URL ze stránky provozovatele (pole page)


async def _video_ze_stranky(cfg, cam):
    """aktuální video YouTube ze stránky provozovatele (pole page, ne YouTube) – pro kanály, jejichž přenosy nejsou veřejně
    v seznamu (např. kamery.wia.cz); jiné video než to, které skončilo → watch URL, jinak None"""
    page = cam.get("page") or ""
    if not page.startswith(("http://", "https://")) or "youtube." in page or "youtu.be" in page:
        return None
    r = await hls.client(cfg).get(page, headers=hls.req_headers(cfg, cam.get("headers")))
    r.raise_for_status()
    stare = _YT_ID_RE.search(cam["source"])
    for m in _YT_ID_RE.finditer(r.text.replace("\\/", "/")):
        if not stare or m.group(1) != stare.group(1):
            return "https://www.youtube.com/watch?v=" + m.group(1)
    return None


async def _ytdlp_cam(cfg, cam):
    """yt-dlp pro kameru; pevné video (watch?v=), které skončilo → nové video ze stránky provozovatele (pole page),
    jinak nový přenos na stejném kanálu (provozovatel restartoval přenos)"""
    cid = cam.get("id") or ""
    if cid in _ze_stranky:
        try:
            return await _ytdlp(cfg, dict(cam, source=_ze_stranky[cid]))
        except ResolveError:
            _ze_stranky.pop(cid, None)             # i nové video skončilo – zkusit znovu od začátku
    if cid in _naucene:
        kanal, nazev = _naucene[cid]
        try:
            return (await _novy_prenos(cfg, cam, kanal, nazev))[0]
        except ResolveError:
            pass                                   # třeba původní video zase vysílá
    try:
        url = await _ytdlp(cfg, cam)
        _naucene.pop(cid, None)
        zmeny.pop(cid, None)
        return url
    except ResolveError as e:
        if "watch?v=" not in cam["source"]:
            raise
        try:
            nove = await _video_ze_stranky(cfg, cam)
        except Exception as e3:
            log.info("cam %s: stránka %s: %s", cid, cam.get("page"), e3)
            nove = None
        if nove:
            try:
                url = await _ytdlp(cfg, dict(cam, source=nove))
                if cid:
                    _ze_stranky[cid] = nove
                zmeny[cid or "?"] = {"kdy": time.strftime("%Y-%m-%d %H:%M"), "proc": "původní video skončilo, nové ze stránky kamery",
                                    "kanal": cam.get("page"), "nazev": nove}
                log.warning("cam %s: video skončilo, hraje nové ze stránky %s: %s", cid, cam.get("page"), nove)
                return url
            except ResolveError:
                pass
        kanal, nazev = (e.kanal, e.nazev) if isinstance(e, NeniZive) and e.kanal else (None, None)
        if not kanal:
            try:
                kanal, nazev = await _udaje_videa(cfg, cam["source"])
            except Exception:
                kanal = None
        if not kanal:
            raise
        try:
            url, novy = await _novy_prenos(cfg, cam, kanal, nazev)
        except ResolveError as e2:
            raise ResolveError("%s; %s" % (e, e2))
        if cid:
            _naucene[cid] = (kanal, nazev)
        zmeny[cid or "?"] = {"kdy": time.strftime("%Y-%m-%d %H:%M"), "proc": "původní video skončilo",
                            "kanal": kanal, "nazev": novy}
        log.warning("cam %s: video skončilo, hraje nový přenos „%s“ na kanálu %s", cid, novy, kanal)
        return url


async def _regex(cfg, cam):
    r = await hls.client(cfg).get(cam["source"], headers=hls.req_headers(cfg, cam.get("headers")))
    r.raise_for_status()
    body = r.text.replace("\\/", "/")
    m = re.search(cam.get("regex") or DEFAULT_RE, body)
    if not m:
        raise ResolveError("regex: m3u8 na stránce nenalezeno")
    return m.group(1) if m.groups() else m.group(0)


RESOLVERS = {"direct": _direct, "ytdlp": _ytdlp_cam, "ytkanal": _ytkanal, "skyline": _skyline, "feratel": _feratel,
             "ipcamlive": _ipcamlive, "mjpeg": _mjpeg, "regex": _regex, "snimek": _snimek}
NE_HLS = ("feratel", "mjpeg", "snimek")   # výsledek není HLS playlist (smyčka klipu / MJPEG / smyčka ze snímku)


def typ_odhad(cam):
    """první typ, který se zkusí (pro playlist: MJPEG bez inputstream.adaptive)"""
    return _poradi(cam)[0]


def _poradi(cam):
    t = cam.get("type") or "auto"
    if t != "auto":
        return [t]
    src = cam["source"].lower()
    if "skylinewebcams.com" in src:
        return ["skyline"]
    if "feratel.com" in src:
        return ["feratel"]
    if "ipcamlive.com" in src:
        return ["ipcamlive"]
    if "mjpg" in src or "mjpeg" in src or "video.cgi" in src:
        return ["mjpeg"]
    if cam.get("match") and "youtube.com" in src:
        return ["ytkanal"]
    if ".m3u8" in src:
        return ["direct", "ytdlp", "regex"]
    return ["ytdlp", "regex", "direct"]


async def resolve(cfg, cam):
    """→ (url, typ). Chyby všech pokusů dohromady jako ResolveError."""
    chyby = []
    for t in _poradi(cam):
        try:
            url = await RESOLVERS[t](cfg, cam)
            log.info("resolve %s: %s OK", cam["id"], t)
            return url, t
        except ResolveError as e:
            chyby.append(str(e))
        except Exception as e:   # síť, HTTP chyby…
            chyby.append("%s: %s" % (t, e.__class__.__name__ + (" " + str(e) if str(e) else "")))
    raise ResolveError(" | ".join(chyby)[:600])


async def ytdlp_version():
    try:
        p = await asyncio.create_subprocess_exec(YTDLP, "--version", stdout=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(p.communicate(), 10)
        return out.decode().strip()
    except Exception:
        return None
