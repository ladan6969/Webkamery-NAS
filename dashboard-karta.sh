#!/bin/bash
# Karta Webkamery do dashboardu na mojetv1 (/opt/dashboard/index.html), 3. 11. 2026. Spustit: sudo bash ~/webcams/dashboard-karta.sh
# Idempotentní (značka WEBCAMS_DASHBOARD_V1), záloha s časem. Statický soubor – nginx se nepřenačítá.
set -e
F=/opt/dashboard/index.html
if grep -q WEBCAMS_DASHBOARD_V1 "$F"; then echo "karta už je"; exit 0; fi
cp -p "$F" "$F.bak.$(date +%Y%m%d_%H%M%S)"
python3 - "$F" <<'PY'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
kotva = "  // PROXY_ADMIN_CARDS_V1"
assert s.count(kotva) == 1, "kotva PROXY_ADMIN_CARDS_V1 nenalezena"
karta = ('  // WEBCAMS_DASHBOARD_V1 – běží na VPS Caoliny (statistiky CPU/RAM tady nejsou)\n'
         '  { name: "Webkamery", desc: "Webcams proxy – stálé adresy kamer (VPS Caoliny)", '
         'url: "https://api.caolina.zone/webcams/admin/", statsPort: null },\n')
s = s.replace(kotva, karta + kotva)
open(p, "w", encoding="utf-8").write(s)
PY
grep -n "WEBCAMS_DASHBOARD_V1" -A1 "$F"
