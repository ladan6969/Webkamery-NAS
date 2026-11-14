"""hls.py – práce s HLS playlisty: zkrácený master (Enigma2), absolutní URI, přepis pro proxy.

Proxy odkazy nesou cílovou URL (base64) a HMAC podpis – proxy tak stáhne jen to, co sama vygenerovala
(nejde zneužít jako otevřená proxy na cokoli jiného).
"""
import base64
import hashlib
import hmac
import re
from urllib.parse import quote, urljoin

import httpx

import store

_client = None
URI_ATTR = re.compile(r'URI="([^"]+)"')


def client(cfg):
    global _client
    if _client is None:
        _client = httpx.AsyncClient(follow_redirects=True, timeout=cfg["http_timeout"],
                                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))
    return _client


def req_headers(cfg, headers):
    h = {"User-Agent": cfg["user_agent"]}
    h.update(headers or {})
    return h


async def fetch_text(cfg, url, headers=None):
    r = await client(cfg).get(url, headers=req_headers(cfg, headers))
    r.raise_for_status()
    return r.text, str(r.url)


def is_master(text):
    return "#EXT-X-STREAM-INF" in text


def _attrs(line):
    """atributy tagu „#EXT-X-…:A=1,B="x,y"“ → dict"""
    out = {}
    body = line.split(":", 1)[1] if ":" in line else ""
    for m in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', body):
        out[m.group(1)] = m.group(2).strip('"')
    return out


def variants(text):
    """[(attrs, uri, index_řádku_tagu)]"""
    lines = text.splitlines()
    out = []
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF"):
            for j in range(i + 1, len(lines)):
                u = lines[j].strip()
                if u and not u.startswith("#"):
                    out.append((_attrs(l), u, i))
                    break
    return out


def _vykon(attrs):
    res = attrs.get("RESOLUTION", "")
    try:
        w, h = (int(x) for x in res.lower().split("x"))
    except ValueError:
        w = h = 0
    avc = attrs.get("CODECS", "").startswith("avc1") or ",avc1" in attrs.get("CODECS", "")   # při stejném rozlišení přednost H.264
    return (w * h, avc, int(attrs.get("BANDWIDTH", "0") or 0))


def best_variant(text):
    v = variants(text)
    return max(v, key=lambda x: _vykon(x[0])) if v else None


def absolutize(text, base):
    """všechny URI (řádky i URI="…" v tazích) na absolutní"""
    out = []
    for l in text.splitlines():
        s = l.strip()
        if not s:
            continue
        if s.startswith("#"):
            out.append(URI_ATTR.sub(lambda m: 'URI="%s"' % urljoin(base, m.group(1)), s))
        else:
            out.append(urljoin(base, s))
    return "\n".join(out) + "\n"


def reduce_master(text, base):
    """Master jen s nejvyšší variantou (podle RESOLUTION, pak BANDWIDTH) a její audio stopou, URI absolutní.
    Gstreamer v Enigma2 z plného masteru bere nejnižší kvalitu."""
    best = best_variant(text)
    if not best:
        return absolutize(text, base)
    attrs, uri, idx = best
    audio = attrs.get("AUDIO")
    lines = text.splitlines()
    out = ["#EXTM3U"]
    for l in lines:
        s = l.strip()
        if s.startswith(("#EXT-X-VERSION", "#EXT-X-INDEPENDENT-SEGMENTS")):
            out.append(s)
        elif s.startswith("#EXT-X-MEDIA") and "TYPE=AUDIO" in s:
            a = _attrs(s)
            if not audio or a.get("GROUP-ID") == audio:
                out.append(URI_ATTR.sub(lambda m: 'URI="%s"' % urljoin(base, m.group(1)), s))
    out.append(lines[idx].strip())
    out.append(urljoin(base, uri))
    return "\n".join(out) + "\n"


# ── proxy ───────────────────────────────────────────────────────────────────────
def _sig(cfg, cid, target):
    return hmac.new(cfg["secret"].encode(), (cid + "|" + target).encode(), hashlib.sha256).hexdigest()[:20]


