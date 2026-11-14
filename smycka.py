"""smycka.py – kamery, které nevysílají živě, ale dávají jen poslední klip (feratel webTV: ~1 min MP4, obnovuje se průběžně).

Při sledování server klip stáhne, bez překódování přebalí na HLS segmenty (ffmpeg -c copy) a podává ho jako nekonečný
„živý“ playlist (posuvné okno podle času, #EXT-X-DISCONTINUITY na konci smyčky). Klip se obnoví po 10 min.
Data jen v /tmp (PrivateTmp služby), drží se poslední 2 verze na kameru. Bez diváků se nic nestahuje.
Typ „snimek“ (7. 11. 2026): z obrázku (JPG/PNG) udělá ffmpeg 20s video (H.264, 2 snímky/s), obnova po 2 min.
CASOSBER_V1 (12. 11. 2026): kamery ČHMÚ – místo stojícího obrázku časosběr posledních N hodin z archivu ČHMÚ
(getWebcamImages.php, snímek po 10 min), na konci chvíli drží nejnovější snímek. Zapíná config „casosber“
{"zapnuto": true, "hodin": 6, "kamery": [] = všechny ČHMÚ}; bez něj (výchozí) vše jako dřív. Snímky se drží v
KOREN/snimky.casosber/<kamera>/ a stahují se jen nové. Když časosběr selže, použije se obyčejný snímek.
CASOSBER_SBER_V1 (13. 11. 2026): ostatní snímkové kamery archiv nemají → snímky sbírá služba sama (SBER/<kamera>/,
přežije restart) po 10 min, jen u oblíbených a sledovaných za posledních 7 dní (main._sber_casosberu); snímek stažený
při přehrání se uloží taky. Časosběr se ukáže, jakmile jsou aspoň 3 různé snímky, do té doby obyčejný snímek.
"""
import asyncio
import hashlib
import json
import logging
import math
import os
import re
import shutil
import time

import hls
import store

log = logging.getLogger("webcams")

KOREN = "/tmp/webcams-smycka"
SBER = None                     # trvalá složka sebraných snímků (nastaví main.py: store.path("casosber"))
SBER_KROK = 600                 # s mezi sebranými snímky
SLEDOVANE_DNI = 7
OBNOVA = 600
OBNOVA_SNIMEK = 120
_stav, _zamky = {}, {}
CHMI_RE = re.compile(r"^(https://produkty\.chmi\.cz/webkamery/)input_data/kam/([a-z0-9_]+)\.jpg$")
CAS_SNIMEK = re.compile(r"^(\d{8})\.(\d{4})\.jpg$")
CAS_FPS = 3            # snímků za sekundu videa (1 snímek ČHMÚ = 10 min)
CAS_DRZET = 4          # s – poslední snímek na konci


def _zamek(cid):
    if cid not in _zamky:
        _zamky[cid] = asyncio.Lock()
    return _zamky[cid]


def casosber_hodin(cfg, cid, url):
    """počet hodin časosběru pro tuto snímkovou kameru, nebo 0 (vypnuto)"""
    c = cfg.get("casosber") or {}
    if not c.get("zapnuto"):
        return 0
    if c.get("kamery") and cid not in c["kamery"]:
        return 0
    return max(1, min(24, int(c.get("hodin") or 6)))


async def _casosber_seznam(cfg, url, hodin, d):
    """stáhne chybějící snímky archivu ČHMÚ za posledních `hodin` h, vrátí cestu k seznamu pro ffmpeg concat"""
    zaklad, kam = CHMI_RE.match(url.split("?")[0]).groups()
    kl = hls.client(cfg)
    r = await kl.get(zaklad + "getWebcamImages.php", params={"cam": kam}, headers=hls.req_headers(cfg, {}))
    r.raise_for_status()
    soubory = sorted(f for f in r.json() if isinstance(f, str) and CAS_SNIMEK.match(f))
    if not soubory:
        raise RuntimeError("časosběr: archiv ČHMÚ je prázdný")
    def cas(f):
        m = CAS_SNIMEK.match(f)
        return time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M"))
    konec = cas(soubory[-1])
    vyber = [f for f in soubory if cas(f) > konec - hodin * 3600]
    sd = os.path.join(KOREN, "snimky.casosber", kam)
    os.makedirs(sd, exist_ok=True)
    for f in os.listdir(sd):                    # starší než vybrané období pryč
        if f not in vyber:
            try:
                os.unlink(os.path.join(sd, f))
            except OSError:
                pass
    sem = asyncio.Semaphore(4)
    async def stahni(f):
        cil = os.path.join(sd, f)
        if os.path.exists(cil):
            return
        async with sem:
            try:
                rr = await kl.get("%sinput_data/kam/data_%s/%s" % (zaklad, kam, f), headers=hls.req_headers(cfg, {}))
                if rr.status_code == 200 and rr.headers.get("content-type", "").startswith("image/") and len(rr.content) > 2000:
                    with open(cil + ".tmp", "wb") as fh:
                        fh.write(rr.content)
                    os.replace(cil + ".tmp", cil)
            except Exception:
                pass                            # chybějící snímek se přeskočí
    await asyncio.gather(*(stahni(f) for f in vyber))
    mam = [f for f in vyber if os.path.exists(os.path.join(sd, f))]
    if len(mam) < 3:
        raise RuntimeError("časosběr: málo snímků (%d)" % len(mam))
    sez = os.path.join(d, "snimky.txt")
    with open(sez, "w") as fh:
        for i, f in enumerate(mam):
            fh.write("file '%s'\nduration %s\n" % (os.path.join(sd, f), CAS_DRZET if i == len(mam) - 1 else "%.4f" % (1 / CAS_FPS)))
        fh.write("file '%s'\n" % os.path.join(sd, mam[-1]))   # concat: poslední „duration“ platí jen s opakovaným souborem
    return sez


