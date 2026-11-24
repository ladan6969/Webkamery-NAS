"""Webcams proxy – stálé adresy živých webkamer (/cam/<id>), stream URL se zjišťuje až při přehrání.

Spuštění:  venv/bin/python main.py                (systemd webcams.service)
Nastavení: venv/bin/python main.py --init         (vytvoří config.json: klíč pro přehrávače, tajemství, cron token)
           venv/bin/python main.py --heslo        (heslo do administrace)
Služba poslouchá jen lokálně; ven ji pouští nginx (např. https://example.com/webcams/ → 127.0.0.1:8093).
"""
import asyncio
import base64
import getpass
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import subprocess
import sys
import time

from urllib.parse import quote, urljoin

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

import cache
import checker
import hls
import playlist
import resolver
import rtsp
import smycka
import yt4k
import store
import zeme

VERZE = "1.0.0"
try:                                            # verze nasazeného kódu pro OTA (tools/aktualizuj_kod.py); na VPS soubor není
    KOD_VERZE = open(os.path.join(store.BASE, "VERZE"), encoding="utf-8").read().strip()
except OSError:
    KOD_VERZE = None
MPEGURL = "application/vnd.apple.mpegurl"
os.makedirs(store.path("logs"), exist_ok=True)
log = logging.getLogger("webcams")
log.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(store.path("logs/webcams.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)

app = FastAPI(title="Webcams proxy", docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def cors_pro_prehravac(request: Request, call_next):
    """přehrávač v administraci (hls.js) stahuje /cam, /proxy a /smycka – i když je administrace otevřená přes jinou adresu
    než public_base (NAS přes Tailscale). Bez klíče ?k= tyto adresy stejně nic nevydají."""
    tok = store.nastav_base(store.base_z_hostu(request.headers.get("host")))
    try:
        r = await call_next(request)
    finally:
        store._base_pozadavku.reset(tok)
    if request.url.path.startswith(("/cam/", "/cam4k/", "/proxy/", "/smycka/")):
        r.headers["Access-Control-Allow-Origin"] = "*"
    return r
CFG = store.config()


def cfg():
    return CFG


# ── přístup ─────────────────────────────────────────────────────────────────────
def key_ok(request):
    k = request.query_params.get("k") or request.headers.get("x-webcams-key") or ""
    return k if k and any(hmac.compare_digest(k, x) for x in CFG["access_keys"]) else None


def need_key(request):
    k = key_ok(request) or (admin_ok(request) and (CFG["access_keys"] or [""])[0])
    if not k:
        raise HTTPException(403, "Chybí nebo neplatí přístupový klíč (?k=…)")
    return k


def _podpis(data):
    return hmac.new(CFG["secret"].encode(), data.encode(), hashlib.sha256).hexdigest()


def admin_ok(request):
    c = request.cookies.get("wc_admin", "")
    try:
        exp, sig = c.split(".", 1)
        return int(exp) > time.time() and hmac.compare_digest(sig, _podpis("admin|" + exp))
    except ValueError:
        return False


def need_admin(request):
    if admin_ok(request):
        return
    t = request.headers.get("x-webcams-token", "")
    if CFG["cron_token"] and t and hmac.compare_digest(t, CFG["cron_token"]):
        return
    raise HTTPException(401, "Nepřihlášeno")


def hash_hesla(heslo, salt=None, it=200_000):
    salt = salt or secrets.token_hex(8)
    return "pbkdf2_sha256$%d$%s$%s" % (it, salt, hashlib.pbkdf2_hmac("sha256", heslo.encode(), salt.encode(), it).hex())


def heslo_ok(heslo):
    try:
        _, it, salt, h = CFG["admin_password_hash"].split("$")
        return hmac.compare_digest(hash_hesla(heslo, salt, int(it)), CFG["admin_password_hash"])
    except ValueError:
        return False


# ── stream URL z cache / resolveru ──────────────────────────────────────────────
async def aktualni(cam):
    cid = cam["id"]
    e = cache.get(cid)
    if e and cache.needs_revalidate(e, CFG):
        try:
            async with hls.client(CFG).stream("GET", e["url"], headers=hls.req_headers(CFG, e["headers"])) as r:
                if r.status_code in (401, 403, 404, 410):
                    log.info("revalidace %s: HTTP %s → znovu", cid, r.status_code)
                    cache.drop(cid)
                    e = None
                else:
                    cache.mark_verified(cid)
        except Exception as ex:          # síť / timeout → cache ponechat
            log.info("revalidace %s: %s – cache ponechána", cid, ex.__class__.__name__)
    if e:
        return e
    async with cache.lock(cid):
        e = cache.get(cid)              # mezitím mohl resolvovat souběžný požadavek
        if e:
            return e
        url, typ = await resolver.resolve(CFG, cam)
        e = cache.put(cid, url, cam.get("headers"), typ, CFG)
        if (cam.get("type") or "auto") == "auto":
            s = store.state()
            if s["cams"].get(cid, {}).get("type_used") != typ:
                s["cams"].setdefault(cid, {})["type_used"] = typ
                store.save_state(s)
        return e


def najdi(cid):
    cam = store.cam(cid)
    if not cam:
        raise HTTPException(404, "Kamera %s neexistuje" % cid)
    return cam


_stopy = {}          # id kamery → (platí do, kodek obrazu nebo None) – kamery bez zvuku


async def _kodek_bez_zvuku(cam, e, text, base):
    """kodek obrazu, když stream nemá zvuk (MPEG-TS); jinak None. Výsledek platí 6 h, při neúspěchu 5 min."""
    cid = cam["id"]
    z = _stopy.get(cid)
    if z and z[0] > time.time():
        return z[1]
    seg = hls.last_segment(text)
    if not seg or "#EXT-X-MAP" in text:
        return None
    s = await hls.stopy_segmentu(CFG, urljoin(base or e["url"], seg), cam.get("headers"))
    kodek = s["kodek"] if s and not s["zvuk"] else None
    _stopy[cid] = (time.time() + (21600 if s else 300), kodek)
    if kodek:
        log.info("cam %s: bez zvuku, kodek %s", cid, kodek)
    return kodek


PREDNACITAT_MAX = 40
PREDNACITAT_REZERVA = 1800      # s: obnovit, když adrese zbývá méně


def _z_youtube(c):
    s = c.get("source") or ""
    return c.get("type") in ("ytdlp", "ytkanal") or "youtube." in s or "youtu.be" in s


async def _prednacti_oblibene():
    """NAS: adresy oblíbených kamer z YouTube drží čerstvé, aby se v Kodi spustily hned (limit Kodi je asi 20 s, yt-dlp na NASu trvá déle)."""
    await asyncio.sleep(180)
    while True:
        try:
            for c in [x for x in store.cams() if x.get("oblibena") and x.get("enabled", True) and _z_youtube(x)][:PREDNACITAT_MAX]:
                if checker.running["active"] or checker._nas_vytizeny():
                    break
                z = cache.get(c["id"])
                if z and z["expires"] - time.time() > PREDNACITAT_REZERVA:
                    continue
                try:
                    await aktualni(c)
                    log.info("přednačteno: %s", c["id"])
                except Exception as ex:
                    log.info("přednačtení %s: %s", c["id"], ex)
                await asyncio.sleep(20)     # přehrávání má přednost před dalším načítáním
        except Exception as ex:
            log.warning("přednačítání: %s", ex)
        await asyncio.sleep(600)


SBER_MAX = 200                  # nejvýš tolik kamer se sbírá (CASOSBER_SBER_V1)


def _sber_kamery():
    """snímkové kamery bez archivu ČHMÚ, které jsou oblíbené nebo se sledovaly za posledních 7 dní"""
    sled = smycka.sledovane()
    out = []
    for c in store.cams():
        if c.get("type") != "snimek" or not c.get("enabled", True) or smycka.je_chmi(c.get("source") or ""):
            continue
        if c.get("oblibena") or c["id"] in sled:
            out.append((0 if c.get("oblibena") else 1, -sled.get(c["id"], 0), c))
    return [x[2] for x in sorted(out, key=lambda x: x[:2])][:SBER_MAX]


async def _sber_casosberu():
    """po 10 min stáhne aktuální snímek vybraných kamer, z nich se pak skládá časosběr (smycka.priprav)"""
    await asyncio.sleep(60)
    while True:
        zacatek = time.time()
        ulozeno = 0
        try:
            kamery = _sber_kamery()
            sem = asyncio.Semaphore(4)

            async def jedna(c):
                nonlocal ulozeno
                hodin = smycka.casosber_hodin(CFG, c["id"], c.get("source") or "", c.get("zobrazeni"))
                if not hodin:
                    return
                async with sem:
                    if CFG.get("vlastni_nas") and checker._nas_vytizeny():
                        return
                    try:
                        e = await aktualni(c)
                        if e["kind"] != "snimek" or smycka.je_holidayinfo(e["url"]) or smycka.je_chmi(e["url"]):
                            return                      # holidayinfo a ČHMÚ mají vlastní archiv – nesbírat
                        r = await hls.client(CFG).get(e["url"], headers=hls.req_headers(CFG, c.get("headers") or {}))
                        if r.status_code == 200 and r.headers.get("content-type", "image/").startswith("image/"):
                            if smycka.sber_uloz(c["id"], r.content, hodin):
                                ulozeno += 1
                    except Exception as ex:
                        log.info("časosběr sběr %s: %s", c["id"], ex.__class__.__name__)
            await asyncio.gather(*(jedna(c) for c in kamery))
            if kamery:
                log.info("časosběr: sebráno %d snímků z %d kamer (%.0f s)", ulozeno, len(kamery), time.time() - zacatek)
        except Exception as ex:
            log.warning("časosběr sběr: %s", ex)
        await asyncio.sleep(max(60, smycka.SBER_KROK - (time.time() - zacatek)))


@app.on_event("startup")
async def _start_prednacitani():
    smycka.SBER = store.path("casosber")
    if CFG.get("vlastni_nas"):
        asyncio.create_task(_prednacti_oblibene())
    if (CFG.get("casosber") or {}).get("zapnuto"):
        asyncio.create_task(_sber_casosberu())


FF_BEZI = {"n": 0}


async def _ts_pres_ffmpeg(request, cam, cid, key, zdroj=None):
    """KODI_FFMPEG_V1 (15. 11. 2026): kamera jako jeden MPEG-TS proud – ffmpeg si sám stáhne HLS (přes /cam pro Kodi:
    jen nejvyšší kvalita, YouTube od živého konce) a bez překódování ho přebalí. Kodi pak nepotřebuje InputStream Adaptive."""
    if request.method == "HEAD":
        return Response(b"", media_type="video/mp2t")
    if FF_BEZI["n"] >= int(CFG.get("kodi_ffmpeg_max", 6)):
        return PlainTextResponse("Moc souběžných přehrávání přes ffmpeg", 503)
    if zdroj:      # RTSP_V1: rtsp:// rovnou ffmpegem, obraz beze změny, zvuk IP kamer (G.711…) → AAC
        args = (["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error"] + rtsp.ffmpeg_vstup(zdroj) +
                ["-map", "0:v:0", "-map", "0:a:0?", "-c:v", "copy"] + rtsp.ZVUK + ["-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"])
    else:
        src = "http://127.0.0.1:%d/cam/%s?client=kodi&k=%s" % (int(CFG["port"]), quote(cid), quote(key))
        h = hls.req_headers(CFG, cam.get("headers"))
        args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-user_agent", h.pop("User-Agent", CFG["user_agent"])]
        if h:
            args += ["-headers", "".join("%s: %s\r\n" % kv for kv in h.items())]
        args += ["-fflags", "+genpts+discardcorrupt", "-i", src, "-map", "0:v:0?", "-map", "0:a:0?",
                 "-c", "copy", "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1"]
    p = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    FF_BEZI["n"] += 1

    async def konec():
        FF_BEZI["n"] -= 1
        if p.returncode is None:
            try:
                p.kill()
            except ProcessLookupError:
                pass
        await p.wait()

    try:
        first = await asyncio.wait_for(p.stdout.read(65536), timeout=int(CFG.get("resolve_timeout", 30)) + 15)
    except asyncio.TimeoutError:
        first = b""
    if not first:
        await konec()
        log.warning("cam %s: ffmpeg nic nevrátil", cid)
        return PlainTextResponse("Kamera %s teď nejde načíst (ffmpeg)" % cam["name"], 503)
    log.info("cam %s: ffmpeg TS%s", cid, " z RTSP" if zdroj else " pro Kodi")

    async def proud():
        try:
            yield first
            while True:
                b = await p.stdout.read(65536)
                if not b:
                    break
                yield b
        finally:
            await konec()
    return StreamingResponse(proud(), media_type="video/mp2t", headers={"Cache-Control": "no-store"})


async def _smycka_ts(request, cam, cid, e):
    """KODI_SMYCKA_V1 (15. 11. 2026): feratel klip / snímek / časosběr pro Kodi jako nekonečný MPEG-TS.
    HLS smyčku Kodi po skoku na začátek klipu zastaví (VLC ji hraje dokola) – tady ffmpeg přehraje klip v reálném čase,
    pak znovu, s navazujícím časem (-output_ts_offset); před každým kolem se vezme čerstvější klip (smycka.priprav)."""
    if request.method == "HEAD":
        return Response(b"", media_type="video/mp2t")
    if FF_BEZI["n"] >= int(CFG.get("kodi_ffmpeg_max", 6)):
        return PlainTextResponse("Moc souběžných přehrávání přes ffmpeg", 503)

    async def priprav(e):
        return await smycka.priprav(CFG, cid, e["url"], cam.get("headers"), snimek=e["kind"] == "snimek", rezim=cam.get("zobrazeni"))
    try:
        st = await priprav(e)
    except Exception as ex:
        log.warning("smyčka %s: %s", cid, ex)
        return PlainTextResponse("Kamera %s: klip nejde připravit (%s)" % (cam["name"], ex), 503)
    FF_BEZI["n"] += 1
    log.info("cam %s: smyčka TS pro Kodi", cid)

    async def proud():
        nonlocal st
        posun, kol = 0.0, 0
        try:
            while True:
                idx = os.path.join(smycka.KOREN, cid, st["ver"], "index.m3u8")
                p = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-re", "-i", idx,
                    "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy", "-output_ts_offset", "%.3f" % posun,
                    "-f", "mpegts", "-mpegts_flags", "+resend_headers", "pipe:1",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
                poslano = 0
                try:
                    while True:
                        b = await p.stdout.read(65536)
                        if not b:
                            break
                        poslano += len(b)
                        yield b
                finally:
                    if p.returncode is None:
                        try:
                            p.kill()
                        except ProcessLookupError:
                            pass
                    await p.wait()
                kol = kol + 1 if not poslano else 0
                if kol >= 3:                        # klip se opakovaně nedá přehrát
                    log.warning("smyčka %s: ffmpeg nic nevrátil", cid)
                    return
                posun += sum(s[1] for s in st["segs"])
                try:
                    st = await priprav(await aktualni(cam))
                except Exception as ex:             # zdroj teď nejde – hraje dál poslední klip
                    log.info("smyčka %s: obnova klipu %s", cid, ex)
                    if not os.path.exists(idx):
                        return
        finally:
            FF_BEZI["n"] -= 1
    return StreamingResponse(proud(), media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.api_route("/cam/{cid}", methods=["GET", "HEAD"])
async def cam_stream(cid: str, request: Request, client: str = "kodi", ff: str = ""):
    key = need_key(request)
    cam = najdi(cid)
    stc = store.state()["cams"].get(cid) or {}
    if resolver.typ_odhad(cam) == "rtsp":      # RTSP_V1: web → živé HLS (rtsp.py), ostatní → MPEG-TS
        if client != "web":
            return await _ts_pres_ffmpeg(request, cam, cid, key, zdroj=cam["source"].strip())
        if request.method == "HEAD":
            return Response(b"", media_type=MPEGURL)
        try:
            d = await rtsp.spustit(CFG, cid, cam["source"].strip())
        except Exception as ex:
            log.warning("rtsp %s: %s", cid, ex)
            return PlainTextResponse("Kamera %s teď nejde načíst: %s" % (cam["name"], ex), 503)
        return RedirectResponse("../rtsp/%s/i.m3u8?k=%s" % (quote(cid), quote(key)), 302)
    smy = (stc.get("type_used") or resolver.typ_odhad(cam)) in resolver.KLIP + ("snimek",)
    if ff == "1" and client == "kodi" and not smy and playlist.kodi_ff(CFG, cam, stc):
        return await _ts_pres_ffmpeg(request, cam, cid, key)
    try:
        e = await aktualni(cam)
    except resolver.ResolveError as ex:
        log.warning("cam %s: %s", cid, ex)
        return PlainTextResponse("Kamera %s teď nejde načíst: %s" % (cam["name"], ex), 503)
    if e["kind"] == "mjpeg":                    # MJPEG: přehrávač si ho otevře sám
        return RedirectResponse(e["url"], 302)
    if e["kind"] in resolver.KLIP + ("snimek",) and ff == "1" and client == "kodi" and CFG.get("kodi_ffmpeg"):
        return await _smycka_ts(request, cam, cid, e)
    if e["kind"] in resolver.KLIP + ("snimek",):     # poslední klip / obrázek → nekonečná HLS smyčka
        try:
            st = await smycka.priprav(CFG, cid, e["url"], cam.get("headers"), snimek=e["kind"] == "snimek", rezim=cam.get("zobrazeni"))
        except Exception as ex:
            log.warning("smyčka %s: %s", cid, ex)
            return PlainTextResponse("Kamera %s: klip nejde připravit (%s)" % (cam["name"], ex), 503)
        return Response(smycka.playlist(CFG, cid, st, key), media_type=MPEGURL, headers={"Cache-Control": "no-store"})
    mode = playlist.mode_for(cam, client, store.state()["cams"].get(cid))
    if mode == "redirect" and client not in ("e2", "kodi"):
        return RedirectResponse(e["url"], 302)
    try:
        text, base = await hls.fetch_text(CFG, e["url"], cam.get("headers"))
    except Exception as ex:
        cache.drop(cid)
        log.warning("cam %s: playlist %s", cid, ex)
        return PlainTextResponse("Kamera %s: playlist nejde stáhnout (%s)" % (cam["name"], ex.__class__.__name__), 503)
    if client == "kodi" and not hls.is_master(text):      # stream bez zvuku: Kodi potřebuje vědět, že má jen obraz
        kodek = await _kodek_bez_zvuku(cam, e, text, base)
        if kodek:
            cil = e["url"] if mode == "redirect" else hls.proxy_url(CFG, cid, e["url"], key)
            return Response(hls.master_jen_obraz(cil, kodek), media_type=MPEGURL, headers={"Cache-Control": "no-store"})
    if client == "kodi" and mode == "redirect" and not hls.is_master(text):   # jediná kvalita – není co vybírat, Kodi jde rovnou na zdroj
        return RedirectResponse(e["url"], 302)
    if client in ("e2", "kodi"):                # jen nejvyšší varianta, absolutní URI
        text = hls.reduce_master(text, base) if hls.is_master(text) else hls.absolutize(text, base)
        base = None
    if mode == "proxy":
        text = hls.rewrite_for_proxy(CFG, cid, text, base or e["url"], key)
    elif base:
        text = hls.absolutize(text, base)
    if client in ("e2", "kodi") and mode != "proxy" and hls.is_master(text):
        text = hls.presmeruj_okno(CFG, cid, text, key)     # YouTube: jen živý konec, ne hodina záznamu
    return Response(text, media_type=MPEGURL, headers={"Cache-Control": "no-store"})


@app.api_route("/cam4k/{cid}", methods=["GET", "HEAD"])
async def cam4k(cid: str, request: Request, client: str = "kodi"):
    """YT_4K_V1: kamera z YouTube ve 4K jako fragmentované MP4 (Kodi, přehrávač ve správě). Když 4K nejde
    (není instalace / přenos 4K nemá / YouTube odmítne), Kodi dostane běžnou cestu (přesměrování na /cam), web 503."""
    key = need_key(request)
    cam = najdi(cid)

    def jinak(duvod):
        log.info("4K %s: běžná cesta (%s)", cid, duvod)
        if client == "web":
            return PlainTextResponse("4K teď nejde: " + duvod, 503)
        ff = "&ff=1" if playlist.kodi_ff(CFG, cam, store.state()["cams"].get(cid)) else ""
        return RedirectResponse("%s/cam/%s?client=kodi%s&k=%s" % (store.base(CFG), quote(cid), ff, quote(key)), 302)

    if not cam.get("uhd") or not yt4k.dostupne(CFG):
        return jinak("vypnuto")
    vid = yt4k.video_id(cam)
    if not vid:
        try:
            vid = yt4k.video_id(cam, (await aktualni(cam))["url"])
        except Exception as ex:
            return jinak("adresa: %s" % ex)
    if not vid:
        return jinak("není YouTube")
    if request.method == "HEAD":
        return Response(media_type="video/mp4", headers={"Cache-Control": "no-store"})
    p = await yt4k.proud(CFG, cid, vid)
    if not p:
        return jinak("stahovač nedal obraz")
    return StreamingResponse(p.divak(), media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.api_route("/oriz/{cid}", methods=["GET", "HEAD"])
async def oriz(cid: str, request: Request, u: str = "", s: str = ""):
    """media playlist s hodinovým záznamem (YouTube) → jen posledních ~30 s; segmenty jdou dál přímo ke zdroji"""
    need_key(request)
    cam = najdi(cid)
    target = hls.proxy_target(CFG, cid, u, s)
    if not target:
        raise HTTPException(403, "Neplatný odkaz")
    try:
        text, base = await hls.fetch_text(CFG, target, cam.get("headers"))
    except Exception as ex:
        cache.drop(cid)
        return PlainTextResponse("Zdroj nejde stáhnout (%s)" % ex.__class__.__name__, 502)
    return Response(hls.oriz_media(text, base), media_type=MPEGURL, headers={"Cache-Control": "no-store"})


@app.api_route("/proxy/{cid}", methods=["GET", "HEAD"])
async def proxy(cid: str, request: Request, u: str = "", s: str = ""):
    key = need_key(request)
    cam = najdi(cid)
    target = hls.proxy_target(CFG, cid, u, s)
    if not target:
        raise HTTPException(403, "Neplatný odkaz")
    cl = hls.client(CFG)
    req = cl.build_request("GET", target, headers=hls.req_headers(CFG, cam.get("headers")))
    r = await cl.send(req, stream=True)
    if r.status_code >= 400:
        await r.aclose()
        return PlainTextResponse("Zdroj vrátil HTTP %s" % r.status_code, r.status_code)
    it = r.aiter_bytes()
    try:
        first = await it.__anext__()
    except StopAsyncIteration:
        first = b""
    ct = r.headers.get("content-type", "")
    if first.lstrip().startswith(b"#EXTM3U") or "mpegurl" in ct.lower():
        body = first + b"".join([c async for c in it])
        await r.aclose()
        text = hls.rewrite_for_proxy(CFG, cid, body.decode("utf-8", "replace"), str(r.url), key)
        return Response(text, media_type=MPEGURL, headers={"Cache-Control": "no-store"})

    async def proud():
        try:
            yield first
            async for c in it:
                yield c
        finally:
            await r.aclose()
    hl = {k: v for k, v in r.headers.items() if k.lower() in ("content-length", "cache-control")}
    return StreamingResponse(proud(), media_type=ct or "application/octet-stream", headers=hl)


@app.api_route("/rtsp/{cid}/{name}", methods=["GET", "HEAD"])
async def rtsp_hls(cid: str, name: str, request: Request):
    """RTSP_V1: živé HLS z RTSP pro přehrávač ve správě (playlist a segmenty z dočasné složky)"""
    key = need_key(request)
    cam = najdi(cid)
    if name == "i.m3u8":
        try:
            d = await rtsp.spustit(CFG, cid, cam["source"].strip())
        except Exception as ex:
            return PlainTextResponse("Kamera %s teď nejde načíst: %s" % (cam["name"], ex), 503)
        return Response(rtsp.playlist(cid, d, key), media_type=MPEGURL, headers={"Cache-Control": "no-store"})
    p = rtsp.segment(cid, name)
    if not p:
        raise HTTPException(404, "Segment není")
    return FileResponse(p, media_type="video/mp2t", headers={"Cache-Control": "no-store"})


@app.api_route("/smycka/{cid}/{ver}/{name}", methods=["GET", "HEAD"])
async def smycka_segment(cid: str, ver: str, name: str, request: Request):
    need_key(request)
    p = smycka.soubor(cid, ver, name)
    if not p:
        raise HTTPException(404, "segment není (klip se mezitím obnovil)")
    return FileResponse(p, media_type="video/mp2t")


@app.api_route("/playlist.m3u", methods=["GET", "HEAD"])
async def m3u(request: Request, client: str = "kodi", podle: str = "", cz: str = ""):
    key = need_key(request)
    if client not in ("kodi", "e2", "vlc"):
        client = "kodi"
    return PlainTextResponse(playlist.m3u(CFG, client, key, _podle(podle), _cz(cz)), media_type="audio/x-mpegurl")


def _podle(x):
    return x if x in ("tema", "country") else None


def _cz(x):
    return x if x in ("kraj", "tema") else None


@app.api_route("/bouquet.tv", methods=["GET", "HEAD"])
async def bouquet(request: Request, podle: str = "", cz: str = ""):
    key = need_key(request)
    return PlainTextResponse(playlist.bouquet(CFG, key, _podle(podle), _cz(cz)), headers={"Content-Disposition": 'attachment; filename="userbouquet.webkamery.tv"'})


@app.api_route("/epg.xml", methods=["GET", "HEAD"])
async def epg(request: Request):
    need_key(request)
    return Response(playlist.epg(CFG), media_type="application/xml")


@app.api_route("/thumb/{name}", methods=["GET", "HEAD"])
async def thumb(name: str, request: Request):
    need_key(request)
    cid = re.sub(r"\.jpg$", "", name)
    p = os.path.join(store.BASE, CFG["thumbs_dir"], cid + ".jpg")
    if re.fullmatch(r"[a-z0-9_-]+", cid) and os.path.exists(p):
        return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})
    return FileResponse(store.path("static/placeholder.jpg"), media_type="image/jpeg")


@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    s = store.state()
    cams = store.cams()
    zap = [c for c in cams if c["enabled"]]
    ok = sum(1 for c in zap if s["cams"].get(c["id"], {}).get("status") in ("ok", "pomalá"))
    skryte = sum(1 for c in zap if not checker.visible(s["cams"].get(c["id"]), CFG))
    return {"verze": VERZE, "kod": KOD_VERZE, "kamer": len(cams), "zapnutych": len(zap), "ok": ok, "skrytych_mrtvych": skryte,
            "yt_dlp": await resolver.ytdlp_version(), "posledni_kontrola": s["last_check"],
            "kontrola_bezi": checker.running["active"], "v_cache": len(cache.snapshot()),
            "yt4k": {"dostupne": yt4k.dostupne(CFG), "proudy": yt4k.stav()}}


# ── administrace ────────────────────────────────────────────────────────────────
@app.get("/admin")
async def admin_bez_lomitka():
    return RedirectResponse("admin/", 302)


@app.get("/admin/", response_class=HTMLResponse)
async def admin(request: Request):
    if not admin_ok(request):
        return RedirectResponse("login", 302)
    return FileResponse(store.path("static/admin.html"), headers={"Cache-Control": "no-store"})


@app.get("/admin/login", response_class=HTMLResponse)
async def login_form(chyba: str = ""):
    html = open(store.path("static/login.html"), encoding="utf-8").read()
    return html.replace("{{CHYBA}}", "Špatné heslo." if chyba else "")


@app.post("/admin/login")
async def login(request: Request):
    form = dict(x.split("=", 1) for x in (await request.body()).decode().split("&") if "=" in x)
    from urllib.parse import unquote_plus
    if not CFG["admin_password_hash"] or not heslo_ok(unquote_plus(form.get("heslo", ""))):
        await asyncio.sleep(1.5)
        log.warning("admin: špatné heslo z %s", request.headers.get("x-real-ip") or request.client.host)
        return RedirectResponse("login?chyba=1", 303)
    exp = str(int(time.time()) + 30 * 86400)
    r = RedirectResponse("./", 303)
    r.set_cookie("wc_admin", exp + "." + _podpis("admin|" + exp), max_age=30 * 86400, httponly=True, samesite="lax",
                 secure=CFG["public_base"].startswith("https"))
    return r


@app.get("/admin/logout")
async def logout():
    r = RedirectResponse("login", 302)
    r.delete_cookie("wc_admin")
    return r


@app.get("/admin/api/cams")
async def api_cams(request: Request):
    need_admin(request)
    s = store.state()
    k = (CFG["access_keys"] or [""])[0]
    b = store.base(CFG)
    cams = store.cams()
    # kategorie jako v playlistu (velké země děleny podle zaměření) + zaměření – pro filtry v administraci
    pocty = {}
    for c in cams:
        if c["enabled"]:
            kk = (c.get("country") or "").upper()
            pocty[kk] = pocty.get(kk, 0) + 1
    velke = {kk for kk, n in pocty.items() if kk and n > CFG.get("velka_zeme", zeme.VELKA_ZEME)}
    for c in cams:
        c["_kat"] = zeme.skupina(c, CFG, None, None, velke)
        c["_tema"] = zeme.tema(c)
    ids = {c["id"] for c in cams if c.get("enabled", True)}     # vypnutá kamera se v upozornění na přepnutí neukazuje
    return {"cams": cams, "state": s["cams"], "cache": cache.snapshot(), "last_check": s["last_check"],
            "zmeny": {k: v for k, v in resolver.zmeny.items() if k in ids},
            "duplicity": store.duplicity(cams),       # DUPLICITY_V1: stejný stream i výběr u víc kamer
            "kontrola": checker.running, "dead_after": CFG["dead_after"], "zeme": zeme.ZEME, "group_by": CFG["group_by"],
            "odkazy": {"Playlist Kodi": "%s/playlist.m3u?client=kodi&k=%s" % (b, k), "Playlist VLC": "%s/playlist.m3u?client=vlc&k=%s" % (b, k),
                       "Playlist Enigma2": "%s/playlist.m3u?client=e2&k=%s" % (b, k), "Bouquet Enigma2": "%s/bouquet.tv?k=%s" % (b, k),
                       "EPG": "%s/epg.xml?k=%s" % (b, k),
                       "Kodi – podle zaměření (celý svět)": "%s/playlist.m3u?client=kodi&podle=tema&k=%s" % (b, k),
                       "Kodi – Česko podle krajů": "%s/playlist.m3u?client=kodi&cz=kraj&k=%s" % (b, k),
                       "Bouquet – podle zaměření": "%s/bouquet.tv?podle=tema&k=%s" % (b, k)}}


def _cista_kamera(d):
    c = {x: d.get(x, store.CAM_DEFAULT[x]) for x in store.CAM_DEFAULT}
    c["id"] = (d.get("id") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{2,40}", c["id"]):
        raise HTTPException(400, "id: 2–40 znaků a-z, 0-9, _ a -")
    if not str(c["source"]).startswith(("http://", "https://", "rtsp://", "rtsps://")):
        raise HTTPException(400, "source musí být http(s) nebo rtsp:// adresa")
    if str(c["source"]).startswith(("rtsp://", "rtsps://")):     # RTSP_V1: typ i režim podle adresy
        c["type"], c["mode"], c["uhd"] = "rtsp", "auto", False
    if c["type"] not in ("rtsp", "auto", "direct", "ytdlp", "ytkanal", "skyline", "feratel", "holidayinfo", "ipcamlive", "mjpeg", "regex", "snimek") or c["mode"] not in ("auto", "redirect", "proxy"):
        raise HTTPException(400, "neplatný type / mode")
    if not isinstance(c["headers"], dict):
        raise HTTPException(400, "headers musí být objekt")
    if c.get("zobrazeni") not in smycka.ZOBRAZENI:
        c["zobrazeni"] = "auto"
    c["enabled"] = bool(c["enabled"])
    c["oblibena"] = bool(c["oblibena"])
    c["uhd"] = bool(c.get("uhd"))
    c["rtsp_primo"] = bool(c.get("rtsp_primo", True))
    return c


@app.post("/admin/api/cams")
async def api_ulozit(request: Request):
    need_admin(request)
    d = await request.json()
    c = _cista_kamera(d)
    lst = store.cams()
    puvodni = d.get("puvodni_id")
    stejna = store.stejny_stream(lst, c, (c["id"], puvodni))
    if stejna and not d.get("i_tak"):           # DUPLICITY_V1: stejný stream už má jiná kamera
        return JSONResponse({"ok": False, "stejna": {"id": stejna["id"], "name": stejna["name"]}}, 409)
    if "oblibena" not in d:                     # formulář hvězdičku neposílá – zachovat
        c["oblibena"] = any(x.get("oblibena") for x in lst if x["id"] in (c["id"], puvodni))
    lst = [x for x in lst if x["id"] not in (c["id"], puvodni)] + [c]
    store.save_cams(lst)
    cache.drop(c["id"])
    s = store.state()
    store.sid_for(s, c["id"])
    store.save_state(s)
    log.info("admin: uložena kamera %s", c["id"])
    return {"ok": True}


@app.put("/admin/api/cams")
async def api_hromadne(request: Request):
    """Celý seznam kamer najednou (import, 3. 11. 2026): každá se zkontroluje, id musí být jedinečná. Stav kontrol zůstává."""
    need_admin(request)
    lst = [_cista_kamera(d) for d in await request.json()]
    ids = [c["id"] for c in lst]
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "duplicitní id")
    store.save_cams(lst)
    s = store.state()
    for c in lst:
        store.sid_for(s, c["id"])
    store.save_state(s)
    for cid in list(cache.snapshot()):
        cache.drop(cid)
    log.info("admin: hromadně uloženo %d kamer", len(lst))
    return {"ok": True, "kamer": len(lst)}


@app.delete("/admin/api/cams/{cid}")
async def api_smazat(cid: str, request: Request):
    need_admin(request)
    store.save_cams([x for x in store.cams() if x["id"] != cid])
    cache.drop(cid)
    log.info("admin: smazána kamera %s", cid)
    return {"ok": True}


@app.post("/admin/api/cams/{cid}/prepnout")
async def api_prepnout(cid: str, request: Request):
    need_admin(request)
    lst = store.cams()
    for x in lst:
        if x["id"] == cid:
            x["enabled"] = not x["enabled"]
    store.save_cams(lst)
    return {"ok": True}


@app.post("/admin/api/zmeny/skryt")
async def api_zmeny_skryt(request: Request):
    """upozornění „samo se přepnulo“: {id} skryje jednu kameru, bez id všechny (do dalšího přepnutí)"""
    need_admin(request)
    try:
        d = await request.json()
    except Exception:
        d = {}
    if d.get("id"):
        resolver.zmeny.pop(d["id"], None)
    else:
        resolver.zmeny.clear()
    return {"ok": True}


@app.post("/admin/api/cams/{cid}/oblibena")
async def api_oblibena(cid: str, request: Request):
    """hvězdička: kamera navíc ve skupině „⭐ Oblíbené“ nahoře v playlistech a bouquetu"""
    need_admin(request)
    lst = store.cams()
    for x in lst:
        if x["id"] == cid:
            x["oblibena"] = not x.get("oblibena")
    store.save_cams(lst)
    return {"ok": True}


# ── doplněk Kodi „Webkamery – úpravy“ (KODI_UPRAVY_V1, 11. 11. 2026): přihlášení heslem administrace (cookie) ──
def _slug(t):
    import unicodedata
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:34] or "kamera"


@app.get("/admin/api/kodi/najdi")
async def api_kodi_najdi(request: Request, id: str = "", nazev: str = ""):
    """kamera podle id (z ikony / adresy kanálu v Kodi) nebo přesného názvu kanálu"""
    need_admin(request)
    n = nazev.strip().casefold()
    return [{"id": c["id"], "name": c["name"], "oblibena": bool(c.get("oblibena")), "enabled": c["enabled"]}
            for c in store.cams() if (id and c["id"] == id) or (not id and n and c["name"].strip().casefold() == n)][:20]


@app.post("/admin/api/kodi/oblibena")
async def api_kodi_oblibena(request: Request):
    """{id, oblibena?: true/false} – bez hodnoty přepne; vrací nový stav"""
    need_admin(request)
    d = await request.json()
    lst = store.cams()
    c = next((x for x in lst if x["id"] == d.get("id")), None)
    if not c:
        raise HTTPException(404, "kamera nenalezena")
    c["oblibena"] = bool(d["oblibena"]) if "oblibena" in d else not c.get("oblibena")
    store.save_cams(lst)
    log.info("kodi: kamera %s oblíbená=%s", c["id"], c["oblibena"])
    return {"ok": True, "id": c["id"], "name": c["name"], "oblibena": c["oblibena"]}


@app.post("/admin/api/kodi/pridat")
async def api_kodi_pridat(request: Request):
    """{source, name, country?, oblibena?, ulozit_i_nezivou?} – vyzkouší kameru; když hraje (nebo ulozit_i_nezivou), uloží ji"""
    need_admin(request)
    d = await request.json()
    src, name = (d.get("source") or "").strip(), (d.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "chybí název")
    lst = store.cams()
    stejna = store.stejny_stream(lst, src)
    if stejna:
        if not stejna["enabled"] or (d.get("oblibena") and not stejna.get("oblibena")):
            stejna["enabled"] = True
            stejna["oblibena"] = stejna.get("oblibena") or bool(d.get("oblibena"))
            store.save_cams(lst)
        return {"ok": True, "uz_byla": True, "id": stejna["id"], "name": stejna["name"], "ulozeno": True}
    ids = {x["id"] for x in lst}
    zaklad = "k-" + _slug(name)
    cid, i = zaklad, 2
    while cid in ids:
        cid, i = "%s-%d" % (zaklad, i), i + 1
    c = _cista_kamera({"id": cid, "name": name, "source": src, "country": (d.get("country") or "").strip().upper()[:2],
                       "oblibena": bool(d.get("oblibena")), "note": "přidáno z Kodi " + time.strftime("%Y-%m-%d")})
    r = await checker.probe(CFG, c, None, live_wait=8)
    r.pop("url", None)
    r.update(id=cid, name=name, ulozeno=False)
    if r["ok"] or d.get("ulozit_i_nezivou"):
        store.save_cams(store.cams() + [c])
        s = store.state()
        store.sid_for(s, cid)
        store.save_state(s)
        r["ulozeno"] = True
        log.info("kodi: přidána kamera %s (%s) hraje=%s", cid, src, r["ok"])
        if r["ok"] and not checker.running["active"]:
            asyncio.create_task(_kontrola(cid))       # stav + náhled hned, ne až v noci
    else:
        cache.drop(cid)
    return r


@app.get("/kodi/", response_class=HTMLResponse)
async def kodi_slozka():
    """zdroj souborů pro Kodi (Instalovat ze ZIP): http://<server>/kodi/ – jen když config kodi_doplnek=true (KODI_UPRAVY_V1)"""
    if not CFG.get("kodi_doplnek"):
        raise HTTPException(404)
    d = store.path("kodi")
    zipy = sorted(f for f in os.listdir(d) if f.endswith(".zip")) if os.path.isdir(d) else []
    return "<html><body>" + "".join('<a href="%s">%s</a><br>' % (f, f) for f in zipy) + "</body></html>"


@app.api_route("/kodi/{name}", methods=["GET", "HEAD"])
async def kodi_soubor(name: str):
    p = store.path("kodi/" + os.path.basename(name))
    if not CFG.get("kodi_doplnek") or not name.endswith(".zip") or not os.path.isfile(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="application/zip")

# STRANKA_ODKAZ_V1 (23. 11. 2026, uživatel: „poradit mu, jak dostat ze stránky odkaz kamery“ → „to zní dobře – rozpoznat“):
# Rozpoznat u obecné stránky najde v kódu (i v rámcích <iframe>) přehrávač ipcamlive, odkazy .m3u8 a YouTube a dočasné
# odkazy převede na trvalé (chunklist_w… → playlist.m3u8; ipcamlive jen přes alias – ID streamu se mění).
_CHUNKLIST = re.compile(r"^(.*/)chunklist_w\d+(?:_b\d+)?\.m3u8(?:\?.*)?$", re.I)


async def _trvaly_odkaz(u):
    m = _CHUNKLIST.match(u)
    if not m:
        return u
    try:
        text, _ = await hls.fetch_text(CFG, m.group(1) + "playlist.m3u8")
        if "#EXTM3U" in text:
            return m.group(1) + "playlist.m3u8"
    except Exception:
        pass
    return u


async def _hledej_zdroje(text, base, hloubka=0):
    import html as _html
    from urllib.parse import urljoin
    t = _html.unescape(text.replace("\\/", "/"))
    kand = []

    def pridej(src, typ, popis):
        if src not in [k["source"] for k in kand]:
            kand.append({"source": src, "type": typ, "popis": popis})

    for m in re.finditer(r"ipcamlive\.com/player/player\.php\?(?:[^\"'<>\s]*?&)?alias=([\w-]+)", t, re.I):
        pridej("https://www.ipcamlive.com/player/player.php?alias=" + m.group(1), "ipcamlive", "přehrávač ipcamlive (alias %s)" % m.group(1))
    for m in list(re.finditer(r"(?:https?:)?//[^\s\"'<>\\]+?\.m3u8(?:\?[^\s\"'<>\\]*)?", t))[:5]:
        u = urljoin(base, m.group(0))
        if re.search(r"\.ipcamlive\.com/streams/", u, re.I):
            continue                                    # dočasné ID – alias je výš
        pridej(await _trvaly_odkaz(u), "direct", "stream m3u8")
    for m in re.finditer(r"youtube(?:-nocookie)?\.com/embed/([\w-]{11})(?![\w-])", t):
        if m.group(1) != "live_stream":
            pridej("https://www.youtube.com/watch?v=" + m.group(1), "auto", "YouTube")
    for m in re.finditer(r"youtube\.com/embed/live_stream\?channel=([\w-]+)", t):
        pridej("https://www.youtube.com/channel/%s/live" % m.group(1), "auto", "YouTube kanál – živě")
    if not kand and hloubka == 0:
        for m in list(re.finditer(r"<iframe[^>]+src=[\"']([^\"']+)", text, re.I))[:4]:
            u = urljoin(base, _html.unescape(m.group(1)))
            if not u.startswith("http") or re.search(r"google\.|facebook\.|twitter\.|doubleclick|mapy\.cz|seznam\.cz/reklama", u):
                continue
            if re.search(r"ipcamlive|youtube|\.m3u8", u, re.I):
                kand += await _hledej_zdroje(u, base, 1)
                continue
            try:
                t2, u2 = await hls.fetch_text(CFG, u)
                kand += await _hledej_zdroje(t2, u2, 1)
            except Exception:
                pass
    return kand


@app.post("/admin/api/rozpoznej")
async def api_rozpoznej(request: Request):
    """ROZPOZNEJ_ODKAZ_V1 (15. 11. 2026, uživatel: „v panelu jen vložení odkazu z adresy, ať si to zjistí samo“):
    z vloženého odkazu návrh vyplnění formuláře {type, name, id, country, category, group, match, source} a u stránky
    s víc kamerami (holidayinfo) seznam „kamery“ na výběr. Nic neukládá."""
    need_admin(request)
    d = await request.json()
    src = (d.get("source") or "").strip()
    if not re.match(r"(https?|rtsps?)://", src):
        raise HTTPException(400, "Vložte celý odkaz (https://… nebo rtsp://…)")
    out = {"source": src, "type": "auto"}
    if src.lower().startswith(("rtsp://", "rtsps://")):        # RTSP_V1: název z adresy, ověří se tlačítkem Test
        m = re.match(r"rtsps?://(?:[^@/]*@)?([^/:?]+)", src, re.I)
        out.update(type="rtsp", name=(m.group(1) if m else "RTSP kamera")[:80])
        zakl = _slug(out["name"]); nid, n = zakl, 2
        ids = {c["id"] for c in store.cams()}
        while nid in ids:
            nid = "%s-%d" % (zakl, n); n += 1
        out["id"] = nid
        return out
    src2 = await _trvaly_odkaz(src)                 # STRANKA_ODKAZ_V1: chunklist_w… → playlist.m3u8
    if src2 != src:
        src = out["source"] = src2
        out["poznamka"] = "Dočasný odkaz chunklist_w… převeden na stálý playlist.m3u8."
    if re.search(r"\.ipcamlive\.com/streams/", src, re.I):
        out.update(type="direct", poznamka="Odkaz ipcamlive obsahuje dočasné ID streamu – časem přestane fungovat. "
                                          "Vložte raději stránku s kamerou, Rozpoznat najde trvalý alias.")
        return out
    typ = resolver.typ_odhad({"source": src, "type": "auto", "match": ""})
    try:
        if typ == "holidayinfo":
            m = re.search(r"holidayinfo\.cz/(?:[a-z]{2}/)?camera/([\w-]+)", src)
            mc = re.search(r"[?&]camid=(\d+)", src) or re.search(r"/camera/[\w-]+/(\d+)", src)     # i /camera/kycerka/3101
            text, _ = await hls.fetch_text(CFG, "https://holidayinfo.cz/cs/camera/%s%s" % (m.group(1), "?camid=" + mc.group(1) if mc else ""))
            i = text.find("setupCamDiv(")
            data, _ = json.JSONDecoder().raw_decode(text[i + len("setupCamDiv("):])
            cd, loc = data.get("camdata") or {}, data.get("locdata") or {}
            mesto = loc.get("nazev_menu") or loc.get("nazev") or m.group(1)
            jmeno = lambda n: n if mesto.lower() in n.lower() else "%s – %s" % (mesto, n)
            ids = {c["id"] for c in store.cams()}
            zdroje = {c["source"] for c in store.cams()}
            # všechny kamery střediska v provozu – s videem i jen s obrázkem (HOLIDAYINFO_SNIMEK_V1); 2080 Kyčerka má video, ale je vypnutá od 2012
            kamery = []
            for k, v in ((data.get("loccams") or {}).get("bycamid") or {}).items():
                if str(v.get("camactive")) != "1":
                    continue
                zdr = "https://holidayinfo.cz/cs/camera/%s?camid=%s" % (m.group(1), k)
                kamery.append({"camid": k, "name": jmeno(v.get("camname") or k), "id": "hi-" + _slug(jmeno(v.get("camname") or k)),
                               "source": zdr, "video": bool(v.get("_has_video")), "uz_je": zdr in zdroje})
            camid = str(cd.get("id_kamery") or (mc.group(1) if mc else ""))
            nazev = cd.get("nazev_cam") or mesto
            out.update(type="holidayinfo", source="https://holidayinfo.cz/cs/camera/%s?camid=%s" % (m.group(1), camid),
                       name=jmeno(nazev), country="CZ", category="hory", kamery=kamery, ma_video=bool(data.get("video")))
        elif typ in ("ytdlp", "ytkanal") and re.search(r"youtu\.?be", src):
            r = await hls.client(CFG).get("https://www.youtube.com/oembed", params={"url": src, "format": "json"})
            if r.status_code == 200:
                out["name"] = re.sub(r"\s*[|–-]\s*(live|živě|livestream|webcam|webkamera)\b.*$", "", r.json().get("title", ""), flags=re.I).strip()
        if typ != "holidayinfo" and not out.get("name"):     # obecně: název stránky (og:title / <title>), i když YouTube oEmbed název nedal
            out["type"] = typ if typ in ("feratel", "skyline", "ipcamlive", "mjpeg") else "auto"
            if not re.search(r"\.(m3u8|mjpg|jpg|jpeg|png|mp4)(\?|$)", src, re.I):
                text, url_str = await hls.fetch_text(CFG, src)
                if typ == "ytdlp" and not re.search(r"youtu\.?be", src):        # STRANKA_ODKAZ_V1
                    nal = await _hledej_zdroje(text, url_str)
                    if nal:
                        out.update(source=nal[0]["source"], type=nal[0]["type"], nalezeno=nal[:10], stranka=src,
                                   poznamka="Na stránce nalezen zdroj: %s." % nal[0]["popis"])
                    else:
                        out["poznamka"] = "Na stránce jsem přímý odkaz nenašel – zkusí se přes yt-dlp (Otestovat)."
                mt = re.search(r"<meta[^>]+property=[\"']og:title[\"'][^>]+content=[\"']([^\"']+)", text, re.I) or re.search(r"<title[^>]*>([^<]+)</title>", text, re.I)
                if mt:
                    import html as _html
                    out["name"] = re.split(r"\s+[|–-]\s+", _html.unescape(mt.group(1)).strip())[0][:80]
            if re.search(r"\.(jpg|jpeg|png)(\?|$)", src, re.I):
                out["type"] = "snimek"
    except Exception as ex:
        out["chyba"] = "%s: %s" % (ex.__class__.__name__, ex)
    if out.get("name"):
        zakl = _slug(out["name"]); nid, n = zakl, 2
        ids = {c["id"] for c in store.cams()}
        while nid in ids:
            nid = "%s-%d" % (zakl, n); n += 1
        out["id"] = nid
    return out


@app.post("/admin/api/test")
async def api_test(request: Request):
    """Otestovat kameru bez uložení: resolve + živost + ffprobe + náhled (vrací se jako data URI)."""
    need_admin(request)
    d = await request.json()
    d.setdefault("id", "test")
    c = _cista_kamera(d)
    tmp = os.path.join(store.BASE, CFG["thumbs_dir"], "_test_%s.jpg" % secrets.token_hex(4))
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    r = await checker.probe(CFG, c, tmp, live_wait=8)
    if r["thumb"]:
        with open(tmp, "rb") as f:
            r["thumb"] = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
        os.unlink(tmp)
    cache.drop(c["id"]) if c["id"] == "test" else None
    r.pop("url", None)
    stejna = store.stejny_stream(store.cams(), c, (c["id"], d.get("puvodni_id")))
    if stejna:
        r["stejna"] = {"id": stejna["id"], "name": stejna["name"]}
    return r


@app.post("/admin/api/check")
async def api_check(request: Request, id: str = ""):
    """Kontrola všech kamer (cron 2:45 na NASu, 4:45 na VPS) nebo jedné (?id=…) – běží na pozadí, stav v /admin/api/cams → kontrola."""
    need_admin(request)
    if checker.running["active"]:
        return JSONResponse({"error": "kontrola už běží", "kontrola": checker.running}, 409)
    asyncio.create_task(_kontrola(id or None))
    return {"ok": True, "spusteno": id or "všechny"}


@app.post("/admin/api/check/stop")
async def api_check_stop(request: Request):
    """Zastaví běžící kontrolu: další kamery už nebere, rozdělané doběhnou a hotové výsledky se uloží."""
    need_admin(request)
    if not checker.running["active"]:
        return {"ok": True, "bezi": False}
    checker.running["zastavit"] = True
    log.info("admin: kontrola se zastavuje (%d/%d)", checker.running["done"], checker.running["total"])
    return {"ok": True, "bezi": True}


async def _kontrola(only):
    try:
        r = await checker.check_all(CFG, only)
        log.info("kontrola hotová: %s", r)
    except Exception:
        log.exception("kontrola selhala")


# ── start ───────────────────────────────────────────────────────────────────────
def placeholder():
    p = store.path("static/placeholder.jpg")
    if not os.path.exists(p):
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x37474f:s=480x270",
                        "-vf", "drawtext=text='Webkamera':fontcolor=white:fontsize=36:x=(w-tw)/2:y=(h-th)/2",
                        "-frames:v", "1", p], timeout=30)
        if not os.path.exists(p):           # bez fontu drawtext selže – aspoň barevná plocha
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=0x37474f:s=480x270",
                            "-frames:v", "1", p], timeout=30)


