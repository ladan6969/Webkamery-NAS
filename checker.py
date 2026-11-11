"""checker.py – denní kontrola kamer (spouští ji cron přes POST /admin/api/check, nebo ručně z administrace).

Kontrola běží uvnitř služby (jediný zapisovatel state.json a sdílená cache). Kamery postupně, s pauzou mezi nimi
(šetrnost ke zdrojům): resolve → playlist živý (bez #EXT-X-ENDLIST, MEDIA-SEQUENCE se posune) → ffprobe →
náhled ffmpeg -frames:v 1. Stav ok / pomalá / mrtvá; po `dead_after` neúspěšných dnech v řadě se kamera skryje
z playlistu, bouquetu a EPG (ze cams.json se nemaže), po prvním úspěchu se vrátí.
"""
import asyncio
import json
import logging
import os
import time
from urllib.parse import urljoin, urlparse

import cache
import hls
import resolver
import store

log = logging.getLogger("webcams")
running = {"active": False, "done": 0, "total": 0, "started": None, "zastavit": False}
ZAPIS_KAZDYCH = 60                 # s: jak často kontrola ukládá průběžné výsledky do state.json
NAS_MAX_ZATEZ = 3.0                # NAS: nad touto zátěží (1 min) kontrola počká s další kamerou
NAS_MIN_RAM_MB = 100               # NAS: pod tolika MB dostupné paměti kontrola počká
NAS_CEKANI_KAMERA = 30             # s: nejdelší čekání před jednou kamerou
NAS_CEKANI_CELKEM = 4 * 3600       # s: celkový rozpočet čekání za jednu kontrolu, ať nestihne doběhnout další den
# identifikace přehrávačů – některé zdroje (např. WhatsUpCams) VLC / Kodi odmítnou (403), prohlížeč pustí → kamera pojede přes proxy
PLAYER_UAS = ["VLC/3.0.21 LibVLC/3.0.21", "Kodi/21.1 (Windows NT 10.0; Win64; x64) App_Bitness/64 Version/21.1-(21.1.0)-Git:20240804-4ed79fdbc3"]


async def odmita_prehravac(cfg, url):
    """True = zdroj odmítne přehrávač (401/403) → nutná proxy"""
    for ua in PLAYER_UAS:
        try:
            async with hls.client(cfg).stream("GET", url, headers={"User-Agent": ua}) as r:
                if r.status_code in (401, 403):
                    return True
        except Exception:
            pass
    return False


def _ff_headers(cfg, headers):
    h = dict(headers or {})
    ua = h.pop("User-Agent", None) or cfg["user_agent"]
    args = ["-user_agent", ua]
    if h:
        args += ["-headers", "".join("%s: %s\r\n" % kv for kv in h.items())]
    return args


async def _run(args, timeout):
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return None, "timeout"
    return (out if p.returncode == 0 else None), err.decode(errors="replace")[-300:]


async def ffprobe(cfg, url, headers):
    out, err = await _run(["ffprobe", "-v", "error", "-rw_timeout", "15000000"] + _ff_headers(cfg, headers) +
                          ["-show_entries", "stream=codec_type,codec_name,width,height,avg_frame_rate", "-of", "json", url], 40)
    if not out:
        return None
    v = next((s for s in json.loads(out).get("streams", []) if s.get("codec_type") == "video"), None)
    if not v:
        return None
    fps = v.get("avg_frame_rate") or "0/1"
    try:
        n, d = (int(x) for x in fps.split("/"))
        fps = round(n / d, 2) if d else 0
    except ValueError:
        fps = 0
    return {"width": v.get("width"), "height": v.get("height"), "fps": fps, "codec": v.get("codec_name")}