def je_chmi(url):
    return bool(CHMI_RE.match(url.split("?")[0]))


def _sber_dir(cid):
    return os.path.join(SBER, cid) if SBER and re.fullmatch(r"[a-z0-9_-]+", cid) else None


def sber_uloz(cid, data, hodin):
    """uloží sebraný snímek (YYYYMMDD.HHMM.jpg); stejný jako poslední se neukládá; starší než `hodin` h smaže"""
    d = _sber_dir(cid)
    if not d or not data or len(data) < 2000:
        return False
    os.makedirs(d, exist_ok=True)
    mam = sorted(f for f in os.listdir(d) if CAS_SNIMEK.match(f))
    ted = time.time()
    for f in mam:
        m = CAS_SNIMEK.match(f)
        if time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")) < ted - hodin * 3600 - 300:
            try:
                os.unlink(os.path.join(d, f))
            except OSError:
                pass
    mam = sorted(f for f in os.listdir(d) if CAS_SNIMEK.match(f))
    h = hashlib.md5(data).hexdigest()
    if mam:
        posl = mam[-1]
        m = CAS_SNIMEK.match(posl)
        if ted - time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")) < SBER_KROK - 60:
            return False                        # nedávno uloženo
        try:
            if hashlib.md5(open(os.path.join(d, posl), "rb").read()).hexdigest() == h:
                return False                    # kamera se neobnovila
        except OSError:
            pass
    cil = os.path.join(d, time.strftime("%Y%m%d.%H%M", time.localtime(ted)) + ".jpg")
    with open(cil + ".tmp", "wb") as fh:
        fh.write(data)
    os.replace(cil + ".tmp", cil)
    return True


def _sber_seznam(cid, d):
    sd = _sber_dir(cid)
    mam = sorted(f for f in os.listdir(sd) if CAS_SNIMEK.match(f)) if sd and os.path.isdir(sd) else []
    if len(mam) < 3:
        raise RuntimeError("časosběr: zatím málo sebraných snímků (%d)" % len(mam))
    sez = os.path.join(d, "snimky.txt")
    with open(sez, "w") as fh:
        for i, f in enumerate(mam):
            fh.write("file '%s'\nduration %s\n" % (os.path.join(sd, f), CAS_DRZET if i == len(mam) - 1 else "%.4f" % (1 / CAS_FPS)))
        fh.write("file '%s'\n" % os.path.join(sd, mam[-1]))
    return sez


def _sledovane_soubor():
    return os.path.join(SBER, "sledovane.json") if SBER else None


def sledovane():
    """{kamera: čas posledního přehrání} za posledních SLEDOVANE_DNI dní"""
    try:
        d = json.load(open(_sledovane_soubor()))
    except (OSError, ValueError, TypeError):
        return {}
    hranice = time.time() - SLEDOVANE_DNI * 86400
    return {k: v for k, v in d.items() if isinstance(v, (int, float)) and v > hranice}


def zaznamenej_sledovani(cid):
    if not SBER:
        return
    d = sledovane()
    if time.time() - d.get(cid, 0) < 3600:
        return
    d[cid] = time.time()
    os.makedirs(SBER, exist_ok=True)
    p = _sledovane_soubor()
    with open(p + ".tmp", "w") as fh:
        json.dump(d, fh)
    os.replace(p + ".tmp", p)


