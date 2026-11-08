#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: typ „snimek“ (obrázek → video smyčka) + 98 kamer ČHMÚ (Česko – Počasí) + oprava kontroly
# (state jednou) + týdenní kontrola nevysílajících. VERZE .7. Počká, až doběhne běžící kontrola, pak restart (~5 s).
# Spouští uživatel na mojetv1: bash ~/webcams/nasadit-snimek-vps.sh
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
SOUBORY="resolver.py smycka.py main.py zeme.py checker.py store.py VERZE static/admin.html tools/doplnit_kamery.py"
ssh -o BatchMode=yes -i "$KEY" "$H" "rm -rf /tmp/snimek-nasadit; mkdir -p /tmp/snimek-nasadit/static /tmp/snimek-nasadit/tools"
for f in $SOUBORY; do scp -q -i "$KEY" "$W/$f" "$H:/tmp/snimek-nasadit/$f"; done
scp -q -i "$KEY" "$W/.mistni/kamery_pridat_vse.json" "$H:/tmp/snimek-nasadit/kamery.json"
ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=60 -i "$KEY" "$H" "T=$T SOUBORY='$SOUBORY' bash -s" <<'VZDALENE'
set -uo pipefail
cd /opt/webcams; N=/tmp/snimek-nasadit
echo "čekám, až doběhne běžící kontrola…"
for i in $(seq 360); do curl -s -m 20 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' && break; sleep 60; done
curl -s -m 20 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' || { echo "!! kontrola pořád běží – nic nezměněno"; exit 1; }
venv/bin/python -m py_compile $N/resolver.py $N/smycka.py $N/main.py $N/zeme.py $N/checker.py $N/store.py $N/tools/doplnit_kamery.py || exit 1
tar czf /root/zaloha-webcams-kod-$T.tgz $SOUBORY cams.json
for f in $SOUBORY; do install -o webcams -g webcams -m 644 $N/$f $f; done
NOVE=$(sudo -u webcams python3 - <<PY
import json, os
k = json.load(open("cams.json")); ids = {x.get("id") for x in k}
nove = [x for x in json.load(open("$N/kamery.json")) if x["id"] not in ids]
json.dump(k + nove, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print(" ".join(x["id"] for x in nove))
PY
)
rm -rf $N
echo "nasazeno, přidáno kamer: $(wc -w <<< "$NOVE")"
systemctl restart webcams; sleep 8
curl -s -m 30 127.0.0.1:8093/health; echo
TOK=$(python3 -c 'import json;print(json.load(open("config.json"))["cron_token"])')
for c in $NOVE; do
  for i in $(seq 90); do curl -s -m 10 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' && break; sleep 2; done
  curl -s -m 30 -o /dev/null -X POST -H "X-Webcams-Token: $TOK" "http://127.0.0.1:8093/admin/api/check?id=$c"
done
for i in $(seq 90); do curl -s -m 10 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false' && break; sleep 2; done
NOVE="$NOVE" python3 - <<PY
import json, os
ids = os.environ["NOVE"].split(); k = {x["id"]: x["name"] for x in json.load(open("cams.json"))}
s = json.load(open("state.json"))["cams"]
ne = [k[i] for i in ids if not s.get(i, {}).get("last_ok")]
print("nové: hraje %d, skryto %d %s" % (len(ids) - len(ne), len(ne), ", ".join(ne)))
PY
K=$(python3 -c 'import json;print(json.load(open("config.json"))["access_keys"][0])')
curl -s -m 60 -o /dev/null -w "smyčka chmi-cheb: %{http_code}, %{time_total} s\n" "http://127.0.0.1:8093/cam/chmi-cheb?client=kodi&k=$K"
echo "HOTOVO. Vrácení: tar xzf /root/zaloha-webcams-kod-$T.tgz -C /opt/webcams; systemctl restart webcams"
VZDALENE
