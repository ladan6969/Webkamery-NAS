"""Hromadně doplní nové kamery z GitHubu do běžící instalace, bez přeinstalace a bez restartu.

Spuštění (na NASu jako root, např. v Plánovači úloh):
  curl -fsSL https://raw.githubusercontent.com/ladan6969/Webkamery-NAS/main/tools/doplnit_kamery.py | /volume1/webcams/venv/bin/python -

Přidá jen kamery, které v instalaci ještě nejsou. Vlastní kamery a úpravy zůstanou, smazané se ale znovu přidají.
Před zápisem uloží zálohu cams.json. Jiná složka instalace: první argument (… python - /cesta).
"""
import json
import os
import shutil
import sys
import time

import httpx

ZDROJ = "https://raw.githubusercontent.com/ladan6969/Webkamery-NAS/main/tools/kamery_pridat.json"
slozka = sys.argv[1] if len(sys.argv) > 1 else "/volume1/webcams"
soubor = os.path.join(slozka, "cams.json")

r = httpx.get(ZDROJ, timeout=60, follow_redirects=True)
r.raise_for_status()
seznam = r.json()
if not isinstance(seznam, list) or not all(isinstance(c, dict) and c.get("id") and c.get("source") for c in seznam):
    sys.exit("Seznam kamer z GitHubu nemá očekávaný tvar, nic neměním.")

kamery = json.load(open(soubor, encoding="utf-8"))
ids = {c.get("id") for c in kamery}
nove = [c for c in seznam if c["id"] not in ids]
if not nove:
    print("Nic k doplnění, kamer je %d." % len(kamery))
    sys.exit(0)

zaloha = "%s.pred-doplnenim-%s" % (soubor, time.strftime("%Y%m%d_%H%M%S"))
shutil.copy2(soubor, zaloha)
docasny = soubor + ".tmp"
with open(docasny, "w", encoding="utf-8") as f:
    json.dump(kamery + nove, f, ensure_ascii=False, indent=1)
shutil.copystat(soubor, docasny)
os.replace(docasny, soubor)
print("Přidáno %d kamer, celkem %d." % (len(nove), len(kamery) + len(nove)))
print("Záloha: %s" % zaloha)
