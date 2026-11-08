#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: kamery WIA přejmenovat na „Město – ulice“ podle kamery.wia.cz (např. Aš – Hedvábnická). Bez restartu.
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/.mistni/wia_nazvy.json" "$H:/tmp/nazvy.json"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -euo pipefail
cd /opt/webcams
cp -p cams.json /root/zaloha-webcams-cams-$T.json; chmod 644 /tmp/nazvy.json
sudo -u webcams python3 - <<PY
import json, os
n = json.load(open("/tmp/nazvy.json")); k = json.load(open("cams.json")); z = 0
for x in k:
    if x["id"] in n and x["name"] != n[x["id"]]:
        x["name"] = n[x["id"]]; z += 1
json.dump(k, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print("přejmenováno:", z)
PY
rm -f /tmp/nazvy.json; sleep 3
K=$(python3 -c 'import json;print(json.load(open("config.json"))["access_keys"][0])')
curl -s -m 60 "http://127.0.0.1:8093/playlist.m3u?k=$K" | grep -o "Aš – Hedvábnická\|Cheb – Dvořákova\|Praha – Nusle, Hvězdova" | sort -u
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json"
VZDALENE
