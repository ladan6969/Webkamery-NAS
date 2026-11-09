#!/bin/bash
# Webkamery → VPS, 8. 11. 2026: 1395 nových kamer (Česko s námi, onlinezona, Windy přes meteoblue) z tools/kamery_pridat.json.
# Bez restartu a bez okamžité kontroly – nové kamery ověří noční kontrola ve 4:45. Spouští uživatel: bash ~/webcams/nasadit-kamery-8-11-vps.sh
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/tools/kamery_pridat.json" "$H:/tmp/kamery_pridat.json"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -euo pipefail
cd /opt/webcams
curl -s -m 15 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' || { echo "!! běží kontrola – spusť později"; rm -f /tmp/kamery_pridat.json; exit 1; }
cp -p cams.json /root/zaloha-webcams-cams-$T.json; chmod 644 /tmp/kamery_pridat.json
sudo -u webcams python3 - <<PY
import json, os
k = json.load(open("cams.json")); ids = {x.get("id") for x in k}
nove = [x for x in json.load(open("/tmp/kamery_pridat.json")) if x["id"] not in ids]
json.dump(k + nove, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print("přidáno:", len(nove))
PY
rm -f /tmp/kamery_pridat.json
curl -s -m 20 127.0.0.1:8093/health; echo
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json"
VZDALENE
