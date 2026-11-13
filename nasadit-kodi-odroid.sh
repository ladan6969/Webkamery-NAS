#!/bin/bash
# 12. 11. 2026: doplněk Kodi 0.1.1 + upozornění na stejný stream (DUPLICITY_V1) – server na Odroid + zip na http://192.168.100.21:8093/kodi/
set -euo pipefail
cd ~/webcams
C="ssh -o BatchMode=yes -o ConnectTimeout=15 odroid"
$C 'cd /opt/webcams; T=$(date +%Y%m%d_%H%M%S); for f in main.py store.py static/admin.html VERZE config.json; do cp -p $f $f.pred-kodi-$T; done; echo "záloha .pred-kodi-$T"'
tar czf - main.py store.py static/admin.html VERZE kodi/script.webcams.upravy-0.1.1.zip | $C 'cd /opt/webcams && O=$(stat -c %U:%G main.py) && tar xzf - --no-same-owner && chown -R $O main.py store.py static VERZE kodi \
  && venv/bin/python -c "import json;p=\"config.json\";c=json.load(open(p));c[\"kodi_doplnek\"]=True;json.dump(c,open(p,\"w\"),ensure_ascii=False,indent=1)" \
  && ./webcams.sh stop; sleep 2; ./webcams.sh start'
sleep 12
$C 'cd /opt/webcams; ./webcams.sh status 2>&1 | tail -2; curl -s -m 15 -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8093/health; curl -s -m 10 -o /dev/null -w "kodi zip %{http_code}\n" http://127.0.0.1:8093/kodi/script.webcams.upravy-0.1.1.zip; cat VERZE'
