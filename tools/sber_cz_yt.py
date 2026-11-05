"""Sběr českých kamer z YouTube (5. 11. 2026) → tools/sber_cz_yt.json (formát cams.json, typ ytkanal).

1. Hledá živé přenosy (filtr Live) podle českých dotazů: obecné + „<město> živě“ pro okresní a turistická města.
2. Přidá kanály, které už ve finalni.json jsou – často vysílají víc kamer, než je v seznamu.
3. U každého kanálu projde /streams, vezme živé přenosy, které vypadají česky, a určí kraj podle názvu.
4. Vyřadí, co už je ve finalni.json (stejný klíč jako sloucit.py: kanál + match, nebo ID videa).

Šetrně: postupně, s pauzami (~15–25 min). Ověření dělá kontrola ve službě (checker.py).
Spuštění: cd ~/webcams && venv/bin/python tools/sber_cz_yt.py [--jen-kanaly]
  --jen-kanaly  bez hledání, jen znovu projde známé české kanály (rychlé, ~2 min)
"""
import asyncio
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from urllib.parse import quote

D = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(D))
import resolver  # noqa: E402,F401  (sber2 ho potřebuje)

# pomocné funkce ze sber2.py / sloucit.py (bez spuštění jejich main)
g = {"__file__": os.path.join(D, "sber2.py")}
exec(open(os.path.join(D, "sber2.py"), encoding="utf-8").read().split("async def youtube")[0], g)
ytdlp_json, cisty, match_z, kategorie_z, NEGATIVNI = g["ytdlp_json"], g["cisty"], g["match_z"], g["kategorie_z"], g["NEGATIVNI"]
h = {"__file__": os.path.join(D, "sloucit.py")}
exec(open(os.path.join(D, "sloucit.py"), encoding="utf-8").read().split("def main")[0], h)
klic = h["klic"]

LIVE = "https://www.youtube.com/results?search_query=%s&sp=EgJAAQ%%253D%%253D"

OBECNE = [
    "webkamera živě", "živá webkamera", "webkamera online česko", "živý přenos kamera náměstí", "náměstí živě",
    "čapí hnízdo živě", "čápi online", "krmítko živě", "ptačí budka živě", "sokol hnízdo kamera", "poštolka hnízdo živě",
    "zoo živě kamera", "zoo online přenos", "nádraží živě", "vlaky živě kamera", "železnice online kamera",
    "letiště živě kamera", "skiareál webkamera", "sjezdovka živě", "krkonoše webkamera", "šumava webkamera",
    "beskydy webkamera", "jeseníky webkamera", "přehrada živě", "rybník kamera živě", "koupaliště webkamera",
    "aquapark webkamera", "zimní stadion živě kamera", "radnice webkamera", "výhled z věže živě",
]

