#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: dalších 22 kamer WIA (zatím nevysílají; zdroj = stránka WIA, yt-dlp si z ní vezme video) +
# kontrola: kamera, která ještě nikdy nehrála, se skryje po první neúspěšné kontrole. Po přidání zkontroluje nové kamery hned.
# Spouští uživatel na mojetv1: bash ~/webcams/nasadit-wia2-vps.sh   (restartuje službu webcams, ~5 s výpadek)
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/checker.py" "$W/VERZE" "$W/tools/kamery_pridat.json" "$H:/tmp/"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -euo pipefail
cd /opt/webcams
cp -p cams.json /root/zaloha-webcams-cams-$T.json; cp -p checker.py /root/zaloha-webcams-checker-$T.py
venv/bin/python -m py_compile /tmp/checker.py
install -o webcams -g webcams -m 644 /tmp/checker.py checker.py; install -o webcams -g webcams -m 644 /tmp/VERZE VERZE
chmod 644 /tmp/kamery_pridat.json
NOVE=$(sudo -u webcams python3 - <<PY
import json, os
k = json.load(open("cams.json")); ids = {x.get("id") for x in k}
nove = [x for x in json.load(open("/tmp/kamery_pridat.json")) if x["id"] not in ids]
k += nove
json.dump(k, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print(" ".join(x["id"] for x in nove))
PY
)
rm -f /tmp/checker.py /tmp/VERZE /tmp/kamery_pridat.json
echo "přidáno: $(wc -w <<< "$NOVE")"
systemctl restart webcams; sleep 6
curl -s -m 30 127.0.0.1:8093/health; echo
TOK=$(python3 -c 'import json;print(json.load(open("config.json"))["cron_token"])')
for c in $NOVE; do
  curl -s -m 30 -o /dev/null -X POST -H "X-Webcams-Token: $TOK" "http://127.0.0.1:8093/admin/api/check?id=$c"
  for i in $(seq 60); do sleep 2; curl -s -m 10 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' && break; done
done
python3 - <<PY
import json
k = {x["id"]: x["name"] for x in json.load(open("cams.json")) if x["id"].startswith("wia-")}
s = json.load(open("state.json"))["cams"]
hraje = [n for i, n in k.items() if s.get(i, {}).get("last_ok")]
ne = [n for i, n in k.items() if not s.get(i, {}).get("last_ok") and s.get(i, {}).get("fail_streak", 0) >= 1]
nez = [n for i, n in k.items() if i not in s]
print("WIA celkem %d: hraje %d, neověřeno (kontrola v noci) %d, skryto – zatím nevysílá %d: %s" % (len(k), len(hraje), len(nez), len(ne), ", ".join(ne)))
PY
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json; cp -p /root/zaloha-webcams-checker-$T.py /opt/webcams/checker.py; systemctl restart webcams"
VZDALENE
