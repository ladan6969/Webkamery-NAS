#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: + kamery z archivu zdroje_kamer_evropa (feratel 973, kanály 38, webcamera.pl 582); kontrola – nevysílající kamera se po týdnu zkouší jen 1× týdně (checker.py, store.py, VERZE .5) + nové kamery z kamery_pridat.json.
# Restartuje službu webcams (~5 s); nové kamery se hned zkontrolují. Spouštět, až neběží kontrola.
# Spouští uživatel na mojetv1: bash ~/webcams/nasadit-kamery-tipy-vps.sh
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/tools/kamery_pridat.json" "$W/checker.py" "$W/store.py" "$W/VERZE" "$H:/tmp/"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -euo pipefail
cd /opt/webcams
curl -s -m 10 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' || { echo "!! právě běží kontrola – spusť znovu později"; exit 1; }
cp -p cams.json /root/zaloha-webcams-cams-$T.json; chmod 644 /tmp/kamery_pridat.json
tar czf /root/zaloha-webcams-kod-$T.tgz checker.py store.py VERZE
venv/bin/python -m py_compile /tmp/checker.py /tmp/store.py
for f in checker.py store.py VERZE; do install -o webcams -g webcams -m 644 /tmp/$f $f; rm -f /tmp/$f; done
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
echo "přidáno: $(wc -w <<< "$NOVE")"; systemctl restart webcams; sleep 6
TOK=$(python3 -c 'import json;print(json.load(open("config.json"))["cron_token"])')
if [ "$(wc -w <<< "$NOVE")" -gt 60 ]; then
  # velká dávka: jedna celková kontrola na pozadí (~1,5 h), nevysílající nové kamery se pak skryjí
  curl -s -m 30 -X POST -H "X-Webcams-Token: $TOK" "http://127.0.0.1:8093/admin/api/check"; echo
  echo "celková kontrola běží na pozadí – stav: curl -s 127.0.0.1:8093/health (kontrola_bezi)"
  curl -s -m 20 127.0.0.1:8093/health; echo
  echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json; tar xzf /root/zaloha-webcams-kod-$T.tgz -C /opt/webcams; systemctl restart webcams"
  exit 0
fi
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
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-cams-$T.json /opt/webcams/cams.json; tar xzf /root/zaloha-webcams-kod-$T.tgz -C /opt/webcams; systemctl restart webcams"
VZDALENE
