"""store.py – JSON soubory služby (config.json, cams.json, state.json) s atomickým zápisem.

Zápis přes tempfile + os.replace, takže soubor nikdy není napůl zapsaný. Jediný zapisovatel je běžící služba
(kontrola běží uvnitř služby, ne jako druhý proces), proto stačí zámek v rámci procesu.
"""
import re
import json
import os
import tempfile
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
_lock = threading.RLock()

CONFIG_DEFAULT = {
    "bind": "127.0.0.1",
    "port": 8093,
    # veřejná adresa služby (za nginx) – z ní se skládají odkazy v playlistu, bouquetu a proxy
    "public_base": "http://127.0.0.1:8093",
    # klíče pro přehrávače (?k=…): playlist, bouquet, EPG, kamery, náhledy, proxy
    "access_keys": [],
    # administrace: heslo (pbkdf2) + tajemství pro podpis přihlášení a proxy odkazů
    "admin_password_hash": "",
    "secret": "",
    # token pro cron (spuštění kontroly) – hlavička X-Webcams-Token
    "cron_token": "",
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "resolve_timeout": 30,
    "http_timeout": 15,
    "default_ttl": 1800,          # cache bez expirace v URL: 30 min
    "max_ttl": 14400,             # nejvýš 4 h
    "expire_margin": 300,         # od expirace z URL odečíst 5 min
    "revalidate_after": 3600,     # po 60 min lehké ověření playlistu
    "check_pause": 2.5,           # pauza mezi kamerami při kontrole (v každém souběžném vlákně)
    "check_parallel": 6,          # kolik kamer se kontroluje naráz (šetrně – stovky kamer by jinak trvaly půl dne)
    "check_live_wait": 10,        # čekání na posun MEDIA-SEQUENCE
    "slow_threshold": 10,         # resolve + start nad 10 s = pomalá
    "dead_after": 3,              # po 3 neúspěšných denních kontrolách v řadě skrýt
    "dead_weekly_after": 7,       # skrytá kamera, která nejde 7 dní, se dál zkouší jen jednou týdně (0 = každou noc)
    "thumbs_dir": "thumbs",
    "epg_tz": "Europe/Prague",
    "group_by": "country",        # kategorie playlistu: country (podle země) | group (volná skupina)
    "telegram": {"token": "", "chat_id": ""},
}


def path(name):
    return os.path.join(BASE, name)


def load(name, default):
    with _lock:
        try:
            with open(path(name), "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return default
        except json.JSONDecodeError:
            # poškozený soubor nepřepisovat výchozím – nechat stranou a pokračovat s výchozím
            os.replace(path(name), path(name) + ".poskozeny")
            return default


def save(name, data, indent=1):
    with _lock:
        fd, tmp = tempfile.mkstemp(prefix="." + name + ".", dir=BASE)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, ensure_ascii=False, indent=indent))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path(name))
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def config():
    c = dict(CONFIG_DEFAULT)
    c.update(load("config.json", {}))
    tg = dict(CONFIG_DEFAULT["telegram"])
    tg.update(c.get("telegram") or {})
    c["telegram"] = tg
    return c


# ── kamery ──────────────────────────────────────────────────────────────────────
CAM_DEFAULT = {"name": "", "group": "Ostatní", "country": "", "category": "", "source": "", "type": "auto",
               "headers": {}, "mode": "auto", "enabled": True, "regex": "", "match": "",
               "page": "", "note": "", "oblibena": False}


def cams():
    out = []
    for c in load("cams.json", []):
        x = dict(CAM_DEFAULT)
        x.update(c)
        out.append(x)
    return out


def cam(cid):
    for c in cams():
        if c["id"] == cid:
            return c
    return None


def save_cams(lst):
    save("cams.json", lst)


def state():
    s = load("state.json", {})
    s.setdefault("cams", {})
    s.setdefault("sids", {})
    s.setdefault("next_sid", 0)
    s.setdefault("last_check", None)
    return s


def save_state(s):
    save("state.json", s, indent=None)      # bez odsazení: json.dumps pak použije rychlý kodér v C (state.json má přes 2 MB)


def sid_for(s, cid):
    """Stabilní SID pro bouquet: přidělí se jednou a už se nemění (ani po smazání jiných kamer)."""
    if cid not in s["sids"]:
        s["sids"][cid] = 0xA000 + s["next_sid"]
        s["next_sid"] += 1
    return s["sids"][cid]


def klic_streamu(url):
    """stejný stream = stejný server a cesta (bez schématu a parametrů); u YouTube rozhoduje id videa (DUPLICITY_V1, 12. 11. 2026)"""
    u = (url or "").strip()
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/|embed/|shorts/)|youtu\.be/)([\w-]{11})", u)
    if m:
        return "youtube:" + m.group(1)
    u = re.sub(r"^https?://", "", u).split("#")[0]
    cesta, _, dotaz = u.partition("?")
    par = sorted(p for p in dotaz.split("&") if p and not _PROMENNE.match(p.split("=")[0]))   # cam=, alias= určují kameru
    return cesta.lower().rstrip("/") + ("?" + "&".join(par) if par else "")


# parametry, které se mění (podpisy, tokeny, časy) – kameru neurčují
_PROMENNE = re.compile(r"(?i)^(token|tok|auth\w*|sig|signature|hash|hdnts|hdnea|wmsauthsign|expires?|exp|e|st|t|ts|time|_|nocache|rnd|rand|random|k|key|sid|session\w*)$")


def klic_kamery(c):
    """stream + výběr: z jednoho kanálu/stránky (ytkanal match, regex) bývá víc různých kamer"""
    k = klic_streamu(c.get("source"))
    return k and "%s|%s|%s" % (k, " ".join((c.get("match") or "").lower().split()), (c.get("regex") or "").strip())


def stejny_stream(lst, c, krome=()):
    """kamera ze seznamu se stejným streamem i výběrem (jiné id než krome) nebo None; c = kamera nebo jen adresa"""
    k = klic_kamery({"source": c} if isinstance(c, str) else c)
    if not k:
        return None
    return next((x for x in lst if x["id"] not in krome and klic_kamery(x) == k), None)


def duplicity(lst):
    """skupiny id se stejným streamem i výběrem (jen kamery se zapnutým i vypnutým stavem dohromady)"""
    sk = {}
    for x in lst:
        k = klic_kamery(x)
        if k:
            sk.setdefault(k, []).append(x["id"])
    return [v for v in sk.values() if len(v) > 1]
