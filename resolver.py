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


class YtBlok(ResolveError):
    """YT_SETRNE_V1: YouTube odmítá tuto IP (429 / „Sign in to confirm you're not a bot“) – nejde o mrtvou kameru"""


# YT_SETRNE_V1 (26. 11. 2026): po noční kontrole 3 300 kamer YouTube zablokoval Odroid i card1 → při blokaci YouTube
# chvíli vůbec nevolat (15 min, pak 30, 60, 120, nejvýš 180), kamery tím neoznačovat jako mrtvé; s cookies (vedlejší
# účet Google, soubor yt-cookies.txt) se během blokace zkouší s přihlášením.
YT_BLOK = {"do": 0.0, "od": None, "pocet": 0, "posledni": "", "kdy": 0.0, "s_cookies": None}
_BLOK_ZNAKY = ("not a bot", "HTTP Error 429", "Too Many Requests")


def yt_blokovano():
    return time.time() < YT_BLOK["do"]


def _je_blok(text):
    return any(z in text for z in _BLOK_ZNAKY)


def _blok_zjisten(text):
    YT_BLOK["pocet"] += 1
    pauza = min(10800, 900 * 2 ** min(YT_BLOK["pocet"] - 1, 4))
    YT_BLOK.update(do=time.time() + pauza, od=YT_BLOK["od"] or time.strftime("%Y-%m-%d %H:%M:%S"), posledni=text[-200:], kdy=time.time())
    log.warning("YouTube blokuje (%d×), další pokus za %d min: %s", YT_BLOK["pocet"], pauza // 60, text[-160:].replace("\n", " "))


def _blok_konec():
    if YT_BLOK["pocet"]:
        log.info("YouTube zase odpovídá (blokace od %s)", YT_BLOK["od"])
    YT_BLOK.update(do=0.0, od=None, pocet=0)


def _cookies(cfg):
    """soubor cookies (Netscape) z vedlejšího účtu Google; None = není"""
    cesta = cfg.get("yt_cookies") or "yt-cookies.txt"
    if not os.path.isabs(cesta):
        cesta = os.path.join(os.path.dirname(os.path.abspath(__file__)), cesta)
    return cesta if os.path.isfile(cesta) and os.path.getsize(cesta) > 100 else None


def _blok_zprava():
    return "YouTube dočasně blokuje (od %s, další pokus %s)" % (YT_BLOK["od"], time.strftime("%H:%M", time.localtime(YT_BLOK["do"])))


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
    args = [YTDLP, "-J", "--no-playlist", "--no-cache-dir", "--socket-timeout", "15"] + _js_runtime()
    for k, v in (cam.get("headers") or {}).items():
        args += ["--add-header", "%s:%s" % (k, v)]
    args.append(cam["source"])

    async def _spust(a):
        async with _YTDLP_ZAMEK:
            p = await asyncio.create_subprocess_exec(*a, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(p.communicate(), timeout=cfg["resolve_timeout"])
            except asyncio.TimeoutError:
                p.kill()
                await p.wait()
                raise ResolveError("yt-dlp: timeout %d s" % cfg["resolve_timeout"])
        e = err.decode(errors="replace").strip()
        radky = [x for x in e.splitlines() if x.startswith("ERROR")] or e.splitlines() or ["?"]
        return p.returncode, out, radky[-1], e

    cookies = _cookies(cfg)
    if yt_blokovano():
        if not cookies:
            raise YtBlok(_blok_zprava())
        args = args[:1] + ["--cookies", cookies] + args[1:]       # během blokace jen s přihlášením
    rc, out, msg, cele = await _spust(args)
    if rc != 0 and _je_blok(cele) and cookies and "--cookies" not in args:
        log.info("cam %s: YouTube chce přihlášení – zkouším s cookies", cam.get("id"))
        args = args[:1] + ["--cookies", cookies] + args[1:]
        rc, out, msg, cele = await _spust(args)
        YT_BLOK["s_cookies"] = rc == 0
    # YT_MWEB_V1: majitel zakázal přehrávání mimo YouTube (embed) → výchozí klienti yt-dlp (tv/android_vr) odmítnou,
    # mobilní web (mweb) přenos načte (16. 11. Panorama zYLsqXPfMqw)
    if rc != 0 and "other websites has been disabled" in msg and re.search(r"youtu\.?be", cam["source"]):
        rc, out, msg, cele = await _spust(args[:-1] + ["--extractor-args", "youtube:player_client=mweb", cam["source"]])
    if rc != 0:
        # „This video is not available“ chodí při blokaci i bez 429 – za blokaci se bere, když byla zjištěná v poslední hodině
        if _je_blok(cele) or ("This video is not available" in msg and time.time() - YT_BLOK["kdy"] < 3600):
            _blok_zjisten(cele)
            raise YtBlok("yt-dlp: " + _blok_zprava())
        raise ResolveError("yt-dlp: " + msg[:300])
    _blok_konec()
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


HOLIDAYINFO = "https://holidayinfo.cz"


async def _holidayinfo(cfg, cam):
    """HOLIDAYINFO_V1 (15. 11. 2026, uživatel: „krátké streamy z holidayinfo.cz“): holidayinfo.cz dává u části kamer
    poslední krátký klip (~40 s MP4, 1080p) – jako feratel se z něj dělá smyčka (smycka.py). Adresa kamery
    https://holidayinfo.cz/cs/camera/<středisko>[?camid=N] (číslo kamery i v poli match); data jsou ve stránce (setupCamDiv)."""
    src = cam["source"]
    m = re.search(r"holidayinfo\.cz/(?:[a-z]{2}/)?camera/([\w-]+)", src)
    if not m:
        raise ResolveError("holidayinfo: adresa má být https://holidayinfo.cz/cs/camera/<středisko>[?camid=číslo]")
    camid = (cam.get("match") or "").strip()
    if not camid.isdigit():
        # číslo kamery: ?camid=N nebo v cestě /camera/<středisko>/N (odkaz z webu holidayinfo, 15. 11. 2026)
        mc = re.search(r"[?&]camid=(\d+)", src) or re.search(r"/camera/[\w-]+/(\d+)", src)
        camid = mc.group(1) if mc else ""
    stranka = "%s/cs/camera/%s" % (HOLIDAYINFO, m.group(1)) + ("?camid=" + camid if camid else "")
    text, _ = await hls.fetch_text(cfg, stranka, cam.get("headers"))
    i = text.find("setupCamDiv(")
    if i < 0:
        raise ResolveError("holidayinfo: stránka kamery nemá data (změnil se web?)")
    try:
        d, _ = json.JSONDecoder().raw_decode(text[i + len("setupCamDiv("):])
    except ValueError:
        raise ResolveError("holidayinfo: data kamery nejdou přečíst")
    cd, video = d.get("camdata") or {}, d.get("video") or {}
    if str(cd.get("active", "")).upper() != "CHECKED":
        raise ResolveError("holidayinfo: kamera %s teď není v provozu" % (cd.get("nazev_cam") or camid))
    if not video.get("url_mp4"):
        # HOLIDAYINFO_SNIMEK_V1 (15. 11. 2026, Kyčerka): většina kamer dává jen obrázek (~každých 35 min) →
        # poslední obrázek (plné rozlišení) a z něj smyčka jako u snímkových kamer; resolve() vrátí typ „snimek“
        dt = "%s%s" % (cd.get("datum", ""), cd.get("cas", ""))
        if not re.fullmatch(r"\d{14}", dt):
            raise ResolveError("holidayinfo: kamera %s nemá video ani čas posledního obrázku" % (cd.get("nazev_cam") or camid))
        stari = time.time() - time.mktime(time.strptime(dt, "%Y%m%d%H%M%S"))
        if stari > SNIMEK_MAX_STARI:
            raise ResolveError("holidayinfo: obrázek kamery %s se neobnovuje (naposledy před %d h)" % (cd.get("nazev_cam") or camid, stari // 3600))
        img = "%s/hol3_data.php?type=panoimg_last&camid=%s&dt=%s" % (HOLIDAYINFO, cd.get("id_kamery") or camid, dt)
        async with hls.client(cfg).stream("GET", img, headers=hls.req_headers(cfg, cam.get("headers"))) as r:
            if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/"):
                raise ResolveError("holidayinfo: obrázek kamery není (HTTP %s)" % r.status_code)
        return img
    url = "%s/%s&dt=%s%s" % (HOLIDAYINFO, video["url_mp4"].lstrip("/"), cd.get("datum", ""), cd.get("cas", ""))
    async with hls.client(cfg).stream("GET", url, headers=hls.req_headers(cfg, cam.get("headers"))) as r:
        if r.status_code != 200 or "video" not in r.headers.get("content-type", ""):
            raise ResolveError("holidayinfo: klip kamery není (HTTP %s)" % r.status_code)
    return url


SNIMEK_MAX_STARI = 6 * 3600       # starší obrázek = kamera zamrzla (Last-Modified)
SNIMEK_HODINA = "{hodina_utc}"          # v adrese snímku: aktuální hodina UTC jako 20261108_0900


async def _snimek(cfg, cam):
    """Adresa může obsahovat {hodina_utc} (snímky Ventusky): zkusí aktuální hodinu a dvě předchozí, nejnovější bývá ještě nenahraná."""
    if SNIMEK_HODINA not in cam["source"]:
        return await _snimek_adresa(cfg, cam, cam["source"])
    ted = int(time.time() // 3600) * 3600
    chyba = None
    for i in range(3):
        adresa = cam["source"].replace(SNIMEK_HODINA, time.strftime("%Y%m%d_%H00", time.gmtime(ted - i * 3600)))
        try:
            return await _snimek_adresa(cfg, cam, adresa)
        except ResolveError as e:
            chyba = chyba or e
    raise chyba


async def _snimek_adresa(cfg, cam, adresa):
    """kamera, která dává jen obrázek (JPG/PNG, obnovovaný po minutách – ČHMÚ, holidayinfo…): ověří, že je to obrázek
    a není zamrzlý; služba z něj dělá video smyčku (smycka.py)"""
    async with hls.client(cfg).stream("GET", adresa, headers=hls.req_headers(cfg, cam.get("headers"))) as r:
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
    return adresa


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
    cookies = _cookies(cfg)
    if yt_blokovano() and not cookies:
        raise YtBlok("ytkanal: " + _blok_zprava())
    extra = ["--cookies", cookies] if yt_blokovano() else []
    p = await asyncio.create_subprocess_exec(YTDLP, "--flat-playlist", "-J", "--no-cache-dir", "--playlist-end", "80", *extra,
                                             kanal + "/streams", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout=cfg["resolve_timeout"])
    except asyncio.TimeoutError:
        p.kill()
        raise ResolveError("ytkanal: timeout výpisu kanálu")
    if p.returncode != 0:
        e = err.decode(errors="replace").strip()
        if _je_blok(e):
            _blok_zjisten(e)
            raise YtBlok("ytkanal: " + _blok_zprava())
        raise ResolveError("ytkanal: " + (([x for x in e.splitlines() if x.startswith("ERROR")] or e.splitlines() or ["?"])[-1])[:200])
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
        except YtBlok:
            raise
        except ResolveError:
            _ze_stranky.pop(cid, None)             # i nové video skončilo – zkusit znovu od začátku
    if cid in _naucene:
        kanal, nazev = _naucene[cid]
        try:
            return (await _novy_prenos(cfg, cam, kanal, nazev))[0]
        except YtBlok:
            raise
        except ResolveError:
            pass                                   # třeba původní video zase vysílá
    try:
        url = await _ytdlp(cfg, cam)
        _naucene.pop(cid, None)
        zmeny.pop(cid, None)
        return url
    except ResolveError as e:
        if isinstance(e, YtBlok) or "watch?v=" not in cam["source"]:
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


async def _rtsp(cfg, cam):
    """RTSP_V1 (21. 11. 2026): veřejná kamera rtsp:// – adresa je rovnou zdroj, přehrává se přes ffmpeg (main.py, rtsp.py)"""
    src = (cam.get("source") or "").strip()
    if not src.lower().startswith(("rtsp://", "rtsps://")):
        raise ResolveError("rtsp: adresa musí začínat rtsp://")
    return src


RESOLVERS = {"rtsp": _rtsp, "direct": _direct, "ytdlp": _ytdlp_cam, "ytkanal": _ytkanal, "skyline": _skyline, "feratel": _feratel, "holidayinfo": _holidayinfo,
             "ipcamlive": _ipcamlive, "mjpeg": _mjpeg, "regex": _regex, "snimek": _snimek}
KLIP = ("feratel", "holidayinfo")         # poslední klip MP4 → smyčka (smycka.py)
NE_HLS = KLIP + ("mjpeg", "snimek", "rtsp")   # výsledek není HLS playlist (smyčka klipu / MJPEG / smyčka ze snímku)


def typ_odhad(cam):
    """první typ, který se zkusí (pro playlist: MJPEG bez inputstream.adaptive)"""
    return _poradi(cam)[0]


def _poradi(cam):
    t = cam.get("type") or "auto"
    if t != "auto":
        return [t]
    src = cam["source"].lower()
    if src.startswith(("rtsp://", "rtsps://")):        # RTSP_V1
        return ["rtsp"]
    if "skylinewebcams.com" in src:
        return ["skyline"]
    if "feratel.com" in src:
        return ["feratel"]
    if "holidayinfo.cz/" in src and "/camera/" in src:
        return ["holidayinfo"]
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
    blok = False
    for t in _poradi(cam):
        try:
            url = await RESOLVERS[t](cfg, cam)
            if t == "holidayinfo" and "type=panoimg_last" in url:
                t = "snimek"                  # kamera holidayinfo jen s obrázkem → smyčka ze snímku
            log.info("resolve %s: %s OK", cam["id"], t)
            return url, t
        except ResolveError as e:
            chyby.append(str(e))
            blok = blok or isinstance(e, YtBlok)
        except Exception as e:   # síť, HTTP chyby…
            chyby.append("%s: %s" % (t, e.__class__.__name__ + (" " + str(e) if str(e) else "")))
    raise (YtBlok if blok else ResolveError)(" | ".join(chyby)[:600])


async def ytdlp_version():
    try:
        p = await asyncio.create_subprocess_exec(YTDLP, "--version", stdout=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(p.communicate(), 10)
        return out.decode().strip()
    except Exception:
        return None
