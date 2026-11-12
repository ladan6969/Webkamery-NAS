# Webkamery – úpravy: spojení se serverem webkamer (přihlášení heslem administrace, cookie v profilu doplňku)
import http.cookiejar
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON = xbmcaddon.Addon()
NAZEV = "Webkamery"
PROFIL = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))


class Chyba(Exception):
    pass


def oznam(text, chyba=False):
    xbmcgui.Dialog().notification(NAZEV, text, xbmcgui.NOTIFICATION_ERROR if chyba else xbmcgui.NOTIFICATION_INFO, 4000)


def _z_pvr():
    """adresa serveru z playlistu PVR IPTV Simple (…/playlist.m3u?k=…)"""
    try:
        a = xbmcaddon.Addon("pvr.iptvsimple")
        for k in ("m3uUrl", "epgUrl"):
            u = a.getSetting(k)
            m = re.match(r"(https?://.+?)/(playlist\.m3u|epg\.xml)", u or "")
            if m:
                return m.group(1)
    except Exception:
        pass
    # Kodi 21: nastavení instancí v instance-settings-*.xml
    try:
        d = xbmcvfs.translatePath("special://profile/addon_data/pvr.iptvsimple/")
        for f in sorted(os.listdir(d)):
            if f.endswith(".xml"):
                m = re.search(r"(https?://[^\"<\s]+?)/(playlist\.m3u|epg\.xml)", open(os.path.join(d, f), encoding="utf-8").read())
                if m:
                    return m.group(1).replace("&amp;", "&")
    except Exception:
        pass
    return ""


def server():
    s = (ADDON.getSetting("server") or "").strip().rstrip("/") or _z_pvr()
    if not s:
        raise Chyba("Chybí adresa serveru webkamer – vyplň ji v nastavení doplňku.")
    if not s.startswith("http"):
        s = "http://" + s
    return s


class Spojeni:
    def __init__(self):
        os.makedirs(PROFIL, exist_ok=True)
        self.base = server()
        self.jar = http.cookiejar.LWPCookieJar(os.path.join(PROFIL, "cookies.lwp"))
        try:
            self.jar.load(ignore_discard=True)
        except Exception:
            pass
        ctx = ssl.create_default_context()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar),
                                              urllib.request.HTTPSHandler(context=ctx))

    def _req(self, cesta, data=None, timeout=60):
        url = self.base + "/admin/" + cesta
        body, h = None, {"User-Agent": "Kodi webcams.upravy"}
        if data is not None:
            body, h["Content-Type"] = json.dumps(data).encode(), "application/json"
        return self.op.open(urllib.request.Request(url, body, h), timeout=timeout)

    def prihlasit(self):
        heslo = ADDON.getSetting("heslo")
        if not heslo:
            heslo = xbmcgui.Dialog().input("Heslo administrace webkamer", option=xbmcgui.ALPHANUM_HIDE_INPUT)
            if not heslo:
                raise Chyba("Bez hesla administrace nejde kamery měnit.")
            ADDON.setSetting("heslo", heslo)
        r = self.op.open(urllib.request.Request(self.base + "/admin/login",
                                                urllib.parse.urlencode({"heslo": heslo}).encode(),
                                                {"User-Agent": "Kodi webcams.upravy"}), timeout=30)
        if "chyba=1" in r.geturl():
            ADDON.setSetting("heslo", "")
            raise Chyba("Špatné heslo administrace webkamer.")
        self.jar.save(ignore_discard=True)

    def api(self, cesta, data=None, timeout=60):
        for pokus in (1, 2):
            try:
                with self._req("api/" + cesta, data, timeout) as r:
                    return json.loads(r.read().decode())
            except urllib.error.HTTPError as e:
                if e.code == 401 and pokus == 1:
                    self.prihlasit()
                    continue
                try:
                    zprava = json.loads(e.read().decode()).get("detail")
                except Exception:
                    zprava = None
                raise Chyba(zprava or "Server odpověděl chybou %d" % e.code)
            except urllib.error.URLError as e:
                raise Chyba("Server webkamer nedostupný (%s)" % getattr(e, "reason", e))


def obnovit_tv():
    """PVR IPTV Simple vypnout a zapnout = znovu načte playlist (oblíbené, nové kamery)"""
    if ADDON.getSetting("obnovit") != "true":
        oznam("Změna se v TV ukáže po obnovení kanálů.")
        return
    for zap in (False, True):
        xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "Addons.SetAddonEnabled",
                                        "params": {"addonid": "pvr.iptvsimple", "enabled": zap}}))
        xbmc.sleep(1500)


def cid_z(*texty):
    """id kamery z adresy ikony (/thumb/<id>.jpg) nebo streamu (/cam/<id>)"""
    for t in texty:
        m = re.search(r"/(?:thumb|cam|proxy|oriz)/([a-z0-9_-]+?)(?:\.jpg)?(?:[?/]|$)", urllib.parse.unquote(t or ""))
        if m:
            return m.group(1)
    return ""


def najdi(sp, cid, nazev):
    """→ kamera {id, name, oblibena} nebo None"""
    lst = sp.api("kodi/najdi?" + urllib.parse.urlencode({"id": cid})) if cid else []
    if not lst and nazev:
        lst = sp.api("kodi/najdi?" + urllib.parse.urlencode({"nazev": nazev}))
    if len(lst) > 1:
        i = xbmcgui.Dialog().select("Která kamera?", ["%s (%s)" % (x["name"], x["id"]) for x in lst])
        return lst[i] if i >= 0 else None
    return lst[0] if lst else None


def prepnout_oblibenou(cid, nazev):
    sp = Spojeni()
    c = najdi(sp, cid, nazev)
    if not c:
        raise Chyba("„%s“ není kamera z tohoto serveru webkamer." % (nazev or cid))
    r = sp.api("kodi/oblibena", {"id": c["id"]})
    oznam(("⭐ %s je v oblíbených" if r["oblibena"] else "%s odebrána z oblíbených") % r["name"])
    obnovit_tv()