def proxy_url(cfg, cid, target, key):
    b = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return "%s/proxy/%s?u=%s&s=%s&k=%s" % (store.base(cfg), quote(cid), b, _sig(cfg, cid, target), quote(key))


def proxy_target(cfg, cid, u, s):
    """→ cílová URL, nebo None při neplatném podpisu"""
    try:
        target = base64.urlsafe_b64decode(u + "=" * (-len(u) % 4)).decode()
    except Exception:
        return None
    if not target.startswith(("http://", "https://")) or not hmac.compare_digest(_sig(cfg, cid, target), s or ""):
        return None
    return target


def rewrite_for_proxy(cfg, cid, text, base, key):
    """playlist (master i media) → všechny URI přes /proxy/{id} (včetně #EXT-X-KEY, #EXT-X-MAP, audio stop)"""
    out = []
    for l in text.splitlines():
        s = l.strip()
        if not s:
            continue
        if s.startswith("#"):
            out.append(URI_ATTR.sub(lambda m: 'URI="%s"' % proxy_url(cfg, cid, urljoin(base, m.group(1)), key), s))
        else:
            out.append(proxy_url(cfg, cid, urljoin(base, s), key))
    return "\n".join(out) + "\n"


# ── stopy streamu (kamery bez zvuku v Kodi) ─────────────────────────────────
_TS_ZVUK = {0x03, 0x04, 0x06, 0x0F, 0x11, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0xA1, 0xA2}     # 0x06 může být AC-3 → raději počítat jako zvuk


def ts_stopy(data):
    """z úseku MPEG-TS: {"video": typ, "zvuk": bool, "kodek": "avc1…"}, None = nepoznáno"""
    zac = next((i for i in range(min(376, len(data) - 376)) if data[i] == 0x47 and data[i + 188] == 0x47), None) if len(data) > 564 else None
    if zac is None:
        return None
    pmt, stopy, video_pid, es = None, None, None, bytearray()
    for i in range(zac, len(data) - 187, 188):
        p = data[i:i + 188]
        if p[0] != 0x47:
            return None
        pid = ((p[1] & 0x1F) << 8) | p[2]
        o = 4
        if p[3] & 0x20:
            o += 1 + p[4]
        if not p[3] & 0x10 or o >= 188:
            continue
        sec = None
        if p[1] & 0x40 and pid in (0, pmt):
            sec = p[o + 1 + p[o]:]
        if pid == 0 and sec and pmt is None and len(sec) >= 12:
            pmt = ((sec[10] & 0x1F) << 8) | sec[11]
        elif pid == pmt and sec and stopy is None and len(sec) >= 12 and sec[0] == 0x02:
            konec = min(3 + (((sec[1] & 0x0F) << 8) | sec[2]) - 4, len(sec))
            j = 12 + (((sec[10] & 0x0F) << 8) | sec[11])
            stopy = []
            while j + 5 <= konec:
                stopy.append((sec[j], ((sec[j + 1] & 0x1F) << 8) | sec[j + 2]))
                j += 5 + (((sec[j + 3] & 0x0F) << 8) | sec[j + 4])
            video_pid = next((s[1] for s in stopy if s[0] in (0x1B, 0x24)), None)
        elif pid == video_pid and video_pid is not None:
            es += p[o:]
    if not stopy or video_pid is None:
        return None
    typ = next(s[0] for s in stopy if s[1] == video_pid)
    kodek = "hvc1.1.6.L120.90" if typ == 0x24 else "avc1.4d4028"
    if typ == 0x1B:
        k = bytes(es).find(b"\x00\x00\x01\x67")
        if k >= 0 and k + 7 <= len(es):
            kodek = "avc1." + bytes(es[k + 4:k + 7]).hex()
    return {"video": typ, "zvuk": any(s[0] in _TS_ZVUK for s in stopy), "kodek": kodek}


async def stopy_segmentu(cfg, url, headers=None, limit=376000):
    """prvních pár set kB segmentu → ts_stopy; chyba = None"""
    data = bytearray()
    try:
        async with client(cfg).stream("GET", url, headers=req_headers(cfg, headers)) as r:
            if r.status_code >= 400:
                return None
            async for c in r.aiter_bytes():
                data += c
                if len(data) >= limit:
                    break
    except Exception:
        return None
    return ts_stopy(bytes(data))


