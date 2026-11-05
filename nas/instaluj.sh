#!/bin/sh
# Webkamery – instalace na Synology NAS (DS216play: armv7, 1 GB RAM, bez Dockeru). 4. 11. 2026.
# Tento skript je uvnitř samorozbalovacího souboru webcams-nas.run (nas/sestav.sh); samostatně se nespouští.
#
# Spuštění na NASu jako root – jedna z možností:
#   a) SSH:  sudo sh /volume1/<složka>/webcams-nas.run
#   b) bez SSH: Ovládací panel → Plánovač úloh → Vytvořit → Plánovaná úloha → Uživatelem definovaný skript,
#      uživatel root, skript:  sh /volume1/<složka>/webcams-nas.run   → uložit → vybrat → Spustit.
#      Průběh a výsledek je ve webcams-instalace.log vedle souboru .run (a v e-mailu, pokud ho Plánovač posílá).
#
# Volitelné proměnné: DEST (výchozí /volume1/webcams), PORT (8093), PUBLIC_BASE (adresa, na které budou kamery
# dostupné zvenku, např. http://muj-nas.synology.me:8093 – jinak se odvodí z IP NASu), BEZ_STARTU=1.
# Opakované spuštění je bezpečné: kód a knihovny se obnoví, config.json / cams.json / state.json na NASu zůstanou.

DEST=${DEST:-/volume1/webcams}
PORT=${PORT:-8093}
LOG=/volume1/webcams-instalace.log
[ -d /volume1 ] || LOG=/tmp/webcams-instalace.log
BALIK=$1                       # rozbalený obsah (předá ho samorozbalovací hlavička)

echo "=================================================================="
echo "Webkamery – instalace $(date '+%Y-%m-%d %H:%M:%S')"

CHYBY=""
chyba() { CHYBY="$CHYBY\n  ✗ $1"; echo "  ✗ $1"; }
ok()    { echo "  ✓ $1"; }

echo "== 1) kontrola NASu"
[ "$(id -u)" = 0 ] && ok "běží jako root" || chyba "musí běžet jako root (sudo / Plánovač úloh s uživatelem root)"
ok "architektura: $(uname -m)"
if [ -f /etc.defaults/VERSION ]; then
  . /etc.defaults/VERSION 2>/dev/null; ok "DSM ${productversion:-$majorversion.$minorversion} (build ${buildnumber:-?})"
else
  echo "  ! /etc.defaults/VERSION chybí – není to Synology? Pokračuji."
fi
VOLNO=$(df -m "$(dirname "$DEST")" 2>/dev/null | awk 'NR==2{print $4}')
[ "${VOLNO:-0}" -ge 300 ] && ok "volné místo ${VOLNO} MB" || chyba "málo místa na $(dirname "$DEST") (${VOLNO:-?} MB, potřeba aspoň 300 MB)"

# Python 3.10+ (yt-dlp starší nepodporuje) – SynoCommunity „Python 3.12“ / „Python 3.11“
PY=""
for p in /var/packages/python313/target/bin/python3 /var/packages/python312/target/bin/python3 \
         /var/packages/python311/target/bin/python3 /var/packages/python310/target/bin/python3 \
         /var/packages/Python3.11/target/usr/bin/python3.11 /var/packages/Python3.10/target/usr/bin/python3.10 \
         /usr/local/bin/python3 /usr/bin/python3; do
  [ -x "$p" ] || continue
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then PY=$p; break; fi
done
if [ -n "$PY" ]; then
  ok "Python: $PY ($("$PY" -V 2>&1))"
  "$PY" -c 'import venv, ensurepip' 2>/dev/null && ok "venv + pip k dispozici" \
    || chyba "Python nemá venv/ensurepip – nainstalujte „Python 3.12“ ze SynoCommunity (ne systémový python3)"
else
  chyba "chybí Python 3.10+ → Centrum balíčků → Nastavení → Zdroje balíčků → přidat SynoCommunity (https://packages.synocommunity.com) → nainstalovat „Python 3.12“ (stačí i 3.11), pak spustit instalaci znovu"
fi

# ffmpeg (SynoCommunity balík ffmpeg; systémový ffmpeg DSM je okleštěný a často chybí)
FF=""
for f in /var/packages/ffmpeg7/target/bin/ffmpeg /var/packages/ffmpeg6/target/bin/ffmpeg /var/packages/ffmpeg/target/bin/ffmpeg \
         /usr/local/bin/ffmpeg /usr/bin/ffmpeg /bin/ffmpeg; do
  [ -x "$f" ] && "$f" -version >/dev/null 2>&1 && { FF=$f; break; }
