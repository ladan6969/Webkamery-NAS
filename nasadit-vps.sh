#!/bin/bash
# Webcams proxy → VPS Caoliny (84.201.4.202), 3. 11. 2026. Spouští Ladan na mojetv1: bash ~/webcams/nasadit-vps.sh
#   1) ffmpeg, uživatel webcams, /opt/webcams (kód, venv, config, kamery), systemd unit (enable, NEspouští)
#   2) nginx: location /webcams/ v api.caolina.zone (značka WEBCAMS_NGINX_V1, záloha, nginx -t; NEpřenačítá)
#   3) /etc/cron.d/webcams: kontrola 4:45, týdenní aktualizace yt-dlp (po 4:15)
# Spuštění služby a přenačtení nginx až na konci zvlášť (vypíše příkazy) – nic se nerestartuje bez souhlasu.
# Opakované spuštění je bezpečné: config.json, cams.json, state.json, náhledy a logy na VPS zůstávají.
set -euo pipefail
SRC=$(cd "$(dirname "$0")" && pwd)
KEY=$HOME/.ssh/id_ed25519_caolina_vps
H=root@84.201.4.202
K="ssh -i $KEY -o BatchMode=yes $H"
T=$(date +%Y%m%d_%H%M%S)

echo "== balík kódu"
TGZ=$(mktemp --suffix=.tgz)
tar czf "$TGZ" -C "$SRC" main.py store.py cache.py hls.py resolver.py checker.py playlist.py zeme.py smycka.py requirements.txt \
    webcams.service README.md cams.start.json static/admin.html static/login.html tools/oprava_fail_streak.py
scp -q -i "$KEY" "$TGZ" $H:/root/webcams-$T.tgz
rm -f "$TGZ"

$K "bash -s" <<REMOTE
set -euo pipefail
T=$T
echo "== 1) ffmpeg + uživatel + /opt/webcams"
command -v ffmpeg >/dev/null || DEBIAN_FRONTEND=noninteractive apt-get install -y -q ffmpeg >/dev/null
id webcams >/dev/null 2>&1 || useradd --system --home-dir /opt/webcams --shell /usr/sbin/nologin webcams
mkdir -p /opt/webcams/static /opt/webcams/thumbs /opt/webcams/logs /opt/webcams/tools
if [ -f /opt/webcams/main.py ]; then
  tar czf /root/webcams-kod-pred-\$T.tgz -C /opt/webcams --exclude=venv --exclude=logs --exclude=thumbs --exclude=__pycache__ .
  echo "   záloha předchozí verze: /root/webcams-kod-pred-\$T.tgz"
fi
tar xzf /root/webcams-\$T.tgz -C /opt/webcams
[ -f /opt/webcams/cams.json ] || cp /opt/webcams/cams.start.json /opt/webcams/cams.json
[ -x /opt/webcams/venv/bin/pip ] || { rm -rf /opt/webcams/venv; python3 -m venv /opt/webcams/venv; }   # i nedokončený venv z přerušeného běhu
/opt/webcams/venv/bin/pip install -q --upgrade pip >/dev/null
/opt/webcams/venv/bin/pip install -q -r /opt/webcams/requirements.txt
for f in /opt/webcams/*.py; do /opt/webcams/venv/bin/python -m py_compile "\$f"; done
if [ ! -f /opt/webcams/config.json ]; then
  cd /opt/webcams && venv/bin/python - <<'PY'
import json, secrets
c = {"bind": "127.0.0.1", "port": 8093, "public_base": "https://api.caolina.zone/webcams",
     "access_keys": [secrets.token_urlsafe(12)], "secret": secrets.token_hex(32), "cron_token": secrets.token_urlsafe(24),
     "admin_password_hash": "", "telegram": {"token": "", "chat_id": ""}}
json.dump(c, open("config.json", "w"), indent=1)
PY
  echo "   config.json vytvořen"
fi
chown -R webcams:webcams /opt/webcams
chmod 600 /opt/webcams/config.json
install -m 644 /opt/webcams/webcams.service /etc/systemd/system/webcams.service
systemctl daemon-reload
systemctl enable webcams.service >/dev/null 2>&1
echo "   py_compile OK, unit nainstalována (enable, NEspuštěna)"

echo "== 2) nginx"
N=/etc/nginx/sites-enabled/caolina
if grep -q WEBCAMS_NGINX_V1 \$N; then
  echo "   už je (WEBCAMS_NGINX_V1)"
else
  cp -p \$N /root/nginx-caolina.bak.\$T
  python3 - \$N <<'PY'
import sys, re
p = sys.argv[1]; s = open(p).read()
blok = '''    # WEBCAMS_NGINX_V1 (3. 11. 2026) – Webcams proxy /opt/webcams (127.0.0.1:8093)
    location ^~ /webcams/ {
        proxy_pass http://127.0.0.1:8093/;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_read_timeout 120s;
        client_max_body_size 1m;
    }
'''
# první server blok api.caolina.zone s TLS (listen 443) – za řádek server_name
m = re.search(r"server_name api\.caolina\.zone;\n", s)
assert m, "server_name api.caolina.zone nenalezen"
s = s[:m.end()] + blok + s[m.end():]
open(p, "w").write(s)
PY
  if nginx -t 2>/tmp/nginx-t.log; then echo "   vloženo, nginx -t OK (záloha /root/nginx-caolina.bak.\$T)"
  else cat /tmp/nginx-t.log; cp -p /root/nginx-caolina.bak.\$T \$N; echo "   !! nginx -t selhal – vráceno"; exit 1; fi
fi

echo "== 3) cron"
CT=\$(python3 -c "import json;print(json.load(open('/opt/webcams/config.json'))['cron_token'])")
cat > /etc/cron.d/webcams <<CRON
# Webcams proxy (/opt/webcams): denní kontrola kamer 4:45, týdenní aktualizace yt-dlp v pondělí 4:15
SHELL=/bin/bash
45 4 * * * webcams curl -s -m 30 -X POST -H "X-Webcams-Token: \$CT" http://127.0.0.1:8093/admin/api/check >> /opt/webcams/logs/cron.log 2>&1
15 4 * * 1 webcams (date +\%F; /opt/webcams/venv/bin/pip install -q -U yt-dlp; /opt/webcams/venv/bin/yt-dlp --version) >> /opt/webcams/logs/ytdlp-update.log 2>&1
CRON
chmod 644 /etc/cron.d/webcams
echo "   /etc/cron.d/webcams"
rm -f /root/webcams-\$T.tgz
REMOTE

echo
if $K "systemctl is-active --quiet webcams"; then
  echo "HOTOVO – aktualizace kódu. Služba běží se STAROU verzí, nová platí po restartu (se souhlasem):"
  echo "  $K 'systemctl restart webcams && sleep 3 && curl -s 127.0.0.1:8093/health'"
else
  echo "HOTOVO – první instalace (služba neběží, nginx nepřenačten). Další kroky:"
  echo "  a) heslo do administrace:  ssh -i $KEY $H 'cd /opt/webcams && sudo -u webcams venv/bin/python main.py --heslo-nahodne'"
  echo "     (vlastní heslo jen v terminálu s -t: ssh -t … main.py --heslo)"
  echo "  b) spustit a přenačíst:    $K 'systemctl start webcams && sleep 3 && curl -s 127.0.0.1:8093/health && nginx -t && systemctl reload nginx'"
fi
echo "  ověřit zvenku:  curl -s https://api.caolina.zone/webcams/health"
echo "  zálohy kódu: /root/webcams-kod-pred-*.tgz, nginx: /root/nginx-caolina.bak.* (jen při vložení bloku)"
