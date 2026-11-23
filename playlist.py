"""playlist.py – M3U (Kodi / VLC / Enigma2), Enigma2 bouquet (4097) a XMLTV EPG."""
import datetime
from urllib.parse import quote
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import checker
import resolver
import yt4k
import store
import zeme


OBLIBENE = "⭐ Oblíbené"


def _viditelne(cfg, podle=None, cz=None, oblibene=False):
    s = store.state()
    vid = [c for c in store.cams() if c["enabled"] and checker.visible(s["cams"].get(c["id"]), cfg)]
    pocty = {}
    for c in vid:
        k = (c.get("country") or "").upper()
        pocty[k] = pocty.get(k, 0) + 1
    velke = {k for k, n in pocty.items() if k and n > cfg.get("velka_zeme", zeme.VELKA_ZEME)}
    out = [dict(c, kategorie=zeme.skupina(c, cfg, podle, cz, velke)) for c in vid]
    # domácí země (Česko) nahoře, pak abecedně
    out = sorted(out, key=lambda c: (not c["kategorie"].startswith(tuple(zeme.nazev(k) for k in zeme.DOMACI)),
                                     c["kategorie"].lower(), c["name"].lower()))
    if oblibene:          # oblíbené navíc nahoře (kamera zůstává i ve své kategorii); EPG je nepotřebuje
        out = [dict(c, kategorie=OBLIBENE) for c in sorted(out, key=lambda c: c["name"].lower()) if c.get("oblibena")] + out
    return out, s


def cam_url(cfg, cid, client, key):
    return "%s/cam/%s?client=%s&k=%s" % (store.base(cfg), quote(cid), client, quote(key))


FF_BEZ = ("mjpeg",)      # MJPEG hraje Kodi samo; feratel/snímek jdou přes KODI_SMYCKA_V1 (nekonečný TS)


def kodi_ff(cfg, cam, st=None):
    """KODI_FFMPEG_V1: má se kamera pro Kodi posílat přes ffmpeg jako MPEG-TS (config kodi_ffmpeg)"""
    if not cfg.get("kodi_ffmpeg"):
        return False
    typ = (st or {}).get("type_used") or resolver.typ_odhad(cam)
    return typ not in FF_BEZ


def mode_for(cam, client, st=None):
    """redirect | proxy – Enigma2 s hlavičkami vždy proxy (SREF umí jen #User-Agent=, ne Referer);
    auto + zdroj odmítá přehrávače (kontrola: force_proxy) → proxy"""
    m = cam.get("mode") or "auto"
    if m == "auto":
        m = "proxy" if cam.get("headers") or (st or {}).get("force_proxy") else "redirect"
    if client == "e2" and cam.get("headers"):
        m = "proxy"
    if client == "web":                     # přehrávač v administraci: vše přes službu (CORS, hlavičky zdroje)
        m = "proxy"
    return m


def rtsp_primo(c, client):
    """RTSP_PRIMO_V1 (22. 11. 2026): přes ffmpeg služby RTSP seká (časové značky Tapo, Wi-Fi), Kodi s rtsp:// přímo hraje plynule.
    Adresa i s přihlášením kamery je pak v playlistu (chráněném klíčem) – jen pro kamery v domácí síti zařízení."""
    return client in ("kodi", "vlc", "e2") and resolver.typ_odhad(c) == "rtsp" and c.get("rtsp_primo", True)


