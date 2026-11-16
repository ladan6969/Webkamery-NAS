#!/bin/bash
# 15. 11. 2026 ZOBRAZENI_V1: u snímkové kamery volba Zobrazení snímku – automaticky / animace / časosběr / stojící obraz
# střediska a „Přidat všechny nové“ ve správě. Hned na Odroid (jinak noční OTA). Spouští uživatel na mojetv1.
set -euo pipefail
cd ~/webcams
C="ssh -o BatchMode=yes -o ConnectTimeout=15 odroid"
F="smycka.py main.py store.py VERZE static/admin.html"
$C 'cd /opt/webcams; T=$(date +%Y%m%d_%H%M%S); for f in smycka.py main.py store.py VERZE static/admin.html; do cp -p $f $f.pred-zobrazeni-$T; done; echo "záloha *.pred-zobrazeni-$T"'
tar czf - $F | $C 'cd /opt/webcams && O=$(stat -c %U:%G main.py) && tar xzf - --no-same-owner && chown $O smycka.py main.py store.py VERZE static/admin.html && ./webcams.sh stop; sleep 2; ./webcams.sh start'
sleep 12
$C 'cd /opt/webcams; ./webcams.sh status 2>&1 | tail -2; curl -s -m 15 http://127.0.0.1:8093/health | grep -o "\"kod\":\"[^\"]*\""'
