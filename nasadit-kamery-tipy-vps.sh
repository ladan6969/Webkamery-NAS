#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: nové kamery z tools/kamery_pridat.json (tipy: CamStreamer + kanály 15, wolfnet 8, Tatry/Jasná 25).
# Bez restartu služby; nové kamery se hned zkontrolují (nevysílající se skryjí, dokud nezačnou vysílat).
# Spouští uživatel na mojetv1: bash ~/webcams/nasadit-kamery-tipy-vps.sh
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/tools/kamery_pridat.json" "$H:/tmp/kamery_pridat.json"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -euo pipefail
cd /opt/webcams
cp -p cams.json /root/zaloha-webcams-cams-$T.json; chmod 644 /tmp/kamery_pridat.json
NOVE=$(sudo -u webcams python3 - <<PY
import json, os
k = json.load(open("cams.json")); ids = {x.get("id") for x in k}
nove = [x for x in json.load(open("/tmp/kamery_pridat.json")) if x["id"] not in ids]
k += nove
json.dump(k, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print(" ".join(x["id"] for x in nove))
PY
)
rm -f /tmp/kamery_pridat.json
echo "přidáno: $(wc -w <<< "$NOVE")"; sleep 3
TOK=$(python3 -c 'import json;print(json.load(open("config.json"))["cron_token"])')
for c in $NOVE; do
  for i in $(seq 60); do curl -s -m 10 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' && break; sleep 2; done
  curl -s -m 30 -o /dev/null -X POST -H "X-Webcams-Token: $TOK" "http://127.0.0.1:8093/admin/api/check?id=$c"
done
for i in $(seq 60); do curl -s -m 10 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' && break; sleep 2; done
NOVE="$NOVE" python3 - <<PY
import json, os
ids = os.environ["NOVE"].split(); k = {x["id"]: x["name"] for x in json.load(open("cams.json"))}
s = json.load(open("state.json"))["cams"]
ok = [k[i] for i in ids if s.get(i, {}).get("last_ok")]; ne = [k[i] for i in ids if not s.get(i, {}).get("last_ok")]
print("hraje %d, zatím nevysílá (skryto) %d: %s" % (len(ok), len(ne), ", ".join(ne)))
PY
curl -s -m 20 127.0.0.1:8093/health; echo
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json"
VZDALENE