def init():
    c = store.load("config.json", {})
    c.setdefault("access_keys", [])
    if not c["access_keys"]:
        c["access_keys"].append(secrets.token_urlsafe(12))
    c.setdefault("secret", secrets.token_hex(32))
    c.setdefault("cron_token", secrets.token_urlsafe(24))
    for k in ("bind", "port", "public_base", "telegram"):
        c.setdefault(k, store.CONFIG_DEFAULT[k])
    store.save("config.json", c)
    print("config.json připraven. Klíč pro přehrávače: %s" % c["access_keys"][0])


def nastav_heslo():
    h1 = getpass.getpass("Nové heslo do administrace: ")
    if len(h1) < 8 or h1 != getpass.getpass("Znovu: "):
        sys.exit("Heslo musí mít aspoň 8 znaků a obě zadání se musí shodovat.")
    c = store.load("config.json", {})
    c["admin_password_hash"] = hash_hesla(h1)
    store.save("config.json", c)
    print("Heslo uloženo (platí po restartu služby).")


def nahodne_heslo():
    """bez terminálu (Claude Code „!“, ssh bez -t): vygeneruje heslo, uloží hash a heslo jednou vypíše"""
    h = secrets.token_urlsafe(12)
    c = store.load("config.json", {})
    c["admin_password_hash"] = hash_hesla(h)
    store.save("config.json", c)
    print("Heslo do administrace: %s   (uložené jen jako hash – zapiš si ho; platí po (re)startu služby)" % h)