done
if [ -n "$FF" ]; then ok "ffmpeg: $FF ($("$FF" -version 2>/dev/null | head -1 | cut -c1-40))"
else chyba "chybí ffmpeg → Centrum balíčků → Nastavení → Zdroje balíčků → přidat SynoCommunity (https://packages.synocommunity.com) → nainstalovat „ffmpeg“"
fi

# internet (pip, YouTube)
if command -v curl >/dev/null 2>&1; then
  curl -s -m 15 -o /dev/null https://pypi.org/simple/fastapi/ && ok "internet (pypi.org) dostupný" || chyba "pypi.org nedostupné – NAS potřebuje internet"
fi

# port
if (netstat -tln 2>/dev/null || ss -tln 2>/dev/null) | grep -q ":$PORT "; then
  if [ -f "$DEST/webcams.pid" ] && kill -0 "$(cat "$DEST/webcams.pid")" 2>/dev/null; then ok "port $PORT drží předchozí instalace (bude restartována)"
  else chyba "port $PORT je obsazený jinou službou – spusťte s PORT=… (např. PORT=8094)"; fi
else ok "port $PORT volný"; fi

if [ -n "$CHYBY" ]; then
  printf "\nINSTALACE ZASTAVENA – chybí:%b\n\nPo nápravě spusťte stejný soubor znovu.\n" "$CHYBY"
  exit 1
fi

