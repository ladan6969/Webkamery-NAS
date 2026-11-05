"""Velký sběr kamer (3. 11. 2026): YouTube kanály (všechny živé přenosy, typ ytkanal) + katalog SkylineWebcams (typ skyline).
Šetrně: postupně, s pauzami. Výstup tools/sber2.json (formát cams.json). Ověření pak dělá kontrola ve službě.
Spuštění: cd ~/webcams && venv/bin/python tools/sber2.py
"""
import asyncio
import json
import os
import re
import sys
import unicodedata
from urllib.parse import quote

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import resolver  # noqa: E402
import store     # noqa: E402
import zeme      # noqa: E402

D = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"

KANALY_JMENA = ["@earthcam", "@VirtualRailfan", "@ExploreLiveNatureCams", "@explorebirds", "@ExploreBears", "@africam",
                "@NamibiaCam", "@CornellLabBirdCams", "@SlowTV", "@livespotting", "@SeeJacksonHole", "@Railcam",
                "@iloveyouvenice", "@WebcamItalia", "@SkylineWebcams", "@teleportcamera", "@FlightRadar24",
                "@AirlineVideosLive", "@worldlivecams", "@ipcamlive", "@afarTV", "@PTZtv", "@LiveCamsPro"]
BLACKLIST_KANAL = re.compile(r"feratel|webcamera\.pl|earthtv|cam nomad|vatican|serene dream|japan walk|tvl trzcianka|"
                             r"radio|music|lofi|news|český rozhlas|ann|fnn", re.I)
HLEDANI = [
    "live webcam", "webcam 24/7", "livecam", "live cam city", "harbor live cam", "airport live cam", "ski resort live cam",
    "beach live cam", "zoo live cam", "bird feeder live", "nest cam live", "waterhole live", "railway live cam", "train station live",
    "volcano live cam", "aquarium live cam", "port ship live", "bridge live cam", "street live cam", "town square live cam",
    "česko živě kamera", "slovensko webkamera", "polska kamera na żywo", "deutschland live webcam", "österreich webcam live",
    "schweiz webcam live", "italia webcam live", "españa cámara en vivo", "france webcam en direct", "nederland live webcam",
    "uk live webcam", "ireland live cam", "norway live cam", "sweden live webcam", "finland live webcam", "denmark live cam",
    "iceland live cam", "croatia webcam live", "slovenia webcam", "hungary live webcam", "greece live cam", "turkey canlı kamera",
    "portugal webcam live", "malta live cam", "cyprus live cam", "usa live cam", "new york live cam", "california live cam",
    "florida live cam", "hawaii live cam", "alaska live cam", "canada live cam", "mexico cámara en vivo", "brazil ao vivo câmera",
    "argentina cámara en vivo", "japan live camera", "ライブカメラ", "korea live cam", "thailand live cam", "bali live cam",
    "australia live cam", "new zealand live cam", "south africa live cam", "kenya live cam", "dubai live cam", "israel live cam",
    "caribbean live cam", "las vegas live", "chicago live cam", "tokyo live camera", "osaka live camera",
]
POZITIVNI = re.compile(r"cam|webcam|live|view|kamera|camera|kamer|câmera|cámara|station|nádraží|airport|letiště|harbou?r|port|hafen|"
                       r"beach|pláž|bay|street|square|náměstí|platz|piazza|zoo|nest|feeder|waterhole|river|lake|mountain|ski|"
                       r"rail|train|bridge|panorama|ライブ|カメラ", re.I)
NEGATIVNI = re.compile(r"(\d{3,}\s*(top|best)|around the world|music|radio|lofi|relax(ing)? music|compilation|replay|highlights|"
                       r"\bnews\b|best of|\bvs\b|football|soccer|game|gaming|minecraft|fortnite|pray|church|mass\b|asmr|podcast|"
                       r"talk show|q&a|tutorial|rain sounds|sleep|study|chat|subathon|irl stream|bingo|casino|trading|crypto)", re.I)

