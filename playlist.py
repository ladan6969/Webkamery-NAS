"""playlist.py – M3U (Kodi / VLC / Enigma2), Enigma2 bouquet (4097) a XMLTV EPG."""
import datetime
from urllib.parse import quote
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

import checker
import resolver
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
    return "%s/cam/%s?client=%s&k=%s" % (cfg["public_base"], quote(cid), client, quote(key))


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


def m3u(cfg, client, key, podle=None, cz=None):
    cams, s = _viditelne(cfg, podle, cz, oblibene=True)
    b = cfg["public_base"]
    out = ['#EXTM3U url-tvg="%s/epg.xml?k=%s"' % (b, quote(key))]
    for c in cams:
        out.append('#EXTINF:-1 tvg-id="%s" tvg-name="%s" tvg-logo="%s/thumb/%s.jpg?k=%s" group-title="%s",%s' % (
            c["id"], c["name"].replace('"', "'"), b, quote(c["id"]), quote(key), c["kategorie"].replace('"', "'"), c["name"]))
        url = cam_url(cfg, c["id"], client, key)
        mjpeg = resolver.typ_odhad(c) == "mjpeg" or (s["cams"].get(c["id"]) or {}).get("type_used") == "mjpeg"
        if client == "kodi" and not mjpeg:
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
        url = quote(cam_url(cfg, c["id"], "e2", key), safe="")
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