echo "== 2) soubory do $DEST"
mkdir -p "$DEST/logs" "$DEST/thumbs" "$DEST/static" "$DEST/tools" || exit 1
[ -f "$DEST/webcams.pid" ] && kill "$(cat "$DEST/webcams.pid")" 2>/dev/null && sleep 2
cp -f "$BALIK"/kod/*.py "$BALIK"/kod/requirements-nas.txt "$DEST/" && cp -f "$BALIK"/kod/static/* "$DEST/static/" \
  && cp -f "$BALIK"/kod/tools/* "$DEST/tools/" 2>/dev/null; ok "kód"
for f in config.json cams.json state.json; do
  if [ -f "$DEST/$f" ]; then ok "$f – ponechán stávající"
  elif [ -f "$BALIK/data/$f" ]; then cp "$BALIK/data/$f" "$DEST/$f"; ok "$f – z balíku"; fi
done

echo "== 3) knihovny (venv)"
[ -x "$DEST/venv/bin/python" ] || "$PY" -m venv "$DEST/venv" || { echo "  ✗ venv nejde vytvořit"; exit 1; }
"$DEST/venv/bin/python" -m pip install -q -U pip >/dev/null 2>&1
if "$DEST/venv/bin/pip" install -q --prefer-binary -r "$DEST/requirements-nas.txt"; then
  ok "knihovny (FastAPI + pydantic 2)"
else
  # armv7 bez hotového pydantic-core → starší FastAPI s čistě pythonovým pydantic 1 (služba žádné modely nepoužívá)
  echo "  ! pydantic 2 nejde nainstalovat (armv7), zkouším pydantic 1"
  "$DEST/venv/bin/pip" install -q --prefer-binary "fastapi==0.99.1" "pydantic<2" "uvicorn<0.30" httpx yt-dlp yt-dlp-ejs \
    && ok "knihovny (FastAPI 0.99 + pydantic 1)" || { echo "  ✗ knihovny se nenainstalovaly – viz výpis výše"; exit 1; }
fi
"$DEST/venv/bin/python" -c "import fastapi, uvicorn, httpx, yt_dlp" && ok "import knihoven v pořádku" || { echo "  ✗ import selhal"; exit 1; }

echo "== 3b) JavaScript pro YouTube"
# YouTube vyžaduje JS prostředí. x86_64/aarch64: Deno z PyPI; armv7 nebo když Deno nejde: QuickJS (pevná verze + kontrola otisku).
QJS_VER=v0.18.0
case "$(uname -m)" in
  x86_64)  QJS_ARCH=x86_64;  QJS_SHA=85ded23b9fabe8c1644fc40a8d2bbdd71157affcbd807359c618cc717ee24d7d; ZKUS_DENO=1 ;;
  aarch64) QJS_ARCH=aarch64; QJS_SHA=2c005c7171424a598d31c53c8dc4a0ae2e4958eb6fbb37908c885a307c78187c; ZKUS_DENO=1 ;;
  armv7*)  QJS_ARCH=armv7;   QJS_SHA=4bbc426083b4a31f35cbb9471ce6138a8d8db8efdfce15b596597ad60ddd79ac; ZKUS_DENO= ;;
  *)       QJS_ARCH=; QJS_SHA=; ZKUS_DENO= ;;
esac
JS_OK=
if [ -n "$ZKUS_DENO" ] && "$DEST/venv/bin/pip" install -q --prefer-binary deno 2>/dev/null \
   && "$DEST/venv/bin/deno" --version >/dev/null 2>&1; then
  JS_OK=1; ok "Deno"
elif [ -n "$ZKUS_DENO" ]; then
  "$DEST/venv/bin/pip" uninstall -q -y deno >/dev/null 2>&1
fi
if [ -z "$JS_OK" ] && [ -n "$QJS_ARCH" ]; then
  Q="$DEST/venv/bin/qjs"
  if curl -fsSL -m 120 -o "$Q.tmp" "https://github.com/quickjs-ng/quickjs/releases/download/$QJS_VER/qjs-linux-$QJS_ARCH" \
     && [ "$("$DEST/venv/bin/python" -c 'import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' "$Q.tmp")" = "$QJS_SHA" ] \
     && chmod 755 "$Q.tmp" && "$Q.tmp" -e 'console.log(1)' >/dev/null 2>&1; then
    mv -f "$Q.tmp" "$Q"; JS_OK=1; ok "QuickJS $QJS_VER ($QJS_ARCH)"
  else
    rm -f "$Q.tmp"
  fi
fi
[ -n "$JS_OK" ] || echo "  ! JS prostředí se nepodařilo připravit – část kamer z YouTube nepůjde načíst; instalace pokračuje"

echo "== 4) nastavení"
IP=$(ip -4 addr 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | grep -v '^127\.' | head -1)
PB=${PUBLIC_BASE:-http://${IP:-127.0.0.1}:$PORT}
cd "$DEST" && "$DEST/venv/bin/python" - "$PORT" "$PB" <<'PY'
import json, os, sys
port, pb = int(sys.argv[1]), sys.argv[2]
c = json.load(open("config.json")) if os.path.exists("config.json") else {}
c["bind"] = "0.0.0.0"; c["port"] = port
if not c.get("public_base") or "127.0.0.1" in c["public_base"] or not c.get("vlastni_nas") or os.environ.get("PUBLIC_BASE"):
    c["public_base"] = pb
# NAS běží samostatně → vlastní klíče, tajemství a heslo (u starší instalace se jednou vymění – značka vlastni_nas)
novy = not c.get("vlastni_nas")
if novy:
    for k in ("access_keys", "secret", "cron_token", "admin_password_hash"): c.pop(k, None)
    c["telegram"] = {"token": "", "chat_id": ""}
    c["vlastni_nas"] = 1
# slabý armv7: JS výzva trvá déle a kamery se kontrolují po menších dávkách
if os.uname().machine.startswith("armv"):
    if c.get("resolve_timeout", 30) < 120: c["resolve_timeout"] = 120
    if c.get("check_parallel", 6) > 2: c["check_parallel"] = 2
json.dump(c, open("config.json", "w"), ensure_ascii=False, indent=1)
# oprava seznamu kamer u stávající instalace (vlastní úpravy zůstávají)
if os.path.exists("cams.json"):
    k = json.load(open("cams.json")); zmena = False
    for x in k:
        if x.get("id") == "vsetin-panorama" and x.get("name") == "Vsetín – panorama":
            x["name"] = "Vsetín – světelná křižovatka"; zmena = True
    if not any(x.get("id") == "vsetin-koupaliste" for x in k):
        k.append({"id": "vsetin-koupaliste", "name": "Vsetín – koupaliště", "group": "Zlínský kraj", "country": "CZ", "category": "město",
                  "source": "https://www.youtube.com/@servissychrovnet3844", "type": "ytkanal", "match": "Vsetín - koupaliště",
                  "headers": {}, "mode": "auto", "enabled": True, "regex": "", "page": "", "note": ""}); zmena = True
    if zmena:
        json.dump(k, open("cams.json.tmp", "w"), ensure_ascii=False, indent=1); os.replace("cams.json.tmp", "cams.json")
print("  ✓ bind 0.0.0.0, port %d, public_base %s" % (port, c["public_base"]))
open(".nove_heslo", "w").write("1" if novy else "")
PY
(cd "$DEST" && venv/bin/python main.py --init >/dev/null) && ok "vlastní klíče NASu"
if [ -s "$DEST/.nove_heslo" ]; then
  H=$(cd "$DEST" && venv/bin/python main.py --heslo-nahodne | sed -n 's/^Heslo do administrace: \([^ ]*\).*/\1/p')
  K=$(cd "$DEST" && venv/bin/python -c 'import json;print(json.load(open("config.json"))["access_keys"][0])')
  [ -n "$H" ] && [ -n "$K" ] || { chyba "nové heslo / klíč se nepodařilo vytvořit"; H=""; }
  PR="${RUN_DIR:-$DEST}/webcams-PRISTUP.txt"
  printf 'Webkamery na NASu – přístup (%s)\nAdministrace: %s/admin\nHeslo: %s\nKlíč pro přehrávače: %s\n\nPo přečtení tento soubor SMAŽ. Heslo změníš v administraci nebo: %s/venv/bin/python %s/main.py --heslo\n' \
    "$(date '+%F %H:%M')" "$(cd "$DEST" && venv/bin/python -c 'import json;print(json.load(open("config.json"))["public_base"])')" "$H" "$K" "$DEST" "$DEST" > "$PR"
  chmod 600 "$PR"; [ -n "$H" ] && ok "nové heslo a klíč: $PR (přečíst a smazat)" || rm -f "$PR"