async def priprav(cfg, cid, url, headers=None, snimek=False):
    hodin = casosber_hodin(cfg, cid, url) if snimek else 0
    chmi = bool(hodin) and je_chmi(url)
    if snimek:
        zaznamenej_sledovani(cid)
    st = _stav.get(cid)
    if st and time.time() - st["t"] < st.get("obnova", OBNOVA):
        return st
    async with _zamek(cid):
        st = _stav.get(cid)
        if st and time.time() - st["t"] < st.get("obnova", OBNOVA):
            return st
        ver = str(int(time.time()))
        d = os.path.join(KOREN, cid, ver)
        os.makedirs(d, exist_ok=True)
        klip = os.path.join(d, "klip.mp4")
        if chmi:
            try:
                sez = await _casosber_seznam(cfg, url, hodin, d)
            except Exception as ex:
                log.warning("časosběr %s: %s – použije se snímek", cid, ex)
                hodin = 0
        zakl = {} if snimek else {"Referer": "https://webtvfc.feratel.com/"}
        if not chmi or not hodin:
            async with hls.client(cfg).stream("GET", url, headers=hls.req_headers(cfg, dict(zakl, **(headers or {})))) as r:
                r.raise_for_status()
                with open(klip, "wb") as f:
                    async for ch in r.aiter_bytes(262144):
                        f.write(ch)
        if hodin and not chmi:                  # sebrané snímky (CASOSBER_SBER_V1)
            try:
                sber_uloz(cid, open(klip, "rb").read(), hodin)
                sez = _sber_seznam(cid, d)
            except Exception as ex:
                log.info("časosběr %s: %s – použije se snímek", cid, ex)
                hodin = 0
        if hodin:      # časosběr: CAS_FPS snímků/s, poslední drží CAS_DRZET s; klíčový snímek po 2 s
            vstup = ["-f", "concat", "-safe", "0", "-i", sez,
                     "-vf", "scale='min(1920,iw)':-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                     "-fps_mode", "cfr", "-r", str(CAS_FPS), "-g", str(2 * CAS_FPS), "-an"]
        elif snimek:     # stojící obraz: 20 s, 2 snímky/s, klíčový snímek každé 2 s; rozměry sudé, nejvýš 1920 px na šířku
            vstup = ["-loop", "1", "-framerate", "2", "-i", klip, "-t", "20",
                     "-vf", "scale='min(1920,iw)':-2:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
                     "-tune", "stillimage", "-r", "2", "-g", "4", "-an"]
        else:
            vstup = ["-i", klip, "-c", "copy"]
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-v", "error", *vstup, "-f", "hls", "-hls_time", "4", "-hls_playlist_type", "vod",
            "-hls_segment_filename", os.path.join(d, "s%03d.ts"), os.path.join(d, "index.m3u8"),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, err = await asyncio.wait_for(p.communicate(), 240 if hodin else 90)
        except asyncio.TimeoutError:
            p.kill()
            raise RuntimeError("smyčka: ffmpeg timeout")
        if os.path.exists(klip):
            os.unlink(klip)
        if not os.path.exists(os.path.join(d, "index.m3u8")):
            raise RuntimeError("smyčka: ffmpeg selhal " + err.decode(errors="replace")[-200:])
        segs, dur = [], None
        for l in open(os.path.join(d, "index.m3u8")).read().splitlines():
            m = re.match(r"#EXTINF:([\d.]+)", l)
            if m:
                dur = float(m.group(1))
            elif l.endswith(".ts") and dur:
                segs.append((l, dur))
                dur = None
        if not segs:
            raise RuntimeError("smyčka: z klipu nevznikly segmenty " + err.decode(errors="replace")[-200:])
        st = {"ver": ver, "segs": segs, "t": time.time(), "avg": sum(s[1] for s in segs) / len(segs),
              "max": max(s[1] for s in segs), "obnova": OBNOVA if hodin else OBNOVA_SNIMEK if snimek else OBNOVA}
        _stav[cid] = st
        # starší verze pryč (poslední 2 zůstávají – přehrávač může dohrávat předchozí)
        verze = sorted(os.listdir(os.path.join(KOREN, cid)))
        for v in verze[:-2]:
            shutil.rmtree(os.path.join(KOREN, cid, v), ignore_errors=True)
        return st


def playlist(cfg, cid, st, key, okno=5):
    segs, n = st["segs"], len(st["segs"])
    k = int(time.time() // st["avg"])
    od = k - okno + 1
    out = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:%d" % math.ceil(st["max"]),
           "#EXT-X-MEDIA-SEQUENCE:%d" % od, "#EXT-X-DISCONTINUITY-SEQUENCE:%d" % (od // n)]
    for j in range(od, k + 1):
        i = j % n
        if i == 0 and j != od:
            out.append("#EXT-X-DISCONTINUITY")
        out.append("#EXTINF:%.3f," % segs[i][1])
        out.append("%s/smycka/%s/%s/%s?k=%s" % (store.base(cfg), cid, st["ver"], segs[i][0], key))
    return "\n".join(out) + "\n"


def soubor(cid, ver, nazev):
    if not (re.fullmatch(r"[a-z0-9_-]+", cid) and ver.isdigit() and re.fullmatch(r"s\d{3}\.ts", nazev)):
        return None
    p = os.path.join(KOREN, cid, ver, nazev)
    return p if os.path.exists(p) else None
