# Webkamery na Synology NAS

Služba pro živé webkamery se stálými adresami. Běží samostatně na NASu a dává playlisty pro **Kodi**, **VLC** a **Enigma2**. V repozitáři je seznam přibližně 5 200 kamer.

Adresy streamů často obsahují token, který brzy vyprší. V playlistu je proto stálá adresa `/cam/<id>` a aktuální stream služba zjistí až při přehrání. Jednou denně také ověří, které kamery fungují.

Odzkoušeno na **Synology DS216play** (armv7, 1 GB RAM, bez Dockeru). Docker není potřeba.

## Obsah

1. [Co je potřeba](#1-co-je-potřeba)
2. [Příprava NASu](#2-příprava-nasu)
3. [Stažení](#3-stažení)
4. [Instalace](#4-instalace)
5. [První přihlášení](#5-první-přihlášení)
6. [Přehrávače](#6-přehrávače)
7. [Přístup mimo domácí síť](#7-přístup-mimo-domácí-síť)
8. [Údržba](#8-údržba)
9. [Řešení potíží](#9-řešení-potíží)
10. [Přidání kamery](#10-přidání-kamery)
11. [Aktualizace a odinstalace](#11-aktualizace-a-odinstalace)

## 1. Co je potřeba

- Synology NAS s DSM a přístupem správce
- aspoň 300 MB volného místa
- internet na NASu
- volný port **8093**

Kvůli YouTube instalátor doplní také JavaScriptové prostředí: Deno na x86_64 a aarch64, QuickJS na armv7. Stáhne ho sám.

Instalátor všechno ověří sám. Když něco chybí, vypíše co a skončí bez instalace.

## 2. Příprava NASu

1. Otevři **Centrum balíčků → Nastavení → Zdroje balíčků → Přidat**.
2. Přidej SynoCommunity s adresou `https://packages.synocommunity.com`.
3. V Centru balíčků nainstaluj **Python 3.12** (stačí i 3.11).
4. Nainstaluj také **ffmpeg**.

Systémový Python nepoužívej. Starší verze nestačí pro yt-dlp a často v něm chybí `venv`.

## 3. Stažení

1. Na GitHubu zvol **Code → Download ZIP**.
2. Ve File Station nahraj `Webkamery-NAS-main.zip`, například do složky `public`.
3. Klikni na soubor pravým tlačítkem a zvol **Extrahovat → Extrahovat sem**.

Vznikne složka, například `/volume1/public/Webkamery-NAS-main`. Tuto cestu použij v dalším kroku. Přesnou cestu najdeš ve File Station ve **Vlastnostech** složky.

## 4. Instalace

Instalace běží několik minut, na slabém NASu i déle. Vyber jeden ze dvou způsobů.

### Bez SSH: Plánovač úloh

1. Otevři **Ovládací panel → Plánovač úloh → Vytvořit → Plánovaná úloha → Uživatelem definovaný skript**.
2. Na kartě **Obecné** nastav uživatele na **root** a vypni **Povoleno**, aby se úloha neopakovala.
3. Na kartě **Nastavení úlohy** vlož podle své cesty:

   ```sh
   cd /volume1/public/Webkamery-NAS-main && bash nas/sestav.sh && sh nas/webcams-nas.run
   ```

4. Ulož úlohu, vyber ji a klikni na **Spustit**.
5. Po dokončení otevři ve File Station `nas/webcams-instalace.log`. Na konci musí být řádek začínající `HOTOVO`.

### Přes SSH

SSH zapneš v **Ovládací panel → Terminál a SNMP**.

```sh
cd /volume1/public/Webkamery-NAS-main
bash nas/sestav.sh
sudo sh nas/webcams-nas.run
```

### Co instalace vytvoří

| Co | Kde |
|---|---|
| Program a data | `/volume1/webcams` |
| Ovládací skript | `/volume1/webcams/webcams.sh` |
| Start po zapnutí NASu | `/usr/local/etc/rc.d/S99webcams.sh` |
| Denní kontrola kamer ve 4:45 | `/etc/crontab` |
| Aktualizace yt-dlp v pondělí ve 4:15 | `/etc/crontab` |
| Přístupové údaje | `nas/webcams-PRISTUP.txt` |

Jiný port nebo složku nastavíš při spuštění, například `PORT=8094 DEST=/volume1/kamery sh nas/webcams-nas.run`. Se `sudo` napiš proměnné až za něj: `sudo PORT=8094 sh nas/webcams-nas.run`.

## 5. První přihlášení

1. Otevři `nas/webcams-PRISTUP.txt`. Najdeš v něm adresu administrace, heslo a klíč pro přehrávače.
2. Přihlas se na `http://IP-NASU:8093/admin`.
3. Údaje si bezpečně ulož a soubor **smaž**.
4. Hotové odkazy s klíčem jsou nahoře v administraci.
5. Ověř běh na `http://IP-NASU:8093/health`, kde se zobrazí počty kamer.

Klíč v odkazech nikam nezveřejňuj. Kdo ho má, může kamery přehrávat přes tvůj NAS.

Instalace přebírá kamery bez výsledku kontroly. Všechny tedy nejdřív uvidíš v playlistu, a nefunkční se skryjí až po 3 neúspěšných denních kontrolách. Po úspěšné kontrole se kamera zase vrátí. První kontrolu můžeš spustit hned v administraci, ale u tisíců kamer může trvat hodiny.

## 6. Přehrávače

Odkazy vždy kopíruj z administrace svého NASu.

### Kodi

1. Nainstaluj doplňky **PVR IPTV Simple Client** a **InputStream Adaptive**.
2. V nastavení Simple Clienta vlož jeden z odkazů níže jako vzdálený M3U playlist.
3. Restartuj Kodi a otevři sekci **TV**.

| Odkaz v administraci | Skupiny kanálů |
|---|---|
| Playlist Kodi | podle zemí, Česko nahoře |
| Kodi – podle zaměření (celý svět) | podle tématu |
| Kodi – Česko podle krajů | české kamery podle krajů |

Vlož jen jeden odkaz, jinak vzniknou duplicitní kanály. EPG není nutné zadávat, adresa je v playlistu. První načtení tisíců kanálů může na slabším zařízení trvat déle.

### VLC

Zvol **Média → Otevřít síťový proud** a vlož odkaz **Playlist VLC**.

### Enigma2

> **Pozor:** bouquet se všemi kamerami může rozbít seznam bouquetů. Bouquet s 5 177 kamerami to na GigaBlue udělal. Nejdřív si zálohuj nastavení přijímače, nebo si přes administraci nech zapnutý jen menší výběr kamer.

1. Na přijímači stáhni bouquet. Odkaz vezmi z administrace, položka **Bouquet Enigma2** nebo **Bouquet – podle zaměření**:

   ```sh
   wget -O /etc/enigma2/userbouquet.webkamery.tv "ODKAZ-Z-ADMINISTRACE"
   ```

2. Do `/etc/enigma2/bouquets.tv` přidej řádek:

   ```
   #SERVICE 1:7:1:0:0:0:0:0:0:0:FROM BOUQUET "userbouquet.webkamery.tv" ORDER BY bouquet
   ```

3. Znovu načti seznam kanálů nebo restartuj GUI.

Bouquet je statický soubor, nové kamery i změna adresy NASu se projeví až po novém stažení. EPG v něm není.

## 7. Přístup mimo domácí síť

Doma nic dalšího nenastavuj, pokud je přehrávač ve stejné síti jako NAS.

**Riziko:** spojení jde přes nešifrované HTTP a přesměrování otevře do internetu i administraci. Heslo a klíč by šlo po cestě odečíst. Bezpečnější je VPN.

1. Na routeru přesměruj port **8093** na IP adresu NASu.
2. Spusť instalaci znovu s veřejnou adresou. Klíče, heslo a kamery zůstanou:

   ```sh
   sudo PUBLIC_BASE=http://VEREJNA-ADRESA:8093 sh nas/webcams-nas.run
   ```

   V Plánovači úloh stejný příkaz napiš bez `sudo`.
3. V přehrávačích znovu načti playlist a bouquet stáhni znovu.

Veřejná adresa se zapisuje do playlistu ke každé kameře, proto ji přehrávač musí mít dostupnou.

## 8. Údržba

Příkazy spouštěj na NASu jako root.

| Co chceš udělat | Příkaz |
|---|---|
| Zjistit stav | `/volume1/webcams/webcams.sh status` |
| Spustit nebo zastavit | `/volume1/webcams/webcams.sh start` nebo `stop` |
| Restartovat | `/volume1/webcams/webcams.sh restart` |
| Zkontrolovat kamery hned | `/volume1/webcams/webcams.sh kontrola` |
| Aktualizovat yt-dlp | `/volume1/webcams/webcams.sh ytdlp` |
| Změnit heslo administrace | `cd /volume1/webcams && venv/bin/python main.py --heslo`, pak restart |
| Zobrazit log | `tail -50 /volume1/webcams/logs/webcams.log` |

## 9. Řešení potíží

| Potíž | Co zkusit |
|---|---|
| Instalace se zastavila | Na konci `nas/webcams-instalace.log` je napsáno, co chybí. Po nápravě spusť instalaci znovu. |
| Chybí Python nebo `venv` | Nainstaluj Python 3.12 ze SynoCommunity podle kroku 2. |
| Port 8093 je obsazený | Spusť instalaci s `PORT=8094`. |
| `/health` neodpovídá | Ověř `status`, spusť `restart` a podívej se do logu. |
| Doma to jde, zvenku ne | Zkontroluj přesměrování portu a krok 7. |
| Kamera nejde přehrát | Otestuj ji v administraci, zdroj může být dočasně mimo provoz. |
| YouTube kamery přestaly jít | Spusť `ytdlp` a pak `restart`. Instalační log musí v kroku 3b hlásit Deno nebo QuickJS, jinak spusť instalaci znovu. |
| Kamera z YouTube se pouští dlouho | Na armv7 může načtení trvat i desítky sekund, QuickJS je pomalý. |

## 10. Přidání kamery

V administraci zvol **＋ Přidat kameru**, do pole **Zdroj** vlož adresu a klikni na **Otestovat**. Pak vyplň ID, název a skupinu a **Ulož**.

| Typ | Zdroj |
|---|---|
| `auto` | zkusí `direct`, `ytdlp` a `regex` |
| `direct` | přímý odkaz m3u8 bez tokenu |
| `ytdlp` | YouTube (odkaz `https://www.youtube.com/@kanal/live`), Twitch a další, jen HLS |
| `regex` | m3u8 ze stránky, podle vlastního výrazu nebo výchozího |

Režim přehrávání:

| Režim | Chování |
|---|---|
| `redirect` | přehrávač jde přímo na zdroj a video přes NAS neteče |
| `proxy` | playlist i video jdou přes NAS, který tak víc zatěžují |
| `auto` | bez hlaviček `redirect`, s hlavičkami `proxy` |

Hlavičky se zadávají jako JSON, například `{"Referer": "https://www.example.com/"}`.

## 11. Aktualizace a odinstalace

**Aktualizace:** stáhni a rozbal nový ZIP podle kroku 3 a znovu spusť krok 4. Nastavení, klíče, heslo, kamery a stav kontrol zůstanou. Seznam kamer z nového ZIPu se tím nepřepíše.

**Odinstalace jako root:**

```sh
/volume1/webcams/webcams.sh stop
rm -f /usr/local/etc/rc.d/S99webcams.sh
sed -i '/webcams\.sh/d' /etc/crontab
rm -rf /volume1/webcams
```
