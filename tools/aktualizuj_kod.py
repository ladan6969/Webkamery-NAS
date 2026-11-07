"""OTA: nainstaluje nový kód služby z GitHubu, jen když se změní soubor VERZE (noční záloha na GitHub tedy nic nenasazuje).

Zapnutí na NASu (jednou, jako root, např. v Plánovači úloh):
  cd /volume1/webcams || exit 1
  U=https://raw.githubusercontent.com/ladan6969/Webkamery-NAS/main
  curl -fsSL $U/tools/aktualizuj_kod.py -o tools/aktualizuj_kod.py || exit 1
  venv/bin/python tools/aktualizuj_kod.py --instalovat

Pak běží každý den ve 4:00, výpis je v logs/aktualizace.log. Ručně: bez --instalovat.
Mění jen kód (*.py, static, vybrané nástroje). config.json, cams.json a state.json se nikdy nepřepisují.
Po restartu musí /health hlásit novou verzi, jinak se vrátí předchozí kód a tato verze se už nezkouší (.spatna-verze).
Jiná složka instalace: argument /cesta.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time

import httpx

REPO = "ladan6969/Webkamery-NAS"
ZDROJ_VERZE = "https://raw.githubusercontent.com/%s/main/VERZE" % REPO
ZDROJ_BALIK = "https://codeload.github.com/%s/tar.gz/refs/heads/main" % REPO
NASTROJE = ("oprava_fail_streak.py", "nazvy_mist.py", "doplnit_kamery.py", "aktualizuj_kod.py")
ZNACKA = "aktualizuj_kod.py"
CEKANI_HEALTH = 300       # armv7 NAS startuje pomalu a /health trvá i několik sekund


class Selhani(Exception):
    pass


def vypis(text):
    print("%s %s" % (time.strftime("%F %T"), text), flush=True)


def cil_souboru(jmeno):
    """cesta v archivu (bez kořenové složky) → cesta v instalaci, None = nepatří do instalace"""
    if re.fullmatch(r"[\w-]+\.py", jmeno) or jmeno == "VERZE":
        return jmeno
    if re.fullmatch(r"static/[\w-]+\.html", jmeno):
        return jmeno
    if jmeno.startswith("tools/") and jmeno[6:] in NASTROJE:
        return jmeno
    if jmeno == "nas/requirements-nas.txt":
        return "requirements-nas.txt"
    return None


def nacti(soubor):
    try:
        return open(soubor, encoding="utf-8").read().strip()
    except OSError:
        return ""


def stahni_a_rozbal(slozka, docasna):
    """→ {cesta v instalaci: rozbalený soubor}; bere se jen seznam povolených běžných souborů"""
    archiv = os.path.join(docasna, "balik.tar.gz")
    with httpx.stream("GET", ZDROJ_BALIK, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        with open(archiv, "wb") as f:
            for kus in r.iter_bytes():
                f.write(kus)
    nove = {}
    with tarfile.open(archiv) as t:
        for m in t:
            jmeno = m.name.split("/", 1)[1] if "/" in m.name else ""
            cil = cil_souboru(jmeno) if m.isfile() else None
            if not cil:
                continue
            obsah = os.path.join(docasna, "nove", cil)
            os.makedirs(os.path.dirname(obsah), exist_ok=True)
            with t.extractfile(m) as z, open(obsah, "wb") as f:
                shutil.copyfileobj(z, f)
            nove[cil] = obsah
    if "main.py" not in nove or "VERZE" not in nove:
        raise Selhani("v balíku chybí main.py nebo VERZE")
    for cil, obsah in nove.items():
        if cil.endswith(".py"):
            try:
                compile(open(obsah, "rb").read(), cil, "exec")
            except SyntaxError as ex:
                raise Selhani("chyba v %s: %s" % (cil, ex))
    return nove


def port(slozka):
    return json.load(open(os.path.join(slozka, "config.json"), encoding="utf-8")).get("port", 8093)


def pozustatky(slozka):
    """procesy služby z této složky, které drží port (starší webcams.sh je nemusel zastavit)"""
    try:
        v = subprocess.run(["netstat", "-tlnp"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for r in v.splitlines():
        p = r.split()
        if len(p) >= 7 and p[3].endswith(":%s" % port(slozka)) and "/" in p[6]:
            pid = p[6].split("/")[0]
            try:
                if os.path.realpath(os.readlink("/proc/%s/cwd" % pid)) == os.path.realpath(slozka):
                    out.append(int(pid))
            except OSError:
                pass
    return out


def restart(slozka):
    sh = os.path.join(slozka, "webcams.sh")
    subprocess.run([sh, "stop"], capture_output=True, timeout=120)
    time.sleep(3)
    for pid in pozustatky(slozka):
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    time.sleep(3)
    # start v novém sezení a bez našich rour – služba musí přežít konec tohoto skriptu
    subprocess.run([sh, "start"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=120, start_new_session=True)


def pocka_na_sluzbu(slozka, verze=None):
    """True, až /health odpoví (a hlásí zadanou verzi kódu)"""
    konec = time.time() + CEKANI_HEALTH
    while time.time() < konec:
        try:
            r = httpx.get("http://127.0.0.1:%s/health" % port(slozka), timeout=40)
            if r.status_code == 200 and (verze is None or r.json().get("kod") == verze):
                return True
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(5)
    return False


def aktualizuj(slozka):
    if not os.access(os.path.join(slozka, "webcams.sh"), os.X_OK):
        sys.exit("V %s není webcams.sh, nejdřív je potřeba instalace." % slozka)
    r = httpx.get(ZDROJ_VERZE, timeout=60, follow_redirects=True)
    r.raise_for_status()
    nova = r.text.strip()
    if not re.fullmatch(r"[\w.-]{1,40}", nova):
        sys.exit("Soubor VERZE na GitHubu nemá očekávaný tvar, nic neměním.")
    mistni = nacti(os.path.join(slozka, "VERZE"))
    spatna_soubor = os.path.join(slozka, ".spatna-verze")
    if nova == mistni:
        vypis("kód je aktuální (%s)" % mistni)
        return 0
    if nova == nacti(spatna_soubor):
        vypis("verze %s se minule nepovedla, čekám na novější" % nova)
        return 0

    docasna = os.path.join(slozka, ".aktualizace")
    zaloha = os.path.join(slozka, ".zaloha-kod")
    shutil.rmtree(docasna, ignore_errors=True)
    os.makedirs(docasna)
    try:
        nove = stahni_a_rozbal(slozka, docasna)
        nova = nacti(nove["VERZE"])          # raw soubor může být chvíli v mezipaměti, platí verze z balíku
        if nova == mistni:
            vypis("kód je aktuální (%s)" % mistni)
            return 0
        pozadavky = nove.get("requirements-nas.txt")
        if pozadavky and nacti(pozadavky) != nacti(os.path.join(slozka, "requirements-nas.txt")):
            pip = subprocess.run([os.path.join(slozka, "venv/bin/pip"), "install", "-q", "--prefer-binary", "-r", pozadavky],
                                 capture_output=True, text=True, timeout=1800)
            if pip.returncode:
                raise Selhani("knihovny: " + pip.stderr.strip()[-300:])
    except (Selhani, httpx.HTTPError, tarfile.TarError, OSError, subprocess.SubprocessError) as ex:
        vypis("nic nezměněno, chyba: %s" % ex)
        shutil.rmtree(docasna, ignore_errors=True)
        return 1

    pribyle = []
    try:
        # záloha až těsně před výměnou; soubory, které dřív nebyly, se při návratu smažou
        shutil.rmtree(zaloha, ignore_errors=True)
        for cil in nove:
            puvodni = os.path.join(slozka, cil)
            if os.path.exists(puvodni):
                os.makedirs(os.path.dirname(os.path.join(zaloha, cil)), exist_ok=True)
                shutil.copy2(puvodni, os.path.join(zaloha, cil))
            else:
                pribyle.append(puvodni)
        for cil, obsah in nove.items():
            os.makedirs(os.path.dirname(os.path.join(slozka, cil)), exist_ok=True)
            shutil.copy2(obsah, os.path.join(slozka, cil) + ".novy")
            os.replace(os.path.join(slozka, cil) + ".novy", os.path.join(slozka, cil))
        restart(slozka)
        if pocka_na_sluzbu(slozka, nova):
            if os.path.exists(spatna_soubor):
                os.remove(spatna_soubor)
            vypis("aktualizováno %s → %s" % (mistni or "?", nova))
            return 0
        vypis("CHYBA: po aktualizaci na %s služba nehlásí novou verzi, vracím předchozí kód" % nova)
    except (OSError, subprocess.SubprocessError, httpx.HTTPError) as ex:
        vypis("CHYBA při výměně na %s: %s, vracím předchozí kód" % (nova, ex))
    finally:
        shutil.rmtree(docasna, ignore_errors=True)

    for koren, _, soubory in os.walk(zaloha):
        for s in soubory:
            z = os.path.join(koren, s)
            os.replace(z, os.path.join(slozka, os.path.relpath(z, zaloha)))
    for p in pribyle:
        if os.path.exists(p):
            os.remove(p)
    open(spatna_soubor, "w", encoding="utf-8").write(nova + "\n")
    restart(slozka)
    vypis("předchozí kód %s" % ("běží" if pocka_na_sluzbu(slozka) else "NEODPOVÍDÁ, je potřeba zásah"))
    return 1


def instalovat(slozka):
    crontab = os.environ.get("WEBCAMS_CRONTAB", "/etc/crontab")
    log = os.path.join(slozka, "logs", "aktualizace.log")
    skript = os.path.join(slozka, "tools", ZNACKA)
    obsah = open(crontab, encoding="utf-8").read() if os.path.exists(crontab) else ""
    if ZNACKA in obsah:
        print("OTA už je zapnutá: každý den ve 4:00.")
        return
    radek = "0\t4\t*\t*\t*\troot\t%s %s %s >> %s 2>&1\n" % (os.path.join(slozka, "venv/bin/python"), skript, slozka, log)
    with open(crontab, "a", encoding="utf-8") as f:
        f.write(("" if not obsah or obsah.endswith("\n") else "\n") + radek)
    if "WEBCAMS_CRONTAB" not in os.environ:
        for prikaz in (["systemctl", "restart", "crond"], ["synoservicectl", "--restart", "crond"]):
            try:
                if subprocess.run(prikaz, capture_output=True, timeout=60).returncode == 0:
                    break
            except (OSError, subprocess.SubprocessError):
                pass
    print("OTA zapnutá: každý den ve 4:00, log %s" % log)


if __name__ == "__main__":
    volby = [a for a in sys.argv[1:] if not a.startswith("--")]
    cil = os.path.abspath(volby[0] if volby else "/volume1/webcams")
    if "--instalovat" in sys.argv:
        instalovat(cil)
        if os.environ.get("WEBCAMS_OTA_JEN_PLAN"):
            sys.exit(0)
    with open(os.path.join(cil, ".aktualizace.zamek"), "w") as zamek:
        try:
            fcntl.flock(zamek, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            sys.exit("Aktualizace už běží.")
        sys.exit(aktualizuj(cil))
