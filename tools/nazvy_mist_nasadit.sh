#!/bin/bash
# Přejmenování kamer bez místa na VPS podle tools/nazvy_mist.json (4. 11. 2026) – záloha cams.json do /root, služba běží dál
# (cams.json se čte při každém požadavku). Spuštění: bash ~/webcams/tools/nazvy_mist_nasadit.sh
set -e
KEY=$HOME/.ssh/id_ed25519_caolina_vps; H=root@84.201.4.202
scp -q -i $KEY ~/webcams/tools/nazvy_mist.json $H:/tmp/nazvy_mist.json
ssh -i $KEY -o BatchMode=yes $H 'cd /opt/webcams && T=$(date +%Y%m%d_%H%M) && cp -p cams.json /root/webcams-cams.json.pred-nazvy-$T && sudo -u webcams venv/bin/python -c "
import json,store
m=json.load(open(\"/tmp/nazvy_mist.json\")); lst=store.load(\"cams.json\",[]); n=0
for c in lst:
    if c.get(\"id\") in m: c[\"name\"]=m[c[\"id\"]]; n+=1
store.save_cams(lst); print(\"přejmenováno\",n)
" && rm -f /tmp/nazvy_mist.json && echo "vrácení: cp /root/webcams-cams.json.pred-nazvy-$T /opt/webcams/cams.json"'