def nas_nocni_plan():
    """NAS: noční úlohy o 2 hodiny dřív (8. 11. 2026, kontrola zatěžovala NAS až do dne). Nové verze si přepíše crontab samy při
    startu, přes OTA, bez zásahu v Plánovači. Mění jen řádky této instalace s původním časem. Aktualizace kódu až po nočním
    nahrání na GitHub (2:00). crond se nerestartuje: restart by shodil i službu spuštěnou z cronu, změnu souboru si načte sám."""
    soubor = os.environ.get("WEBCAMS_CRONTAB", "/etc/crontab")
    plan = (("aktualizuj_kod.py", "0", "10"), ("webcams.sh ytdlp", "15", "15"), ("doplnit_kamery.py", "30", "30"), ("webcams.sh kontrola", "45", "45"))
    try:
        with open(soubor, encoding="utf-8") as f:
            radky = f.read().splitlines(keepends=True)
        zmeneno = 0
        for i, radek in enumerate(radky):
            m = re.match(r"(\d+)(\s+)4(\s+.*)", radek, re.S)
            if not m or store.BASE not in radek:
                continue
            for znacka, stara, nova in plan:
                if znacka in radek and m.group(1) == stara:
                    radky[i] = nova + m.group(2) + "2" + m.group(3)
                    zmeneno += 1
                    break
        if not zmeneno:
            return
        if not os.path.exists(soubor + ".pred-webcams-noc"):
            shutil.copy2(soubor, soubor + ".pred-webcams-noc")
        docasny = soubor + ".webcams-tmp"
        with open(docasny, "w", encoding="utf-8") as f:
            f.write("".join(radky))
        shutil.copystat(soubor, docasny)
        os.replace(docasny, soubor)
        log.info("crontab: %d úloh webkamer posunuto o 2 hodiny dřív", zmeneno)
    except OSError as e:
        log.warning("crontab: %s", e)


if __name__ == "__main__":
    if "--init" in sys.argv:
        init()
    elif "--heslo-nahodne" in sys.argv:
        nahodne_heslo()
    elif "--heslo" in sys.argv:
        nastav_heslo()
    else:
        if not CFG["secret"] or not CFG["access_keys"]:
            sys.exit("Nejdřív: venv/bin/python main.py --init")
        placeholder()
        if CFG.get("vlastni_nas"):
            nas_nocni_plan()
            try:
                os.nice(10)                # NAS: služba i její yt-dlp a ffmpeg ustoupí DSM (8. 11. 2026)
            except OSError as e:
                log.warning("nice: %s", e)
        import uvicorn
        uvicorn.run(app, host=CFG["bind"], port=int(CFG["port"]), log_level="warning", proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1")