fi
rm -f "$DEST/.nove_heslo"

echo "== 5) spouštění"
cat > "$DEST/webcams.sh" <<EOF
#!/bin/sh
# start | stop | restart | status – Webkamery (instalace $(date +%F))
D=$DEST; export PATH=$(dirname "$FF"):\$PATH HOME=$DEST PYTHONUNBUFFERED=1
case "\$1" in
  start)   [ -f \$D/webcams.pid ] && kill -0 \$(cat \$D/webcams.pid) 2>/dev/null && { echo "už běží"; exit 0; }
           cd \$D && nohup venv/bin/python main.py >> logs/webcams.log 2>&1 & echo \$! > \$D/webcams.pid; echo "spuštěno" ;;
  stop)    [ -f \$D/webcams.pid ] && kill \$(cat \$D/webcams.pid) 2>/dev/null; rm -f \$D/webcams.pid; echo "zastaveno" ;;
  restart) \$0 stop; sleep 2; \$0 start ;;
  status)  [ -f \$D/webcams.pid ] && kill -0 \$(cat \$D/webcams.pid) 2>/dev/null && echo "běží (pid \$(cat \$D/webcams.pid))" || echo "neběží" ;;
  kontrola) curl -s -m 30 -X POST -H "X-Webcams-Token: \$(\$D/venv/bin/python -c 'import json;print(json.load(open("'\$D'/config.json"))["cron_token"])')" http://127.0.0.1:$PORT/admin/api/check >> \$D/logs/cron.log 2>&1 ;;
  ytdlp)   \$D/venv/bin/pip install -q -U yt-dlp yt-dlp-ejs >> \$D/logs/ytdlp-update.log 2>&1 ;;
  *) echo "použití: \$0 start|stop|restart|status|kontrola|ytdlp" ;;
esac
EOF
chmod +x "$DEST/webcams.sh"; ok "$DEST/webcams.sh (start/stop/restart/status)"
# start po zapnutí NASu: DSM spouští skripty v /usr/local/etc/rc.d při startu (DSM 6 i 7)
if [ -d /usr/local/etc/rc.d ] || mkdir -p /usr/local/etc/rc.d 2>/dev/null; then
  printf '#!/bin/sh\ncase "$1" in start) %s/webcams.sh start ;; stop) %s/webcams.sh stop ;; esac\n' "$DEST" "$DEST" > /usr/local/etc/rc.d/S99webcams.sh
  chmod 755 /usr/local/etc/rc.d/S99webcams.sh; ok "start po zapnutí NASu (/usr/local/etc/rc.d/S99webcams.sh)"
fi
# denní kontrola kamer 4:45, aktualizace yt-dlp v pondělí 4:15 (crontab DSM)
if [ -f /etc/crontab ] && ! grep -q "webcams.sh kontrola" /etc/crontab; then
  printf '45\t4\t*\t*\t*\troot\t%s/webcams.sh kontrola\n15\t4\t*\t*\t1\troot\t%s/webcams.sh ytdlp\n' "$DEST" "$DEST" >> /etc/crontab
  (systemctl restart crond 2>/dev/null || synoservicectl --restart crond 2>/dev/null || true); ok "plán: kontrola 4:45, yt-dlp po 4:15"
fi

if [ -z "$BEZ_STARTU" ]; then
  "$DEST/webcams.sh" restart >/dev/null; sleep 6
  if curl -s -m 10 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/health" | grep -q 200; then ok "služba běží: http://${IP:-NAS}:$PORT/admin"
  else echo "  ✗ služba neodpovídá – konec logu:"; tail -20 "$DEST/logs/webcams.log"; exit 1; fi
fi
echo "HOTOVO. Zvenku: v routeru přesměrovat port $PORT na ${IP:-NAS}, PUBLIC_BASE pak nastavit na veřejnou adresu (spustit znovu s PUBLIC_BASE=…)."
