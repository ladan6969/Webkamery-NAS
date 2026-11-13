#!/bin/bash
# 13. 11. 2026 3:05 (jednorázově z cronu mojetv1): ověří, že po noční OTA (2:10) běží na card1 a Odroidu časosběr ČHMÚ
# (CASOSBER_V1, VERZE 2026.11.12.2, config „casosber“). Časosběr = segment 3 snímky/s, obyčejný snímek = 2 snímky/s.
# Výsledek: ~/webcams/logs/casosber-overeni.log. Po spuštění smaže svůj řádek z crontabu.
LOG=~/webcams/logs/casosber-overeni.log
mkdir -p ~/webcams/logs
exec >> "$LOG" 2>&1
echo "=== $(date '+%F %T')"
for H in oscam-card1 odroid; do
  ssh -o BatchMode=yes -o ConnectTimeout=20 $H 'bash -s' <<'E' | sed "s/^/$H: /"
S="sudo -n"; [ "$(id -u)" = 0 ] && S=""
cd /opt/webcams || exit
echo "VERZE $(cat VERZE), kód $(grep -c CASOSBER_V1 smycka.py) (1 = časosběr), praděd v seznamu $(grep -c '"chmi-praded"' cams.json)"
K=$($S python3 -c 'import json;print(json.load(open("/opt/webcams/config.json"))["access_keys"][0])')
for c in chmi-praded chmi-cheb chmi-lysa-hora1; do
  M=$(curl -s -m 180 "http://127.0.0.1:8093/cam/$c?client=kodi&k=$K")
  U=$(echo "$M" | grep -m1 '/smycka/' | sed -E 's#^https?://[^/]+#http://127.0.0.1:8093#')
  if [ -z "$U" ]; then echo "$c CHYBA: $(echo "$M" | head -c 200)"; continue; fi
  curl -s -m 60 -o /tmp/casosber-test.ts "$U"
  F=$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 /tmp/casosber-test.ts 2>/dev/null \
      || $S docker exec webcams ffprobe -v error -show_entries stream=r_frame_rate -of csv=p=0 - < /tmp/casosber-test.ts 2>/dev/null); F=$(echo "$F" | grep -m1 /)
  case "$F" in 3/1) echo "$c OK časosběr";; 2/1) echo "$c jen snímek (časosběr neběží)";; *) echo "$c ? ($F)";; esac
done
rm -f /tmp/casosber-test.ts
grep -h "časosběr" logs/*.log 2>/dev/null | tail -3
E
done
crontab -l | grep -v 'over_casosber.sh' | crontab -