# město → kraj (klíče bez diakritiky, malými písmeny; hledá se v názvu přenosu i kanálu)
MESTA = {
    "Praha": ["praha", "prague", "prag"],
    "Středočeský kraj": ["kladno", "mlada boleslav", "kolin", "kutna hora", "pribram", "benesov", "beroun", "melnik",
                         "nymburk", "rakovnik", "slany", "kralupy", "brandys", "podebrady", "cercany", "luzna", "karlstejn"],
    "Jihočeský kraj": ["ceske budejovice", "budejovice", "cesky krumlov", "tabor", "pisek", "strakonice", "prachatice",
                       "jindrichuv hradec", "trebon", "lipno", "volary", "vimperk", "kvilda", "makov", "volyne"],
    "Plzeňský kraj": ["plzen", "pilsen", "klatovy", "domazlice", "rokycany", "tachov", "susice", "zelezna ruda", "spicak",
                      "nyrsko", "horazdovice"],
    "Karlovarský kraj": ["karlovy vary", "carlsbad", "cheb", "sokolov", "marianske lazne", "frantiskovy lazne", "ostrov",
                         "bozi dar", "klinovec", "nejdek"],
    "Ústecký kraj": ["usti nad labem", "teplice", "decin", "most", "chomutov", "litomerice", "louny", "zatec", "kadan",
                     "bilina", "roudnice", "varnsdorf", "rumburk", "krupka", "jirkov"],
    "Liberecký kraj": ["liberec", "jablonec", "ceska lipa", "turnov", "semily", "tanvald", "jested", "frydlant", "hradek nad nisou",
                       "harrachov", "rokytnice nad jizerou", "lomnice nad popelkou", "mimon", "novy bor", "doksy", "machovo jezero"],
    "Královéhradecký kraj": ["hradec kralove", "trutnov", "nachod", "jicin", "rychnov", "dvur kralove", "broumov", "jaromer",
                             "vrchlabi", "spindleruv mlyn", "pec pod snezkou", "snezka", "janske lazne", "mlade buky",
                             "zacler", "cerveny kostelec", "nove mesto nad metuji", "hostinne", "bohuslavice", "destne"],
    "Pardubický kraj": ["pardubice", "chrudim", "svitavy", "usti nad orlici", "litomysl", "policka", "lanskroun",
                        "ceska trebova", "vysoke myto", "holice", "dolni morava", "kralicak", "hlinsko"],
    "Kraj Vysočina": ["jihlava", "trebic", "havlickuv brod", "zdar nad sazavou", "pelhrimov", "humpolec", "velke mezirici",
                      "telc", "nove mesto na morave", "chotebor", "pacov"],
    "Jihomoravský kraj": ["brno", "znojmo", "breclav", "hodonin", "vyskov", "blansko", "mikulov", "lednice", "valtice",
                          "kyjov", "boskovice", "slavkov", "pavlov", "palava", "veseli nad moravou"],
    "Olomoucký kraj": ["olomouc", "prerov", "prostejov", "sumperk", "jesenik", "hranice", "zabreh", "unicov", "litovel",
                       "lipnik", "ramzova", "cervenohorske sedlo", "praded", "kouty"],
    "Zlínský kraj": ["zlin", "kromeriz", "uherske hradiste", "uhersky brod", "vsetin", "valasske mezirici", "roznov",
                     "otrokovice", "holesov", "luhacovice", "bystrice pod hostynem", "valasske klobouky", "vizovice",
                     "napajedla", "stare mesto", "kunovice", "bojkovice", "slavicin", "brumov", "pozlovice", "kohutka",
                     "pustevny", "velke karlovice", "karolinka", "trojak", "hostyn"],
    "Moravskoslezský kraj": ["ostrava", "opava", "frydek", "mistek", "karvina", "havirov", "novy jicin", "koprivnice",
                             "trinec", "bruntal", "krnov", "frenstat", "frydlant nad ostravici", "bila", "lysa hora",
                             "mala moravka", "karlov", "studenka", "bohumin", "hlucin"],
}
MESTO_KRAJ = [(m, k) for k, ms in MESTA.items() for m in ms]
OKRESNI = [m for ms in MESTA.values() for m in ms if len(m) > 4 and m not in ("prague", "prag", "carlsbad", "pilsen")]

CZ_ZNAKY = re.compile(r"[ěščřžůťďňĚŠČŘŽŮŤĎŇ]")
CZ_SLOVA = re.compile(r"\b(živě|zive|náměstí|namesti|nádraží|nadrazi|hnízdo|hnizdo|krmítko|krmitko|čáp|čápi|výhled|vyhled|"
                      r"kamera|webkamera|přenos|prenos|radnice|skiareál|skiareal|sjezdovka|přehrada|koupaliště|česk|cesk|czech)\b", re.I)
CIZI = re.compile(r"\b(slovensk|slovakia|bratislava|tatry|polsk|polska|poland|kraków|zakopane|deutschland|österreich)\b", re.I)


def fold(t):
    return unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()


def kraj_z(text):
    t = " %s " % re.sub(r"[^a-z0-9]+", " ", fold(text))
    for m, k in MESTO_KRAJ:
        if " %s " % m in t:
            return k
    return ""


def cesky(titul, kanal):
    """přenos patří do Česka: jmenuje české město, nebo má češtinu a nic cizího"""
    text = "%s %s" % (titul, kanal)
    if CIZI.search(text):
        return False
    return bool(kraj_z(text) or CZ_ZNAKY.search(text) or CZ_SLOVA.search(text))