# země podle názvu přenosu: klíčová slova → kód (pořadí: konkrétní města před obecnými)
KLICE = [
    (r"praha|prague|brno|česk|czech|nádraží|nadrazi|ústí|ostrava|plzeň|plzen|krkonoš|šumav|liberec|olomouc", "CZ"), (r"bratislava|slovakia|slovensk|tatry|košice", "SK"),
    (r"polsk|poland|kraków|krakow|warszaw|zakopane|gdańsk|wrocław", "PL"), (r"wien|vienna|austria|österreich|tirol|salzburg|zillertal|innsbruck", "AT"),
    (r"berlin|hamburg|münchen|munich|deutschland|germany|köln|cologne|frankfurt|dresden|bayern|rügen|sylt", "DE"),
    (r"zürich|zurich|switzerland|schweiz|suisse|zermatt|matterhorn|luzern|lucerne|grindelwald|jungfrau|davos|st\.? moritz|grimentz", "CH"),
    (r"venezia|venice italy|roma|rome|milano|milan|napoli|naples|firenze|florence|italia|italy|sicilia|sicily|dolomit|sardegna|amalfi|positano|como", "IT"),
    (r"paris|france|nice|marseille|lyon|chamonix|côte|bretagne|normandie", "FR"), (r"madrid|barcelona|españa|spain|tenerife|mallorca|ibiza|canarias|canary|benidorm|lanzarote|gran canaria|málaga|valencia", "ES"),
    (r"lisboa|lisbon|porto|portugal|madeira|algarve|azores|açores", "PT"), (r"amsterdam|netherlands|nederland|rotterdam|den haag|utrecht", "NL"),
    (r"bruxelles|brussels|belgi|antwerp|brugge|bruges", "BE"), (r"london|england|scotland|wales|united kingdom|\buk\b|edinburgh|liverpool|manchester|brighton|heathrow|abbey road", "GB"),
    (r"dublin|ireland|galway|cork", "IE"), (r"iceland|ísland|reykjav|fagradals", "IS"), (r"norway|norge|oslo|bergen|lofoten|tromsø|tromso|lyngen", "NO"),
    (r"sweden|sverige|stockholm|göteborg|malmö", "SE"), (r"finland|suomi|helsinki|tampere|lapland|rovaniemi", "FI"), (r"denmark|danmark|københavn|copenhagen", "DK"),
    (r"croatia|hrvatska|split|dubrovnik|zagreb|hvar|zadar|rovinj|pula|makarska|brela|krk\b|murter|rogoznica|istria", "HR"),
    (r"sloven|ljubljana|bled|piran", "SI"), (r"budapest|hungary|magyar|balaton", "HU"), (r"greece|ελλάδα|athens|santorini|crete|corfu|mykonos|rhodes|zakynthos|thessaloniki", "GR"),
    (r"türkiye|turkey|istanbul|antalya|izmir|bodrum", "TR"), (r"malta|valletta", "MT"), (r"cyprus|limassol|larnaca|paphos|ayia napa", "CY"),
    (r"bulgaria|sofia|varna|burgas", "BG"), (r"romania|bucureşti|bucharest|brașov", "RO"), (r"montenegro|budva|kotor", "ME"), (r"serbia|beograd|belgrade", "RS"),
    (r"new york|nyc|times square|manhattan|brooklyn|california|los angeles|san francisco|san diego|florida|miami|key west|orlando|tampa|"
     r"las vegas|chicago|texas|hawaii|honolulu|maui|alaska|katmai|yellowstone|wyoming|colorado|new orleans|washington|boston|seattle|"
     r"philadelphia|baltimore|nashville|atlanta|arizona|utah|montana|iowa|ohio|michigan|virginia|carolina|maine|usa|\bus\b|"
     r", (al|ak|az|ar|ca|co|ct|de|fl|ga|hi|id|il|in|ia|ks|ky|la|me|md|ma|mi|mn|ms|mo|mt|ne|nv|nh|nj|nm|ny|nc|nd|oh|ok|or|pa|ri|sc|sd|tn|tx|ut|vt|va|wa|wv|wi|wy)\b", "US"),
    (r"canada|toronto|vancouver|montreal|québec|quebec|niagara|banff|alberta|ontario", "CA"), (r"mexico|méxico|cancún|cancun|tulum|playa del carmen", "MX"),
    (r"brazil|brasil|rio de janeiro|são paulo|sao paulo", "BR"), (r"argentina|buenos aires", "AR"), (r"chile|santiago de chile", "CL"),
    (r"costa rica", "CR"), (r"caribbean|aruba|bahamas|barbados|cayman|jamaica|st\.? maarten|sint maarten|curaçao|bermuda|virgin islands", ""),
    (r"japan|tokyo|shibuya|shinjuku|osaka|kyoto|fuji|富士|渋谷|東京|大阪|京都|ライブカメラ|hokkaido|okinawa", "JP"), (r"korea|seoul|busan", "KR"),
    (r"thailand|bangkok|phuket|pattaya", "TH"), (r"bali|indonesia|jakarta", "ID"), (r"philippines|manila", "PH"), (r"vietnam|hanoi", "VN"),
    (r"australia|sydney|melbourne|brisbane|gold coast|perth|adelaide", "AU"), (r"new zealand|auckland|queenstown|wellington", "NZ"),
    (r"namibia|etosha|namib", "NA"), (r"kenya|nairobi|lentorre|ol donyo|mara", "KE"), (r"south africa|cape town|kruger|johannesburg|tembe|africam", "ZA"),
    (r"botswana|okavango", "BW"), (r"tanzania|serengeti", "TZ"), (r"dubai|abu dhabi|emirates|\buae\b", "AE"), (r"israel|jerusalem|tel aviv", "IL"),
]
SKY_ZEME = {"albania": "AL", "argentina": "AR", "australia": "AU", "austria": "AT", "barbados": "BB", "belize": "BZ", "bermuda": "BM",
            "bolivia": "BO", "bosnia-and-herzegovina": "BA", "brasil": "BR", "bulgaria": "BG", "cabo-verde": "CV", "canada": "CA",
            "caribbean-netherlands": "BQ", "chile": "CL", "china": "CN", "costa-rica": "CR", "cyprus": "CY", "czech-republic": "CZ",
            "deutschland": "DE", "dominican-republic": "DO", "ecuador": "EC", "el-salvador": "SV", "ellada": "GR", "espana": "ES",
            "faroe-islands": "FO", "france": "FR", "grenada": "GD", "guadeloupe": "GP", "honduras": "HN", "hrvatska": "HR",
            "hungary": "HU", "iceland": "IS", "ireland": "IE", "israel": "IL", "italia": "IT", "jamaica": "JM", "jordan": "JO",
            "kenya": "KE", "lebanon": "LB", "luxembourg": "LU", "malta": "MT", "maldives": "MV", "martinique": "MQ", "mauritius": "MU",
            "mexico": "MX", "montenegro": "ME", "morocco": "MA", "nederland": "NL", "nepal": "NP", "norway": "NO", "panama": "PA",
            "peru": "PE", "philippines": "PH", "polska": "PL", "portugal": "PT", "puerto-rico": "PR", "romania": "RO",
            "san-marino": "SM", "senegal": "SN", "serbia": "RS", "seychelles": "SC", "sint-maarten": "SX", "slovenija": "SI",
            "south-africa": "ZA", "sri-lanka": "LK", "svizzera": "CH", "schweiz": "CH", "switzerland": "CH", "thailand": "TH",
            "turkey": "TR", "turkiye": "TR", "united-arab-emirates": "AE", "united-kingdom": "GB", "united-states": "US", "usa": "US",
            "uruguay": "UY", "venezuela": "VE", "vietnam": "VN", "zambia": "ZM", "zanzibar": "TZ", "tanzania": "TZ", "egypt": "EG",
            "slovakia": "SK", "slovensko": "SK", "denmark": "DK", "sweden": "SE", "finland": "FI", "estonia": "EE", "latvia": "LV",
            "lithuania": "LT", "belgium": "BE", "ukraine": "UA", "georgia": "GE", "armenia": "AM", "japan": "JP", "indonesia": "ID",
            "australia-": "AU", "new-zealand": "NZ", "fiji": "FJ", "bahamas": "BS", "aruba": "AW", "curacao": "CW", "cuba": "CU",
            "colombia": "CO", "guatemala": "GT", "nicaragua": "NI", "paraguay": "PY", "tunisia": "TN", "cape-verde": "CV", "netherlands": "NL", "norge": "NO", "poland": "PL",
            "us-virgin-islands": "VI", "repubblica-di-san-marino": "SM"}
