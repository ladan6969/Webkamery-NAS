"""Jednorázově (4. 11. 2026): 3. 11. proběhly 3 kontroly a každá zvedla fail_streak → 60 kamer skrytých „po 3 dnech“
po jednom dni. Kamery, které naposledy selhaly 3. 11., dostanou fail_streak = 1 (jeden neúspěšný den) a fail_den.
Spuštění na VPS: cd /opt/webcams && sudo -u webcams venv/bin/python tools/oprava_fail_streak.py   (za běhu služby nevadí –
zapisuje se přes store se zámkem; pro jistotu spouštět, když kontrola neběží)"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import store  # noqa: E402

s = store.state()
n = 0
for cid, x in s["cams"].items():
    if (x.get("fail_streak") or 0) > 1 and (x.get("last_check") or "").startswith("2026-11-03"):
        x["fail_streak"], x["fail_den"] = 1, "2026-11-03"
        n += 1
store.save_state(s)
print("opraveno", n, "kamer")
