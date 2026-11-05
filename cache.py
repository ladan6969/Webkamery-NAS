"""cache.py – cache aktuálních stream URL (stejný princip jako dynamická cache u OnePlay).

Klíč = id kamery. Platnost se bere z URL (YouTube expire, JWT exp, Akamai hdnts exp), drží se do exp − 5 min,
nejvýš 4 h; bez expirace 30 min. Po 60 min od posledního ověření se playlist lehce ověří (GET prvních pár kB):
401/403/404/410 = zahodit a resolvovat znovu, síťová chyba / timeout = ponechat.
"""
import asyncio
import base64
import json
import re
import time
from urllib.parse import parse_qs, urlparse

_cache = {}          # id → dict(url, headers, kind, created, expires, verified)
_locks = {}          # id → asyncio.Lock (jen jeden resolve naráz)


def lock(cid):
    if cid not in _locks:
        _locks[cid] = asyncio.Lock()
    return _locks[cid]


def _jwt_exp(tok):
    try:
        part = tok.split(".")[1]
        part += "=" * (-len(part) % 4)
        return int(json.loads(base64.urlsafe_b64decode(part)).get("exp") or 0) or None
    except Exception:
        return None


def expiry_from_url(url):
    """Unix čas expirace z URL, nebo None."""
    u = urlparse(url)
    q = parse_qs(u.query)
    for k in ("expire", "expires", "exp", "e"):
        v = (q.get(k) or [""])[0]
        if v.isdigit() and len(v) >= 9:
            return int(v)
    # YouTube manifest: …/expire/1793751495/… v cestě
    m = re.search(r"/expire/(\d{9,11})(?:/|$)", u.path)
    if m:
        return int(m.group(1))
    # JWT v token= / cdntoken= / jwt=
    for k in ("token", "cdntoken", "jwt", "t"):
        v = (q.get(k) or [""])[0]
        if v.count(".") == 2:
            e = _jwt_exp(v)
            if e:
                return e
    # Akamai hdnts=st=…~exp=…~hmac=…
    m = re.search(r"(?:hdnts|hdnea|__token__)=[^&]*?exp=(\d{9,11})", url)
    if m:
        return int(m.group(1))
    return None


def put(cid, url, headers, kind, cfg):
    now = time.time()
    exp = expiry_from_url(url)
    if exp:
        until = min(exp - cfg["expire_margin"], now + cfg["max_ttl"])
    else:
        until = now + cfg["default_ttl"]
    e = {"url": url, "headers": headers or {}, "kind": kind, "created": now, "expires": until, "verified": now,
         "url_expire": exp}
    _cache[cid] = e
    return e


def get(cid):
    e = _cache.get(cid)
    if e and e["expires"] > time.time():
        return e
    if e:
        _cache.pop(cid, None)
    return None


def drop(cid):
    _cache.pop(cid, None)


def needs_revalidate(e, cfg):
    return time.time() - e["verified"] > cfg["revalidate_after"]


def mark_verified(cid):
    if cid in _cache:
        _cache[cid]["verified"] = time.time()


def snapshot():
    now = time.time()
    return {k: {"kind": v["kind"], "zbyva_s": int(v["expires"] - now), "stari_s": int(now - v["created"])}
            for k, v in _cache.items()}