KATEGORIE = [(r"airport|letiště|flughafen|aeroporto|plane|runway", "letiště"), (r"rail|train|station|nádraží|bahnhof|stazione|railfan", "železnice"),
             (r"harbou?r|port|hafen|porto|marina|ship|cruise|ferry|přístav", "přístav"), (r"beach|pláž|playa|spiaggia|strand|bay|coast|sea|mare|ocean", "moře"),
             (r"ski|mountain|alp|hory|piste|peak|gipfel|summit|valley|dolomit|tatry", "hory"), (r"bird|nest|feeder|ptá|osprey|eagle|owl", "ptáci"),
             (r"bear|wildlife|waterhole|safari|zoo|animal|aquarium|elephant|lion|panda", "zvířata"), (r"volcano|sopk|geyser|northern lights|aurora|river|lake|waterfall|forest", "příroda"),
             (r"square|náměstí|platz|piazza|plaza|street|downtown|city|town|skyline|bridge", "město")]


def zeme_z(text):
    t = (text or "").lower()
    for vzor, kod in KLICE:
        if re.search(vzor, t):
            return kod
    return ""


def kategorie_z(text):
    t = (text or "").lower()
    for vzor, k in KATEGORIE:
        if re.search(vzor, t):
            return k
    return ""


def slug(t):
    t = unicodedata.normalize("NFKD", t).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")[:36] or "kamera"


