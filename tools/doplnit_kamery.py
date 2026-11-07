"""Automatický aktualizátor kamer: doplní nové kamery z GitHubu do běžící instalace, bez přeinstalace a bez restartu.

Zapnutí na NASu (jednou, jako root, např. v Plánovači úloh):
  curl -fsSL https://raw.githubusercontent.com/ladan6969/Webkamery-NAS/main/tools/doplnit_kamery.py -o /volume1/webcams/tools/doplnit_kamery.py && /volume1/webcams/venv/bin/python /volume1/webcams/tools/doplnit_kamery.py --instalovat

Pak běží každý den ve 4:30 (před kontrolou ve 4:45), výpis je v logs/doplnit.log. Ručně: bez --instalovat.
Tento skript stahuje jen seznam kamer (data); kód aktualizuje tools/aktualizuj_kod.py.
Kamera se přidá jen jednou: kameru, kterou smažeš, automatika nevrátí (kamery_nabidnute.json). Vlastní kamery a úpravy zůstanou.
Jiná složka instalace: argument /cesta.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

import httpx

ZDROJ = "https://raw.githubusercontent.com/ladan6969/Webkamery-NAS/main/tools/kamery_pridat.json"
TYPY = ("auto", "direct", "ytdlp", "ytkanal", "skyline", "feratel", "ipcamlive", "mjpeg", "regex", "snimek")
ZNACKA = "doplnit_kamery.py"


def platna(c):
    return (isinstance(c, dict) and re.fullmatch(r"[a-z0-9_-]{2,40}", str(c.get("id"))) and c.get("name")
            and str(c.get("source")).startswith(("http://", "https://")) and c.get("type") in TYPY)


def zapis(soubor, data):
    docasny = soubor + ".tmp"
    with open(docasny, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    if os.path.exists(soubor):
        shutil.copystat(soubor, docasny)
    os.replace(docasny, soubor)


def doplnit(slozka):
    r = httpx.get(ZDROJ, timeout=60, follow_redirects=True)
    r.raise_for_status()
    seznam = r.json()
    if not isinstance(seznam, list) or not all(platna(c) for c in seznam):
        sys.exit("Seznam kamer z GitHubu nemá očekávaný tvar, nic neměním.")

    soubor = os.path.join(slozka, "cams.json")
    stav = os.path.join(slozka, "kamery_nabidnute.json")
    kamery = json.load(open(soubor, encoding="utf-8"))
    nabidnute = set(json.load(open(stav, encoding="utf-8"))) if os.path.exists(stav) else set()
    ids = {c.get("id") for c in kamery}
    nove = [c for c in seznam if c["id"] not in ids and c["id"] not in nabidnute]

    if nove:
        zaloha = "%s.pred-doplnenim-%s" % (soubor, time.strftime("%Y%m%d_%H%M%S"))
        shutil.copy2(soubor, zaloha)
        zapis(soubor, kamery + nove)
        print("%s přidáno %d kamer, celkem %d. Záloha: %s" % (time.strftime("%F %T"), len(nove), len(kamery) + len(nove), zaloha))
    else:
        print("%s nic k doplnění, kamer je %d." % (time.strftime("%F %T"), len(kamery)))
    zapis(stav, sorted(nabidnute | {c["id"] for c in seznam}))


def instalovat(slozka):
    crontab = os.environ.get("WEBCAMS_CRONTAB", "/etc/crontab")
    log = os.path.join(slozka, "logs", "doplnit.log")
    skript = os.path.join(slozka, "tools", ZNACKA)
    obsah = open(crontab, encoding="utf-8").read() if os.path.exists(crontab) else ""
    if ZNACKA in obsah:
        print("Automatika už je zapnutá: každý den ve 4:30.")
        return
    radek = "30\t4\t*\t*\t*\troot\t%s %s %s >> %s 2>&1\n" % (os.path.join(slozka, "venv/bin/python"), skript, slozka, log)
    with open(crontab, "a", encoding="utf-8") as f:
        f.write(("" if not obsah or obsah.endswith("\n") else "\n") + radek)
    if "WEBCAMS_CRONTAB" not in os.environ:
        for prikaz in (["systemctl", "restart", "crond"], ["synoservicectl", "--restart", "crond"]):
            try:
                if subprocess.run(prikaz, capture_output=True, timeout=60).returncode == 0:
                    break
            except (OSError, subprocess.SubprocessError):
                pass
    print("Automatika zapnutá: každý den ve 4:30, log %s" % log)


if __name__ == "__main__":
    volby = [a for a in sys.argv[1:] if not a.startswith("--")]
    cil = volby[0] if volby else "/volume1/webcams"
    if "--instalovat" in sys.argv:
        instalovat(cil)
    doplnit(cil)