def m3u(cfg, client, key, podle=None, cz=None):
    cams, s = _viditelne(cfg, podle, cz, oblibene=True)
    b = store.base(cfg)
    out = ['#EXTM3U url-tvg="%s/epg.xml?k=%s"' % (b, quote(key))]
    for c in cams:
        out.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s/thumb/%s.jpg?k=%s" group-title="%s",%s' % (
            c["id"], c["name"].replace('"', "'"), b, quote(c["id"]), quote(key), c["kategorie"].replace('"', "'"), c["name"]))
        url = cam_url(cfg, c["id"], client, key)
        if rtsp_primo(c, client):
            out.append(c["source"].strip())        # RTSP_PRIMO_V1: přehrávač bere kameru sám (jako .strm v Kodi)
            continue
        mjpeg = resolver.typ_odhad(c) == "mjpeg" or (s["cams"].get(c["id"]) or {}).get("type_used") == "mjpeg"
        if client == "kodi" and c.get("uhd") and yt4k.dostupne(cfg):
            # YT_4K_V1 (16. 11. 2026): YouTube ve 4K jako fMP4; když 4K nejde, /cam4k přesměruje na běžnou cestu
            url = "%s/cam4k/%s?client=kodi&k=%s" % (store.base(cfg), quote(c["id"]), quote(key))
        elif client == "kodi" and (kodi_ff(cfg, c, s["cams"].get(c["id"])) or resolver.typ_odhad(c) == "rtsp"):   # RTSP_V1: vždy TS
            # KODI_FFMPEG_V1 (15. 11. 2026): InputStream Adaptive na boxu vypíná obraz YouTube kamer
            # („Codec id 27 require extradata“) → služba pošle hotový MPEG-TS přes ffmpeg, Kodi ho hraje sám
            out.append("#KODIPROP:mimetype=video/mp2t")
            url += "&ff=1"
        elif client == "kodi" and not mjpeg:
            out.append("#KODIPROP:inputstream=inputstream.adaptive")
            out.append("#KODIPROP:inputstream.adaptive.manifest_type=hls")
            if c.get("headers") and mode_for(c, client, s["cams"].get(c["id"])) == "redirect":
                url += "|" + "&".join("%s=%s" % (k, quote(v, safe="")) for k, v in c["headers"].items())
        out.append(url)
    return "\n".join(out) + "\n"


def bouquet(cfg, key, podle=None, cz=None):
    cams, s = _viditelne(cfg, podle, cz, oblibene=True)
    out = ["#NAME Webkamery"]
    skupina, n = None, 0
    zmena = False
    for c in cams:
        if c["kategorie"] != skupina:
            skupina = c["kategorie"]
            n += 1
            out.append("#SERVICE 1:64:%X:0:0:0:0:0:0:0::%s" % (n, skupina))
            out.append("#DESCRIPTION %s" % skupina)
        if c["id"] not in s["sids"]:
            zmena = True
        sid = store.sid_for(s, c["id"])
        url = quote(c["source"].strip() if rtsp_primo(c, "e2") else cam_url(cfg, c["id"], "e2", key), safe="")
        out.append("#SERVICE 4097:0:1:%X:0:0:0:0:0:0:%s:%s" % (sid, url, c["name"]))
        out.append("#DESCRIPTION %s" % c["name"])
    if zmena:
        store.save_state(s)
    return "\n".join(out) + "\n"


def epg(cfg):
    cams, _ = _viditelne(cfg)
    tz = ZoneInfo(cfg["epg_tz"])
    dnes = datetime.datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    f = lambda d: d.strftime("%Y%m%d%H%M%S %z")
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="webcams">']
    for c in cams:
        out.append('  <channel id="%s"><display-name>%s</display-name></channel>' % (escape(c["id"]), escape(c["name"])))
    for c in cams:
        popis = ", ".join(x for x in (zeme.nazev(c.get("country")), c.get("category"), "zdroj: " + checker.domena(c["source"])) if x)
        for den in (0, 1):
            od = dnes + datetime.timedelta(days=den)
            out.append('  <programme start="%s" stop="%s" channel="%s"><title lang="cs">%s</title><desc lang="cs">%s</desc></programme>' % (
                f(od), f(od + datetime.timedelta(days=1)), escape(c["id"]), escape("🔴 Živě – " + c["name"]), escape(popis)))
    out.append("</tv>")
    return "\n".join(out) + "\n"
