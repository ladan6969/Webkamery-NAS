#!/bin/bash
# Sestaví jediný instalační soubor pro Synology NAS: ~/webcams/nas/webcams-nas.run (4. 11. 2026)
# Obsah: kód služby + aktuální cams.json, state.json z VPS (bez config.json – vlastní klíče na NASu) (bez náhledů – vytvoří se znovu) + instaluj.sh.
# Spuštění (na mojetv1):  bash ~/webcams/nas/sestav.sh
set -euo pipefail
N=$(cd "$(dirname "$0")" && pwd); W=$(dirname "$N"); T=$(mktemp -d); trap 'rm -rf $T' EXIT
mkdir -p $T/kod/static $T/kod/tools $T/data
cp $W/{main,store,cache,hls,resolver,checker,playlist,zeme,smycka}.py $T/kod/
cp $W/static/{admin.html,login.html} $T/kod/static/ 2>/dev/null || true
cp $W/tools/{oprava_fail_streak.py,nazvy_mist.py} $T/kod/tools/ 2>/dev/null || true
cp $N/requirements-nas.txt $T/kod/; cp $N/instaluj.sh $T/
for f in cams.json state.json; do   # config.json NE – NAS si vytvoří vlastní klíče a heslo (samostatný provoz)
  scp -q -i $HOME/.ssh/id_ed25519_caolina_vps root@84.201.4.202:/opt/webcams/$f $T/data/$f
done
OUT=$N/webcams-nas.run
cat > $OUT <<'HLAVA'
#!/bin/sh
# Webkamery Caoliny – instalace na Synology NAS. Spustit jako root:  sudo sh webcams-nas.run
# (nebo v DSM: Plánovač úloh → Uživatelem definovaný skript, uživatel root: sh /volume1/…/webcams-nas.run)
# log vedle .run (4. 11. 2026: kořen /volume1 není ve File Station vidět)
LOG="$(cd "$(dirname "$0")" && pwd)/webcams-instalace.log"; touch "$LOG" 2>/dev/null || LOG=/tmp/webcams-instalace.log
T=$(mktemp -d /tmp/webcams.XXXXXX) || exit 1
sed '1,/^__BALIK__$/d' "$0" | tar xzf - -C "$T" || { echo "balík je poškozený"; exit 1; }
RUN_DIR="$(cd "$(dirname "$0")" && pwd)" sh "$T/instaluj.sh" "$T" 2>&1 | tee -a "$LOG"
RC=$(tail -1 "$LOG" | grep -q '^HOTOVO' && echo 0 || echo 1)
rm -rf "$T"; exit $RC
__BALIK__
HLAVA
tar czf - -C $T kod data instaluj.sh >> $OUT
chmod +x $OUT
echo "$OUT  $(du -h $OUT | cut -f1)  sha256 $(sha256sum $OUT | cut -c1-16)"