def master_jen_obraz(url, kodek):
    """Kodi (inputstream.adaptive) u playlistu bez údajů čeká i zvuk; u streamu bez zvuku hned hlásí konec → master s kodekem jen obrazu"""
    return '#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-STREAM-INF:BANDWIDTH=4000000,CODECS="%s"\n%s\n' % (kodek, url)


def media_sequence(text):
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    return int(m.group(1)) if m else None


def last_segment(text):
    segs = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    return segs[-1] if segs else None


# ── živé okno (YouTube) ─────────────────────────────────────────────────────
def dlouhe_okno(uri):
    """YouTube posílá živé kamery s hodinovým záznamem (playlist_duration/3600) – Kodi pak nezačne od živého konce
    (11. 11. 2026 Hlavní nádraží o 16 min pozadu). Okno u YouTube změnit nejde (adresa je podepsaná)."""
    return "googlevideo.com" in uri and "/live/1/" in uri and "playlist_duration/" in uri


def oriz_media(text, base, segmentu=6):
    """media playlist → jen posledních N segmentů (MEDIA-SEQUENCE posunutá, poslední #EXT-X-KEY/MAP zachovaný), URI absolutní"""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    hlava, segs, cekajici = [], [], []
    for l in lines:
        if not l.startswith("#"):
            segs.append((cekajici, l))
            cekajici = []
        elif not segs and not cekajici and not l.startswith(("#EXTINF", "#EXT-X-PROGRAM-DATE-TIME", "#EXT-X-DISCONTINUITY", "#EXT-X-BYTERANGE", "#EXT-X-KEY", "#EXT-X-MAP")):
            hlava.append(l)
        else:
            cekajici.append(l)
    if len(segs) <= segmentu:
        return absolutize(text, base)
    pryc, nech = segs[:-segmentu], segs[-segmentu:]
    trvale = {}                                   # poslední KEY/MAP z vyhozené části platí i pro ponechané segmenty
    disk = 0
    for tagy, _ in pryc:
        for t in tagy:
            if t.startswith(("#EXT-X-KEY", "#EXT-X-MAP")):
                trvale[t.split(":", 1)[0]] = t
            elif t == "#EXT-X-DISCONTINUITY":
                disk += 1
    out = []
    for l in hlava:
        if l.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            l = "#EXT-X-MEDIA-SEQUENCE:%d" % (int(l.split(":", 1)[1]) + len(pryc))
        elif l.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:") and disk:
            l = "#EXT-X-DISCONTINUITY-SEQUENCE:%d" % (int(l.split(":", 1)[1]) + disk)
        elif l.startswith("#EXT-X-PLAYLIST-TYPE"):
            continue                                  # EVENT by přehrávač vedl na začátek
        out.append(l)
    if disk and not any(l.startswith("#EXT-X-DISCONTINUITY-SEQUENCE") for l in out):
        out.append("#EXT-X-DISCONTINUITY-SEQUENCE:%d" % disk)
    prvni_tagy = {t.split(":", 1)[0] for t in nech[0][0]}
    out += [t for k, t in trvale.items() if k not in prvni_tagy]
    for tagy, uri in nech:
        out += tagy
        out.append(uri)
    return absolutize("\n".join(out), base)


def oriz_url(cfg, cid, target, key):
    b = base64.urlsafe_b64encode(target.encode()).decode().rstrip("=")
    return "%s/oriz/%s?u=%s&s=%s&k=%s" % (store.base(cfg), quote(cid), b, _sig(cfg, cid, target), quote(key))


def presmeruj_okno(cfg, cid, text, key):
    """v (už zkráceném, absolutním) masteru vede media playlisty s dlouhým oknem přes /oriz"""
    out = []
    for l in text.splitlines():
        if l.startswith("#"):
            out.append(URI_ATTR.sub(lambda m: 'URI="%s"' % (oriz_url(cfg, cid, m.group(1), key) if dlouhe_okno(m.group(1)) else m.group(1)), l))
        else:
            out.append(oriz_url(cfg, cid, l, key) if dlouhe_okno(l) else l)
    return "\n".join(out) + "\n"
