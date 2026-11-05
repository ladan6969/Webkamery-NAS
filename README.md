# Webcams proxy (/opt/webcams na VPS Caoliny)

Stálé adresy živých webkamer. V playlistu je `…/cam/<id>`, aktuální stream (s tokenem, který druhý den vyprší)
server zjistí až při přehrání. Jednou denně ověří, které kamery jdou.

- Služba: `webcams.service` (uživatel `webcams`), FastAPI + uvicorn ve `venv`, poslouchá jen `127.0.0.1:8093`.
- Ven: nginx `https://api.caolina.zone/webcams/…` (blok `WEBCAMS_NGINX_V1` v `/etc/nginx/sites-enabled/caolina`).
- Logy: `logs/webcams.log` (aplikace), `logs/service.log`, `logs/cron.log`, `logs/ytdlp-update.log`.
- Soubory: `config.json` (klíče, heslo, Telegram), `cams.json` (kamery), `state.json` (stav kontrol, SID), `thumbs/`.

## Přístup
- **Administrace** `https://api.caolina.zone/webcams/admin/` – heslo (`sudo -u webcams venv/bin/python main.py --heslo`, pak restart).
- **Přehrávače** – klíč v adrese `?k=…` (`config.json → access_keys`, víc klíčů = víc lidí, klíč jde zrušit). Odkazy s klíčem jsou nahoře v administraci.
- Cron – hlavička `X-Webcams-Token` (`config.json → cron_token`).

## Přidat kameru
Administrace → **＋ Přidat kameru** → do „Zdroj“ vlož adresu → **Otestovat** (resolve, živost, ffprobe, náhled – nic se neukládá) → vyplň ID, název, skupinu → **Uložit**.

| type | zdroj |
|---|---|
| `auto` | zkusí direct → ytdlp → regex (použitý typ se ukáže v tabulce) |
| `direct` | přímé m3u8 bez tokenu |
| `ytdlp` | YouTube (**odkaz na živé vysílání kanálu** `https://www.youtube.com/@kanal/live`, ne video), Twitch, SkylineWebcams… – bere se jen HLS |
| `regex` | m3u8 se vytáhne ze stránky (vlastní výraz v poli Regex, jinak výchozí `…\.m3u8…`) |

`mode`: `redirect` (302 na zdroj), `proxy` (server stahuje playlist i segmenty s hlavičkami zdroje), `auto` (bez hlaviček redirect, s hlavičkami proxy).
Hlavičky (Referer, Origin, User-Agent) jako JSON, např. `{"Referer": "https://www.example.com/"}`.

## Endpointy (všechny kromě /health s `?k=`)
| | |
|---|---|
| `GET /cam/<id>?client=kodi\|vlc\|e2` | Kodi/VLC: 302 na stream (nebo proxy). Enigma2: zkrácený master jen s nejvyšší variantou a audio stopou, URI absolutní (gstreamer by z plného masteru vzal nejnižší kvalitu) |
| `GET /playlist.m3u?client=kodi\|vlc\|e2` | M3U s `url-tvg`, `group-title`, logem (náhled); Kodi s `#KODIPROP:inputstream.adaptive` |
| `GET /bouquet.tv` | Enigma2 bouquet „Webkamery“ (SREF 4097, stabilní SID 0xA000+, skupiny jako markery) |
| `GET /epg.xml` | XMLTV: „🔴 Živě – název“ na dnes a zítra |
| `GET /thumb/<id>.jpg` | poslední náhled (jinak zástupný obrázek) |
| `GET /health` | počty, verze yt-dlp, poslední kontrola |
| `/admin/` | správa (tabulka, přidat/upravit/vypnout/smazat, test jedné, kontrola všech) |

## Zapojení
- **Kodi** – PVR IPTV Simple Client: M3U `…/playlist.m3u?client=kodi&k=…`, EPG `…/epg.xml?k=…` (nebo nechat url-tvg).
- **VLC** – Otevřít síťový proud `…/playlist.m3u?client=vlc&k=…`.
- **Enigma2** – stáhnout bouquet do přijímače:
  `wget -O /etc/enigma2/userbouquet.webkamery.tv "https://api.caolina.zone/webcams/bouquet.tv?k=…"`,
  do `/etc/enigma2/bouquets.tv` přidat řádek `#SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.webkamery.tv" ORDER BY bouquet`
  a znovu načíst seznam kanálů (nebo restart GUI).

## Cache a kontrola
- Cache stream URL: platnost z URL (YouTube `expire`, JWT `exp`, Akamai `hdnts exp`) − 5 min, nejvýš 4 h, jinak 30 min;
  po 60 min lehké ověření (401/403/404/410 → nový resolve). Zámek na kameru – souběžné požadavky = jeden resolve.
- Kontrola 4:45 (`/etc/cron.d/webcams`), postupně s pauzou 2,5 s: resolve → živost (bez ENDLIST, posun MEDIA-SEQUENCE za 15 s) →
  ffprobe → náhled. Po 3 neúspěšných dnech v řadě se kamera skryje z playlistu, bouquetu a EPG (z cams.json se nemaže), po úspěchu se vrátí.
- yt-dlp se aktualizuje v pondělí 4:15 (log `logs/ytdlp-update.log`).

## Další fáze (místo nechané v kódu)
- Sniffer (headless Chromium) – další typ v `resolver.RESOLVERS`, v administraci „najdi stream na stránce“.
- Vyhledávač kandidátů (YouTube Data API, agregátory, iptv-org) – záložka Kandidáti se schválením.
- Klienti: položka Webkamery v Caolině (Kodi, Enigma2, APK) – místo `?k=` ověření účtem Caoliny.
