#!/bin/bash
# 11. 11. 2026: oprava YouTube od živého konce (/oriz) hned na Odroid (jinak noční OTA). Spouští uživatel na mojetv1.
set -euo pipefail
cd ~/webcams
C="ssh -o BatchMode=yes -o ConnectTimeout=15 odroid"
$C 'cd /opt/webcams; T=$(date +%Y%m%d_%H%M%S); for f in main.py hls.py VERZE; do cp -p $f $f.pred-oriz-$T; done; echo "záloha .pred-oriz-$T"'
tar czf - main.py hls.py VERZE | $C 'cd /opt/webcams && O=$(stat -c %U:%G main.py) && tar xzf - --no-same-owner && chown $O main.py hls.py VERZE && ./webcams.sh stop; sleep 2; ./webcams.sh start'
sleep 12
$C 'cd /opt/webcams; ./webcams.sh status 2>&1 | tail -2; curl -s -m 15 -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8093/health; cat VERZE'
