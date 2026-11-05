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
import logging
import logging.handlers
import os
import re
import secrets
import subprocess
import sys
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse

import cache
import checker
import hls
import playlist
import resolver
import smycka
import store
import zeme

VERZE = "1.0.0"
MPEGURL = "application/vnd.apple.mpegurl"
os.makedirs(store.path("logs"), exist_ok=True)
log = logging.getLogger("webcams")
log.setLevel(logging.INFO)
_h = logging.handlers.RotatingFileHandler(store.path("logs/webcams.log"), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_h)

app = FastAPI(title="Webcams proxy", docs_url=None, redoc_url=None, openapi_url=None)
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


@app.api_route("/cam/{cid}", methods=["GET", "HEAD"])
async def cam_stream(cid: str, request: Request, client: str = "kodi"):
    key = need_key(request)
    cam = najdi(cid)
    try:
        e = await aktualni(cam)
    except resolver.ResolveError as ex:
        log.warning("cam %s: %s", cid, ex)
        return PlainTextResponse("Kamera %s teď nejde načíst: %s" % (cam["name"], ex), 503)
    if e["kind"] == "mjpeg":                    # MJPEG: přehrávač si ho otevře sám
        return RedirectResponse(e["url"], 302)
    if e["kind"] == "feratel":                  # poslední klip → nekonečná HLS smyčka
        try:
            st = await smycka.priprav(CFG, cid, e["url"], cam.get("headers"))
        except Exception as ex:
            log.warning("smyčka %s: %s", cid, ex)
            return PlainTextResponse("Kamera %s: klip nejde připravit (%s)" % (cam["name"], ex), 503)
        return Response(smycka.playlist(CFG, cid, st, key), media_type=MPEGURL, headers={"Cache-Control": "no-store"})
    mode = playlist.mode_for(cam, client, store.state()["cams"].get(cid))
    if mode == "redirect" and client != "e2":
        return RedirectResponse(e["url"], 302)
    try:
        text, base = await hls.fetch_text(CFG, e["url"], cam.get("headers"))
    except Exception as ex:
        cache.drop(cid)
        log.warning("cam %s: playlist %s", cid, ex)
        return PlainTextResponse("Kamera %s: playlist nejde stáhnout (%s)" % (cam["name"], ex.__class__.__name__), 503)
    if client == "e2":                          # Enigma2: jen nejvyšší varianta, absolutní URI
        text = hls.reduce_master(text, base) if hls.is_master(text) else hls.absolutize(text, base)
        base = None
    if mode == "proxy":
        text = hls.rewrite_for_proxy(CFG, cid, text, base or e["url"], key)
    elif base:
        text = hls.absolutize(text, base)
    return Response(text, media_type=MPEGURL, headers={"Cache-Control": "no-store"})


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
    return {"verze": VERZE, "kamer": len(cams), "zapnutych": len(zap), "ok": ok, "skrytych_mrtvych": skryte,
            "yt_dlp": await resolver.ytdlp_version(), "posledni_kontrola": s["last_check"],
            "kontrola_bezi": checker.running["active"], "v_cache": len(cache.snapshot())}


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
    b = CFG["public_base"]
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
    return {"cams": cams, "state": s["cams"], "cache": cache.snapshot(), "last_check": s["last_check"],
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
    if not str(c["source"]).startswith(("http://", "https://")):
        raise HTTPException(400, "source musí být http(s) adresa")
    if c["type"] not in ("auto", "direct", "ytdlp", "ytkanal", "skyline", "feratel", "ipcamlive", "mjpeg", "regex") or c["mode"] not in ("auto", "redirect", "proxy"):
        raise HTTPException(400, "neplatný type / mode")
    if not isinstance(c["headers"], dict):
        raise HTTPException(400, "headers musí být objekt")
    c["enabled"] = bool(c["enabled"])
    return c


@app.post("/admin/api/cams")
async def api_ulozit(request: Request):
    need_admin(request)
    c = _cista_kamera(await request.json())
    lst = store.cams()
    puvodni = (await request.json()).get("puvodni_id")
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
    return r


@app.post("/admin/api/check")
async def api_check(request: Request, id: str = ""):
    """Kontrola všech kamer (cron 4:45) nebo jedné (?id=…) – běží na pozadí, stav v /admin/api/cams → kontrola."""
    need_admin(request)
    if checker.running["active"]:
        return JSONResponse({"error": "kontrola už běží", "kontrola": checker.running}, 409)
    asyncio.create_task(_kontrola(id or None))
    return {"ok": True, "spusteno": id or "všechny"}


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
        import uvicorn
        uvicorn.run(app, host=CFG["bind"], port=int(CFG["port"]), log_level="warning", proxy_headers=True,
                    forwarded_allow_ips="127.0.0.1")