def cisty(t):
    t = re.sub(r"[^\w\s\-–,.:'()/&!+·]", " ", t or "", flags=re.U)
    t = re.sub(r"(?i)\b(4k|8k|uhd|hd|live\s*cam|livecam|live|24/7|24 7|webcam|livestream|stream|now|en direct|ao vivo|en vivo|na żywo|ライブカメラ)\b", " ", t)
    t = re.sub(r"(?i)\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b.*?(\d{4})?", " ", t)
    t = re.sub(r"\b\d{1,2}(st|nd|rd|th)?\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b\s*\d{0,4}", " ", t, flags=re.I)
    t = re.sub(r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2}:\d\d\b", " ", t)
    t = re.sub(r"^\s*earthcam\s*:?\s*", "", t, flags=re.I)
    t = re.sub(r"\s*[|•]\s*.*$", "", t)
    return re.sub(r"\s{2,}", " ", t).strip(" -–:,.·")[:70]


def match_z(titul):
    """stabilní kus názvu přenosu pro typ ytkanal (bez data, času, emoji) – nejdelší úsek mezi oddělovači"""
    c = cisty(titul)
    casti = [x.strip() for x in re.split(r"\s[-–:]\s|\s*\(|\)", c) if len(x.strip()) >= 4]
    m = max(casti, key=len) if casti else c
    return m[:40]


