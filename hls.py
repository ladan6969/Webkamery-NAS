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
    return (w * h, int(attrs.get("BANDWIDTH", "0") or 0))


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
    return "%s/proxy/%s?u=%s&s=%s&k=%s" % (cfg["public_base"], quote(cid), b, _sig(cfg, cid, target), quote(key))


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


def media_sequence(text):
    m = re.search(r"#EXT-X-MEDIA-SEQUENCE:(\d+)", text)
    return int(m.group(1)) if m else None


def last_segment(text):
    segs = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith("#")]
    return segs[-1] if segs else None