async def hledej(q, kanaly):
    d = await ytdlp_json(["--playlist-end", "25", LIVE % quote(q)])
    n = 0
    for e in d.get("entries") or []:
        if e.get("channel_id") and e.get("live_status") == "is_live" and cesky(e.get("title") or "", e.get("channel") or ""):
            u = "https://www.youtube.com/channel/" + e["channel_id"]
            if u not in kanaly:
                kanaly[u] = e.get("channel") or ""
                n += 1
    return n


async def main():
    finalni = json.load(open(os.path.join(D, "finalni.json"), encoding="utf-8"))
    mam = {klic(c) for c in finalni}
    mam_video = set(re.findall(r"(?:v=|youtu\.be/|embed/)([\w-]{11})", json.dumps(finalni)))

    # známé české kanály – projdou se vždy
    kanaly = {}
    for c in finalni:
        if c.get("country") == "CZ" and "youtube.com" in c.get("source", ""):
            m = re.search(r"/channel/(UC[\w-]{22})", c["source"] + " " + c.get("note", ""))
            if m:
                kanaly.setdefault("https://www.youtube.com/channel/" + m.group(1), "")
            elif "/@" in c["source"]:
                kanaly.setdefault(re.sub(r"/(live|streams)/?$", "", c["source"]), "")
    print("známých českých kanálů:", len(kanaly), flush=True)

    if "--jen-kanaly" not in sys.argv:
        dotazy = OBECNE + ["%s živě" % m for m in OKRESNI]
        for i, q in enumerate(dotazy, 1):
            n = await hledej(q, kanaly)
            print("[%3d/%d] %-32s +%d (kanálů %d)" % (i, len(dotazy), q, n, len(kanaly)), flush=True)
            await asyncio.sleep(2)

    nove, stat = [], Counter()
    for u, jm in kanaly.items():
        d = await ytdlp_json(["--playlist-end", "60", u + "/streams"])
        jm = d.get("channel") or d.get("uploader") or jm
        uc = d.get("channel_id")
        src = "https://www.youtube.com/channel/" + uc if uc else u
        zive = [e for e in d.get("entries") or [] if e.get("live_status") == "is_live"]
        pridano = 0
        for e in zive:
            t = e.get("title") or ""
            if NEGATIVNI.search(t):
                stat["nekamera"] += 1
                continue
            if not cesky(t, jm):
                stat["necesky"] += 1
                continue
            m = match_z(t)
            if len(m) < 4:
                stat["kratky název"] += 1
                continue
            c = {"name": cisty(t) or jm, "group": kraj_z(t + " " + jm) or "Ostatní", "country": "CZ",
                 "category": kategorie_z(t + " " + jm) or "město", "source": src, "type": "ytkanal", "match": m,
                 "headers": {}, "mode": "auto", "enabled": True, "regex": "",
                 "page": "https://www.youtube.com/watch?v=" + e["id"], "note": "sběr YouTube CZ, kanál „%s“, přenos „%s“" % (jm, t[:80])}
            if klic(c) in mam or e["id"] in mam_video:
                stat["už je"] += 1
                continue
            mam.add(klic(c))
            nove.append(c)
            pridano += 1
        print("kanál %-40s živých %2d, nových %2d" % ((jm or u)[:40], len(zive), pridano), flush=True)
        await asyncio.sleep(1.5)

    ids = {c["id"] for c in finalni}
    for c in nove:
        base = g["slug"](c["name"])
        cid, i = base, 2
        while cid in ids:
            cid, i = "%s-%d" % (base[:33], i), i + 1
        ids.add(cid)
        c["id"] = cid
    nove = [{k: c[k] for k in ("id", "name", "group", "country", "category", "source", "type", "match", "headers",
                               "mode", "enabled", "regex", "page", "note")} for c in nove]
    json.dump(nove, open(os.path.join(D, "sber_cz_yt.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("vyřazeno:", dict(stat))
    print("podle krajů:", dict(Counter(c["group"] for c in nove)))
    print("HOTOVO", len(nove), "nových kamer → tools/sber_cz_yt.json")


if __name__ == "__main__":
    asyncio.run(main())
