#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: 33 kamer WIA (kamery.wia.cz) + resolver: skončené video YouTube → nové ze stránky kamery (pole page).
# Spouští uživatel na mojetv1: bash ~/webcams/nasadit-wia-vps.sh   (restartuje službu webcams, ~5 s výpadek)
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/resolver.py" "$W/VERZE" "$W/tools/kamery_pridat.json" "$H:/tmp/"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -euo pipefail
cd /opt/webcams
cp -p cams.json /root/zaloha-webcams-cams-$T.json; cp -p resolver.py /root/zaloha-webcams-resolver-$T.py
venv/bin/python -m py_compile /tmp/resolver.py
install -o webcams -g webcams -m 644 /tmp/resolver.py resolver.py; install -o webcams -g webcams -m 644 /tmp/VERZE VERZE
chmod 644 /tmp/kamery_pridat.json
sudo -u webcams python3 - <<PY
import json, os
k = json.load(open("cams.json")); ids = {x.get("id") for x in k}
nove = [x for x in json.load(open("/tmp/kamery_pridat.json")) if x["id"] not in ids]
k += nove
json.dump(k, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print("přidáno:", len(nove), " celkem:", len(k))
PY
rm -f /tmp/resolver.py /tmp/VERZE /tmp/kamery_pridat.json
systemctl restart webcams; sleep 6
curl -s -m 30 127.0.0.1:8093/health; echo
K=$(python3 -c 'import json;print(json.load(open("config.json"))["access_keys"][0])')
for c in wia-as wia-cheb-3 wia-praha-12 wia-velke-mezirici; do
  curl -s -m 90 -o /dev/null -w "$c: %{http_code}, %{time_total} s\n" "http://127.0.0.1:8093/cam/$c?client=kodi&k=$K"
done
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json; cp -p /root/zaloha-webcams-resolver-$T.py /opt/webcams/resolver.py; rm /opt/webcams/VERZE; systemctl restart webcams"
VZDALENE
