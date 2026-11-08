#!/bin/bash
# Webkamery → VPS, 7. 11. 2026: oprava – noční kontrola načítala state.json pro každou kameru (7 000×) a na ~3 min zablokovala službu.
# Spouštět, až doběhne běžící kontrola (skript to ověří). Restart služby ~5 s.
set -euo pipefail
W=$(cd "$(dirname "$0")" && pwd); KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202; T=$(date +%Y%m%d_%H%M)
scp -q -i "$KEY" "$W/checker.py" "$W/VERZE" "$H:/tmp/"
ssh -o BatchMode=yes -o ConnectTimeout=15 -i "$KEY" "$H" "T=$T bash -s" <<'VZDALENE'
set -uo pipefail
cd /opt/webcams
if ! curl -s -m 20 127.0.0.1:8093/health | grep -q '"kontrola_bezi":false'; then echo "!! kontrola ještě běží – spusť znovu později (nic nezměněno)"; rm -f /tmp/checker.py /tmp/VERZE; exit 1; fi
venv/bin/python -m py_compile /tmp/checker.py || exit 1
cp -p checker.py /root/zaloha-webcams-checker-$T.py
install -o webcams -g webcams -m 644 /tmp/checker.py checker.py; install -o webcams -g webcams -m 644 /tmp/VERZE VERZE; rm -f /tmp/checker.py /tmp/VERZE
systemctl restart webcams; sleep 8
curl -s -m 30 127.0.0.1:8093/health; echo
python3 - <<PY
import json
k = json.load(open("cams.json")); s = json.load(open("state.json"))["cams"]
from collections import Counter
c = Counter((x.get("country") or "?", "hraje" if s.get(x["id"], {}).get("last_ok") else ("skryto" if s.get(x["id"], {}).get("fail_streak", 0) else "neověřeno")) for x in k)
for z in ("CZ", "SK", "PL", "AT", "DE", "IT", "CH"): print(z, {t: n for (zz, t), n in c.items() if zz == z})
PY
echo "HOTOVO. Vrácení: cp -p /root/zaloha-webcams-checker-$T.py /opt/webcams/checker.py; systemctl restart webcams"
VZDALENE