async def ytdlp_json(args, timeout=60):
    p = await asyncio.create_subprocess_exec(resolver.YTDLP, "--flat-playlist", "-J", "--no-warnings", "--no-cache-dir", *args,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return {}
    try:
        r = json.loads(out or b"{}")
    except ValueError:
        return {}
    return r if isinstance(r, dict) else {}         # yt-dlp umí vrátit „null“


async def youtube():
    kanaly = {}                       # url → název kanálu
    for x in json.load(open(os.path.join(D, "kandidati.json"), encoding="utf-8")):
        if x.get("channel_id"):
            kanaly["https://www.youtube.com/channel/" + x["channel_id"]] = x.get("kanal") or ""
    for h in KANALY_JMENA:
        kanaly.setdefault("https://www.youtube.com/" + h, h)
    for q in HLEDANI:
        d = await ytdlp_json(["--playlist-end", "30", "https://www.youtube.com/results?search_query=%s&sp=EgJAAQ%%253D%%253D" % quote(q)])
        n = 0
        for e in d.get("entries") or []:
            if e.get("channel_id") and e.get("live_status") == "is_live":
                u = "https://www.youtube.com/channel/" + e["channel_id"]
                if u not in kanaly:
                    kanaly[u] = e.get("channel") or ""
                    n += 1
        print("hledání %-28s +%d kanálů (celkem %d)" % (q, n, len(kanaly)), flush=True)
        await asyncio.sleep(2)
    cams = []
    for u, jm in kanaly.items():
        if BLACKLIST_KANAL.search(jm or ""):
            continue
        d = await ytdlp_json(["--playlist-end", "80", u + "/streams"])
        jm = d.get("channel") or d.get("uploader") or jm
        if BLACKLIST_KANAL.search(jm or ""):
            continue
        zive = [e for e in d.get("entries") or [] if e.get("live_status") == "is_live"]
        pridano = 0
        for e in zive:
            t = e.get("title") or ""
            if NEGATIVNI.search(t) or not POZITIVNI.search(t + " " + jm):
                continue
            m = match_z(t)
            if len(m) < 4:
                continue
            nazev = cisty(t) or jm
            cams.append({"name": nazev, "country": zeme_z(t + " " + jm), "category": kategorie_z(t + " " + jm), "source": u,
                         "type": "ytkanal", "match": m, "kanal": jm, "titul": t})
            pridano += 1
        print("kanál %-40s živých %2d, přidáno %2d" % ((jm or u)[:40], len(zive), pridano), flush=True)
        await asyncio.sleep(1.5)
    return cams


async def skyline():
    cams = []
    async with httpx.AsyncClient(headers={"User-Agent": UA}, timeout=20, follow_redirects=True) as cl:
        r = await cl.get("https://www.skylinewebcams.com/en.html")
        fronta = sorted(set(x for x in re.findall(r'href="(/en/webcam/[^"/]+\.html)"', r.text)))
        videno, kamery = set(fronta), {}
        while fronta:
            u = fronta.pop(0)
            try:
                r = await cl.get("https://www.skylinewebcams.com" + u)
            except Exception as e:
                print("!!", u, e)
                continue
            # odkazy bývají relativní bez lomítka (href="en/webcam/…"), název kamery je v alt obrázku
            for href, text in re.findall(r'href="/?(en/webcam/[^"]+\.html)"[^>]*>(.*?)</a>', r.text, re.S):
                href = "/" + href
                hloubka = href.count("/") - 2           # /en/webcam/a.html = 1
                if hloubka >= 4:
                    if href not in kamery:
                        alt = re.search(r'alt="([^"]+)"', text)
                        jm = alt.group(1) if alt else re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text)).strip()
                        kamery[href] = jm
                elif href not in videno and href.startswith(u[:-5] + "/"):
                    videno.add(href)
                    fronta.append(href)
            print("skyline %-60s stránek ve frontě %3d, kamer %d" % (u[:60], len(fronta), len(kamery)), flush=True)
            await asyncio.sleep(0.7)
    for href, jm in kamery.items():
        casti = href.split("/")
        kod = SKY_ZEME.get(casti[3], "")
        mesto = casti[-2].replace("-", " ").title()
        nazev = jm if len(jm) >= 3 else casti[-1][:-5].replace("-", " ").title()
        if mesto.lower() not in nazev.lower():
            nazev = "%s – %s" % (mesto, nazev)
        cams.append({"name": nazev[:70], "country": kod, "category": kategorie_z(href + " " + jm),
                     "source": "https://www.skylinewebcams.com" + href, "type": "skyline", "match": ""})
    return cams


async def main():
    co = sys.argv[1:] or ["youtube", "skyline"]
    vse = []
    if "youtube" in co:
        vse += await youtube()
    if "skyline" in co:
        vse += await skyline()
    # deduplikace (stejný zdroj + match) a jedinečná id
    out, klice, ids = [], set(), set()
    for c in vse:
        k = (c["source"], c.get("match", ""))
        if k in klice:
            continue
        klice.add(k)
        base = slug(("%s-%s" % (c["country"], c["name"])).strip("-"))
        cid, i = base, 2
        while cid in ids:
            cid, i = "%s-%d" % (base[:33], i), i + 1
        ids.add(cid)
        out.append({"id": cid, "name": c["name"], "group": "Ostatní", "country": c["country"], "category": c["category"],
                    "source": c["source"], "type": c["type"], "match": c.get("match", ""), "headers": {}, "mode": "auto",
                    "enabled": True, "regex": ""})
    soubor = "sber2.json" if len(co) == 2 else "sber2_%s.json" % "_".join(co)
    json.dump(out, open(os.path.join(D, soubor), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("HOTOVO", len(out), "kamer")


asyncio.run(main())
