#!/bin/bash
# 15. 11. 2026 HOLIDAYINFO_SNIMEK_V1: kamery holidayinfo jen s obrázkem (Kyčerka) jako snímková smyčka + výběr všech kamer
# střediska a „Přidat všechny nové“ ve správě. Hned na Odroid (jinak noční OTA). Spouští uživatel na mojetv1.
set -euo pipefail
cd ~/webcams
C="ssh -o BatchMode=yes -o ConnectTimeout=15 odroid"
F="main.py resolver.py checker.py VERZE static/admin.html"
$C 'cd /opt/webcams; T=$(date +%Y%m%d_%H%M%S); for f in main.py resolver.py checker.py VERZE static/admin.html; do cp -p $f $f.pred-hisnimek-$T; done; echo "záloha *.pred-hisnimek-$T"'
tar czf - $F | $C 'cd /opt/webcams && O=$(stat -c %U:%G main.py) && tar xzf - --no-same-owner && chown $O main.py resolver.py checker.py VERZE static/admin.html && ./webcams.sh stop; sleep 2; ./webcams.sh start'
sleep 12
$C 'cd /opt/webcams; ./webcams.sh status 2>&1 | tail -2; curl -s -m 15 http://127.0.0.1:8093/health | grep -o "\"kod\":\"[^\"]*\""'
