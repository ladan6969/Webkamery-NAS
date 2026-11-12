#!/bin/bash
# 11. 11. 2026: YouTube kamery v Kodi/E2 od živého konce (/oriz – jen posledních ~30 s místo hodiny záznamu).
# Spouští uživatel na mojetv1. NAS a Odroid si to vezmou nočním OTA (VERZE 2026.11.11.1).
set -euo pipefail
cd ~/webcams
C="ssh -o BatchMode=yes card1.selfip.info"
$C 'cd /opt/webcams; T=$(date +%Y%m%d_%H%M%S); for f in main.py hls.py VERZE; do cp -p $f $f.pred-oriz-$T; done; echo "záloha .pred-oriz-$T"'
tar czf - main.py hls.py VERZE | $C 'cd /opt/webcams && tar xzf - --no-same-owner && chown webcams:webcams main.py hls.py VERZE && systemctl restart webcams'
sleep 12
$C 'systemctl is-active webcams; curl -s -m 10 -o /dev/null -w "health %{http_code}\n" http://127.0.0.1:8093/health'