async def thumbnail(cfg, url, headers, dest):
    tmp = dest + ".tmp.jpg"
    out, err = await _run(["ffmpeg", "-y", "-v", "error", "-rw_timeout", "15000000"] + _ff_headers(cfg, headers) +
                          ["-i", url, "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "4", tmp], 45)
    if out is not None and os.path.exists(tmp) and os.path.getsize(tmp) > 1000:
        os.replace(tmp, dest)
        return True
    if os.path.exists(tmp):
        os.unlink(tmp)
    return False


async def _stahni_nahled(cfg, url, dest, headers=None):
    try:
        r = await hls.client(cfg).get(url, headers=hls.req_headers(cfg, headers))
        if r.status_code == 200 and len(r.content) > 2000 and r.headers.get("content-type", "").startswith("image"):
            with open(dest + ".tmp", "wb") as f:
                f.write(r.content)
            os.replace(dest + ".tmp", dest)
            return True
    except Exception:
        pass
    return False


async def lehka(cfg, cam, thumb_dest=None, rozliseni=False):
    """Lehká denní kontrola (3. 11. 2026) – tisíce kamer bez zátěže zdrojů:
    ytkanal = přenos je ve výpisu živých přenosů kanálu (výpis 1× za 10 min na kanál) + náhled z i.ytimg.com;
    skyline = token ze stránky + playlist má segmenty a není ukončený; náhled ffmpegem jen když chybí / je starší 7 dní;
    rozliseni=True (10. 11. 2026): u skyline i rozlišení – z masteru (RESOLUTION) nebo ffprobe posledního segmentu (~50 kB)."""
    t0 = time.time()
    r = {"ok": False, "status": "mrtvá", "error": None, "type": cam.get("type"), "resolution": None, "fps": None, "codec": None,
         "seconds": None, "thumb": False, "url": None, "lehka": True}
    try:
        if cam["type"] == "ytkanal":
            zive = await resolver.zive_kanalu(cfg, cam["source"])
            v = [x for x in zive if resolver._shoda(cam.get("match") or "", x[1])]
            if not v:
                raise resolver.ResolveError("na kanálu teď nevysílá přenos „%s“ (živých %d)" % (cam.get("match"), len(zive)))
            if thumb_dest:
                r["thumb"] = await _stahni_nahled(cfg, "https://i.ytimg.com/vi/%s/hqdefault_live.jpg" % v[0][0], thumb_dest)
        else:                                             # skyline
            url, typ = await resolver.resolve(cfg, cam)
            text, base = await hls.fetch_text(cfg, url, cam.get("headers"))
            res = None
            if hls.is_master(text):
                best = hls.best_variant(text)
                res = best[0].get("RESOLUTION") or None
                text, base = await hls.fetch_text(cfg, urljoin(base, best[1]), cam.get("headers"))
            if "#EXT-X-ENDLIST" in text or not hls.last_segment(text):
                raise resolver.ResolveError("playlist bez segmentů / ukončený")
            if rozliseni and res:
                r["resolution"] = res.lower()
            elif rozliseni:
                info = await ffprobe(cfg, urljoin(base, hls.last_segment(text)), cam.get("headers"))
                if info and info["width"] and info["height"]:
                    r.update(resolution="%sx%s" % (info["width"], info["height"]), fps=info["fps"], codec=info["codec"])
            cache.put(cam["id"], url, cam.get("headers"), typ, cfg)
            if thumb_dest and (not os.path.exists(thumb_dest) or time.time() - os.path.getmtime(thumb_dest) > 7 * 86400):
                r["thumb"] = await thumbnail(cfg, url, cam.get("headers"), thumb_dest)
        r.update(ok=True, status="ok", seconds=round(time.time() - t0, 1))
    except Exception as e:
        r["error"] = str(e)[:400] or e.__class__.__name__
    return r


async def probe(cfg, cam, thumb_dest=None, live_wait=None):
    """Úplné ověření jedné kamery (bez zápisu stavu). → dict"""
    t0 = time.time()
    r = {"ok": False, "status": "mrtvá", "error": None, "type": None, "resolution": None, "fps": None, "codec": None,
         "seconds": None, "thumb": False, "url": None}
    try:
        url, typ = await resolver.resolve(cfg, cam)
        r["type"], r["url"] = typ, url
        headers = cam.get("headers") or {}
        if typ in resolver.NE_HLS:
            # smyčka klipu (feratel) / MJPEG: jen ffprobe a náhled, živost playlistu se neověřuje
            if typ == "feratel":
                headers = dict(resolver.FERATEL_H, **headers)
            info = await ffprobe(cfg, url, headers)
            if not info:
                raise resolver.ResolveError("ffprobe nenašel video")
            r.update(resolution="%sx%s" % (info["width"], info["height"]), fps=info["fps"], codec=info["codec"],
                     ok=True, status="ok", seconds=round(time.time() - t0, 1))
            if thumb_dest:
                r["thumb"] = await thumbnail(cfg, url, headers, thumb_dest)
            cache.put(cam["id"], url, headers, typ, cfg)
            return r
        r["force_proxy"] = not headers and await odmita_prehravac(cfg, url)
        text, base = await hls.fetch_text(cfg, url, headers)
        media = base
        if hls.is_master(text):
            best = hls.best_variant(text)
            media = urljoin(base, best[1])
            text, media = await hls.fetch_text(cfg, media, headers)
        start = time.time() - t0
        if "#EXT-X-ENDLIST" in text:
            raise resolver.ResolveError("playlist má #EXT-X-ENDLIST – není živý")
        seq1, last1 = hls.media_sequence(text), hls.last_segment(text)
        info = await ffprobe(cfg, media, headers)
        if not info:
            raise resolver.ResolveError("ffprobe nenašel video")
        r.update(resolution="%sx%s" % (info["width"], info["height"]), fps=info["fps"], codec=info["codec"])
        if thumb_dest:
            r["thumb"] = await thumbnail(cfg, media, headers, thumb_dest)
        wait = cfg["check_live_wait"] if live_wait is None else live_wait
        if wait:
            await asyncio.sleep(max(0, wait - (time.time() - t0 - start)))
            text2, _ = await hls.fetch_text(cfg, media, headers)
            seq2, last2 = hls.media_sequence(text2), hls.last_segment(text2)
            if (seq1 is not None and seq2 is not None and seq2 <= seq1) and last1 == last2:
                raise resolver.ResolveError("playlist se neposouvá (MEDIA-SEQUENCE %s)" % seq1)
        r["ok"] = True
        r["status"] = "pomalá" if start > cfg["slow_threshold"] else "ok"
        r["seconds"] = round(start, 1)
        cache.put(cam["id"], url, headers, typ, cfg)       # čerstvý odkaz rovnou do cache
    except Exception as e:
        r["error"] = str(e)[:400] or e.__class__.__name__
    return r


def visible(st_cam, cfg):
    """kamera v playlistu / bouquetu / EPG: zapnutá a ne „skrytá“ po N neúspěšných dnech;
    kamera, která ještě nikdy nehrála (nová, provozovatel ji teprve chystá), se skryje už po první neúspěšné kontrole (7. 11. 2026)"""
    st_cam = st_cam or {}
    if not st_cam.get("last_ok") and st_cam.get("fail_streak", 0) >= 1:
        return False
    return st_cam.get("fail_streak", 0) < cfg["dead_after"]


def _rozliseni_stare(st_cam, dni=7):
    """lehká kontrola zjišťuje rozlišení jen když chybí nebo je starší než týden (10. 11. 2026)"""
    try:
        kdy = time.mktime(time.strptime((st_cam or {}).get("resolution_kdy") or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return True
    return time.time() - kdy > dni * 86400


def _jen_tydne(st_cam, cfg):
    """nevysílající kamera: první týden se zkouší každou noc, pak jednou týdně (7. 11. 2026) – True = dnes přeskočit"""
    st_cam = st_cam or {}
    po = int(cfg.get("dead_weekly_after", 7))
    if not po or visible(st_cam, cfg) or st_cam.get("fail_streak", 0) < po:
        return False
    try:
        posledni = time.mktime(time.strptime(st_cam.get("last_check") or "", "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return False
    return time.time() - posledni < 6.5 * 86400


def _dostupna_ram_mb():
    """MemAvailable, na starších jádrech MemFree + Buffers + Cached; None = nelze zjistit"""
    try:
        hodnoty = {}
        with open("/proc/meminfo", encoding="ascii") as f:
            for radek in f:
                klic, _, zbytek = radek.partition(":")
                hodnoty[klic] = int(zbytek.split()[0])
        kb = hodnoty.get("MemAvailable")
        if kb is None:
            kb = hodnoty["MemFree"] + hodnoty.get("Buffers", 0) + hodnoty.get("Cached", 0)
        return kb / 1024
    except (OSError, KeyError, ValueError, IndexError):
        return None


def _nas_vytizeny():
    ram = _dostupna_ram_mb()
    return os.getloadavg()[0] > NAS_MAX_ZATEZ or (ram is not None and ram < NAS_MIN_RAM_MB)


async def _pockej_na_vykon(dobrzdeno):
    """NAS: když je vytížený, kontrola ustoupí, aby DSM a přehrávání zůstaly dostupné (8. 11. 2026)."""
    cekal = 0
    while cekal < NAS_CEKANI_KAMERA and dobrzdeno[0] < NAS_CEKANI_CELKEM and not running["zastavit"] and _nas_vytizeny():
        await asyncio.sleep(5)
        cekal += 5
        dobrzdeno[0] += 5


async def check_all(cfg, only=None):
    """Kontrola všech (nebo jedné) zapnutých kamer. → souhrn"""
    if running["active"]:
        return {"error": "kontrola už běží"}
    cams = [c for c in store.cams() if (only is None and c["enabled"]) or c["id"] == only]
    if only is None:
        st = store.state()["cams"]                 # jednou – ne pro každou kameru (7. 11. 2026: 7 000× načtení zablokovalo službu)
        cams = [c for c in cams if not _jen_tydne(st.get(c["id"]), cfg)]
    cams.sort(key=lambda c: (c.get("type") != "ytkanal", c.get("source")))      # kamery jednoho kanálu za sebou → jeden výpis kanálu
    running.update(active=True, done=0, total=len(cams), started=time.time(), zastavit=False)
    zmeny = {"nove_mrtve": [], "ozivene": []}
    thumbs = os.path.join(store.BASE, cfg["thumbs_dir"])
    os.makedirs(thumbs, exist_ok=True)
    fronta = list(cams)
    # Výsledky se zapisují do state.json najednou (8. 11. 2026): celý soubor (přes 2 MB) po každé kameře blokoval NAS.
    # Do souboru jdou jen změněná pole, ať se nepřepíše to, co mezitím uložila jiná část služby.
    zaklad = store.state()["cams"]
    cekajici = {}
    zapsano = time.time()
    dobrzdeno = [0.0]

    def zapsat():
        nonlocal zaklad, zapsano
        zapsano = time.time()
        if not cekajici:
            return
        s = store.state()
        for cid, zmena in cekajici.items():
            s["cams"].setdefault(cid, {}).update(zmena)
            store.sid_for(s, cid)
        store.save_state(s)
        cekajici.clear()
        zaklad = s["cams"]

    async def pracovnik(cislo):
        prvni = True
        while fronta and not running["zastavit"]:
            c = fronta.pop(0)
            if not prvni:
                await asyncio.sleep(cfg["check_pause"])
            prvni = False
            if only is None and cfg.get("vlastni_nas"):
                await _pockej_na_vykon(dobrzdeno)
            if running["zastavit"]:
                break
            lehce = only is None and c.get("type") in ("ytkanal", "skyline")
            prev = zaklad.get(c["id"], {})
            if lehce:
                r = await lehka(cfg, c, os.path.join(thumbs, c["id"] + ".jpg"),
                                rozliseni=c.get("type") == "skyline" and _rozliseni_stare(prev))
            else:
                r = await probe(cfg, c, os.path.join(thumbs, c["id"] + ".jpg"))
            was_visible = visible(prev, cfg)
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            x = dict(prev)
            x.update(status=r["status"], last_check=now, error=r["error"], seconds=r["seconds"])
            if r["ok"] and r.get("lehka"):                # lehká kontrola: rozlišení jen občas (skyline) – jinak nechat poslední známé
                x.update(last_ok=now, fail_streak=0, fail_den=None, type_used=r["type"])
                if r.get("resolution"):
                    x.update(resolution=r["resolution"], fps=r["fps"], codec=r["codec"], resolution_kdy=now)
            elif r["ok"]:
                x.update(last_ok=now, fail_streak=0, fail_den=None, type_used=r["type"], resolution=r["resolution"], fps=r["fps"], codec=r["codec"],
                         force_proxy=bool(r.get("force_proxy")))
                if not was_visible:
                    zmeny["ozivene"].append(c["name"])
            else:
                # „po 3 DNECH“: počítadlo roste jen jednou za den (víc kontrol v jeden den = jeden neúspěšný den)
                dnes = now[:10]
                if prev.get("fail_den") != dnes:
                    x["fail_streak"] = prev.get("fail_streak", 0) + 1
                    x["fail_den"] = dnes
                if was_visible and not visible(x, cfg):
                    zmeny["nove_mrtve"].append(c["name"])
            cekajici[c["id"]] = {k: v for k, v in x.items() if k not in prev or prev[k] != v}
            if only is not None or time.time() - zapsano >= ZAPIS_KAZDYCH:
                zapsat()
            running["done"] += 1
            log.info("kontrola %s: %s %s", c["id"], r["status"], r["error"] or r["resolution"])

    try:
        n = max(1, min(int(cfg.get("check_parallel", 3)), len(cams) or 1))
        await asyncio.gather(*(pracovnik(i) for i in range(n)))
        zapsat()
        if only is None and not running["zastavit"]:      # zastavená kontrola se jako dokončená nepočítá
            s = store.state()
            s["last_check"] = time.strftime("%Y-%m-%d %H:%M:%S")
            store.save_state(s)
    finally:
        zapsat()                                   # i po chybě nebo přerušení: hotové výsledky neztratit
        running["active"] = False
    if only is None and (zmeny["nove_mrtve"] or zmeny["ozivene"]):
        await telegram(cfg, zmeny)
    return {"zkontrolovano": len(cams), **zmeny}


async def telegram(cfg, zmeny):
    tg = cfg["telegram"]
    if not tg.get("token") or not tg.get("chat_id"):
        return
    txt = "📷 Webkamery – kontrola"
    if zmeny["nove_mrtve"]:
        txt += "\n❌ skryté (%d dny nejdou): %s" % (cfg["dead_after"], ", ".join(zmeny["nove_mrtve"]))
    if zmeny["ozivene"]:
        txt += "\n✅ znovu jdou: " + ", ".join(zmeny["ozivene"])
    try:
        await hls.client(cfg).post("https://api.telegram.org/bot%s/sendMessage" % tg["token"],
                                   json={"chat_id": tg["chat_id"], "text": txt})
    except Exception as e:
        log.warning("telegram: %s", e)


def domena(url):
    try:
        return urlparse(url).hostname or ""
    except Exception:
        return ""
