#!/bin/bash
# TAILSCALE_BASE_V1 → Odroid: odkazy v playlistu podle adresy, na kterou se zařízení připojilo (Tailscale 100.x / *.ts.net)
set -e
cd "$(dirname "$0")"
Z=.pred-tailscale-$(date +%Y%m%d_%H%M%S)
F="hls.py main.py playlist.py smycka.py store.py VERZE"
ssh odroid "cd /opt/webcams && for f in $F; do cp -p \$f \$f$Z; done"
scp -q $F odroid:/opt/webcams/
ssh odroid 'cd /opt/webcams && sh /opt/webcams/webcams.sh restart >/dev/null 2>&1; sleep 8
K=$(python3 -c "import json;print(json.load(open(\"config.json\"))[\"access_keys\"][0])")
echo "health: $(curl -s -m 20 http://127.0.0.1:8093/health | head -c 120)"
echo "Tailscale: $(curl -s -m 20 "http://100.71.10.97:8093/playlist.m3u?client=kodi&k=$K" | grep -m1 ^http | cut -c1-60)"
echo "domácí:    $(curl -s -m 20 "http://192.168.100.21:8093/playlist.m3u?client=kodi&k=$K" | grep -m1 ^http | cut -c1-60)"'
echo "zpět: ssh odroid 'cd /opt/webcams && for f in $F; do cp -p \$f$Z \$f; done && sh /opt/webcams/webcams.sh restart'"
