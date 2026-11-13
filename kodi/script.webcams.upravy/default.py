# Doplňky → Programy → Webkamery – úpravy
import xbmc
import xbmcgui

from resources.lib import api

ZEME = [("Česko", "CZ"), ("Slovensko", "SK"), ("Jiná země (kód)…", "?"), ("Bez země", "")]


def pridat():
    d = xbmcgui.Dialog()
    src = d.input("Adresa kamery (YouTube nebo .m3u8)").strip()
    if not src:
        return
    if not src.startswith("http"):
        src = "https://" + src
    nazev = d.input("Název kamery (jak se ukáže v TV)").strip()
    if not nazev:
        return
    i = d.select("Země", [z[0] for z in ZEME])
    if i < 0:
        return
    zeme = ZEME[i][1]
    if zeme == "?":
        zeme = d.input("Kód země (2 písmena, např. AT, PL, HR)").strip().upper()[:2]
    obl = d.yesno(api.NAZEV, "Přidat kameru i do ⭐ Oblíbených?")
    sp = api.Spojeni()
    p = xbmcgui.DialogProgress()
    p.create(api.NAZEV, "Zkouším kameru na serveru…")
    try:
        r = sp.api("kodi/pridat", {"source": src, "name": nazev, "country": zeme, "oblibena": obl}, timeout=150)
    finally:
        p.close()
    if r.get("uz_byla"):
        d.ok(api.NAZEV, "Stejný stream už na serveru je jako kamera „%s“ (%s) – nová se nepřidala.%s" % (r["name"], r["id"], " Je v ⭐ Oblíbených." if obl else ""))
    elif r.get("ulozeno"):
        d.ok(api.NAZEV, "Kamera „%s“ hraje (%s) a je přidaná." % (r["name"], r.get("resolution") or r.get("type") or "ok"))
    else:
        if not d.yesno(api.NAZEV, "Kamera teď nehraje:[CR]%s[CR][CR]Uložit ji i tak? (zkontroluje se v noci; dokud nehrála, v TV se po první neúspěšné kontrole skryje)" % (r.get("error") or "neznámá chyba")[:160]):
            return
        r = sp.api("kodi/pridat", {"source": src, "name": nazev, "country": zeme, "oblibena": obl, "ulozit_i_nezivou": True}, timeout=150)
        api.oznam("Uloženo: %s" % r["name"])
    api.obnovit_tv()


def hrajici():
    if not xbmc.Player().isPlaying():
        raise api.Chyba("Teď nic nehraje.")
    api.prepnout_oblibenou(api.cid_z(xbmc.getInfoLabel("Player.Filenameandpath"), xbmc.getInfoLabel("Player.Art(thumb)"),
                                     xbmc.getInfoLabel("Player.Icon")),
                           xbmc.getInfoLabel("VideoPlayer.ChannelName") or xbmc.getInfoLabel("Player.Title"))


def main():
    volby = [("➕ Přidat kameru", pridat), ("⭐ Právě hrající kamera: oblíbená ano / ne", hrajici),
             ("⚙ Nastavení", lambda: api.ADDON.openSettings())]
    i = xbmcgui.Dialog().select(api.NAZEV, [v[0] for v in volby])
    if i >= 0:
        try:
            volby[i][1]()
        except api.Chyba as e:
            api.oznam(str(e), True)


main()
