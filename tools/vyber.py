"""Ruční výběr z tools/kandidati.json (3. 11. 2026) → tools/vyber.json (kamery ve formátu cams.json).
(podřetězec názvu kandidáta, id, český název, země, kategorie)"""
import json
import os
import re

D = os.path.dirname(os.path.abspath(__file__))
VYBER = [
    ("Creative Live streaming from Prague", "praha-panorama", "Praha – panorama", "CZ", "město"),
    ("Trainspotting on Prague Main", "praha-hlavni-nadrazi", "Praha – hlavní nádraží", "CZ", "železnice"),
    ("ITS - Praha centrum", "praha-centrum", "Praha – centrum", "CZ", "město"),
    ("Planespotting at Prague", "praha-letiste", "Praha – letiště Václava Havla", "CZ", "letiště"),
    ("Vršovice, Praha 10", "praha-vrsovice", "Praha – Vršovice", "CZ", "město"),
    ("Brno-střed", "brno-zelny-trh", "Brno – Zelný trh", "CZ", "město"),
    ("Kamera SEVER", "brno-hvezdarna", "Brno – výhled z hvězdárny", "CZ", "město"),
    ("Český Dub", "cesky-dub", "Český Dub – radnice", "CZ", "město"),
    ("Česká Skalice", "ceska-skalice", "Česká Skalice", "CZ", "město"),
    ("Pec pod Sněžkou", "pec-pod-snezkou", "Pec pod Sněžkou – Krkonoše", "CZ", "hory"),
    ("Zvičina", "zvicina", "Zvičina – výhled na sever", "CZ", "hory"),
    ("Oustecké nádraží", "usti-nad-orlici-nadrazi", "Ústí nad Orlicí – nádraží", "CZ", "železnice"),
    ("Čerčany", "cercany-nadrazi", "Čerčany – nádraží", "CZ", "železnice"),
    ("Lužná u Rakovníka", "luzna-nadrazi", "Lužná u Rakovníka – nádraží", "CZ", "železnice"),
    ("Modra #1", "modra", "Modra – náměstí", "SK", "město"),
    ("Bratislava - Panoráma", "bratislava-panorama", "Bratislava – panoráma", "SK", "město"),
    ("Jezioro Rożnowskie", "roznowske-jezero-tatry", "Rožnovské jezero a Tatry", "PL", "hory"),
    ("Railcam Tampere", "tampere-zeleznice", "Tampere – železnice", "FI", "železnice"),
    ("Saalbach", "saalbach", "Saalbach Hinterglemm", "AT", "hory"),
    ("Zillertal", "zillertal", "Zillertal", "AT", "hory"),
    ("Grimentz", "grimentz", "Grimentz – Valais", "CH", "hory"),
    ("Tre Cime", "tre-cime", "Tre Cime di Lavaredo – Dolomity", "IT", "hory"),
    ("Val Gardena", "val-gardena", "Val Gardena – Dolomity", "IT", "hory"),
    ("Rolling Cam Venice", "benatky-canal", "Benátky – Rolling Cam", "IT", "město"),
    ("Ponte delle Guglie", "benatky-guglie", "Benátky – Ponte delle Guglie", "IT", "město"),
    ("St. Peter's Dome", "rim-svaty-petr", "Řím – kopule sv. Petra", "IT", "město"),
    ("Hvar, Croatia", "hvar", "Hvar – přístav", "HR", "moře"),
    ("Lucica Beach", "brela", "Brela – pláž Lucica", "HR", "moře"),
    ("Slanica Beach", "murter", "Murter – pláž Slanica", "HR", "moře"),
    ("Rogoznica", "rogoznica", "Rogoznica – panorama", "HR", "moře"),
    ("PARIS EN DIRECT", "pariz-eiffelovka", "Paříž – Eiffelova věž", "FR", "město"),
    ("Tour Montparnass", "pariz-panorama", "Paříž – panorama s Eiffelovkou", "FR", "město"),
    ("Amsterdam Centraal", "amsterdam-centraal", "Amsterdam – hlavní nádraží", "NL", "železnice"),
    ("Heathrow Airport Live -", "londyn-heathrow", "Londýn – letiště Heathrow", "GB", "letiště"),
    ("Abbey Road", "londyn-abbey-road", "Londýn – Abbey Road", "GB", "město"),
    ("Cruise Center Altona", "hamburg-altona", "Hamburk – přístav Altona", "DE", "přístav"),
    ("Hamburg Hafen Live 24", "hamburg-pristav", "Hamburk – přístav", "DE", "přístav"),
    ("Berlin Alexanderplatz", "berlin-alexanderplatz", "Berlín – Alexanderplatz", "DE", "město"),
    ("17.Juni", "berlin-17-juni", "Berlín – Straße des 17. Juni", "DE", "město"),
    ("TENERIFE AIRPORT", "tenerife-letiste", "Tenerife – letiště", "ES", "letiště"),
    ("BENIDORM", "benidorm", "Benidorm – pláž", "ES", "moře"),
    ("Arillas Corfu", "korfu-arillas", "Korfu – Arillas", "GR", "moře"),
    ("Iceland Volcano Watch", "island-sopky", "Island – sopky (více kamer)", "IS", "příroda"),
    ("Fagradalsfjall", "island-fagradalsfjall", "Island – Fagradalsfjall", "IS", "příroda"),
    ("Lyngen North", "lyngen-polarni-zare", "Lyngen – polární záře", "NO", "příroda"),
    ("1560 Broadway", "nyc-times-square", "New York – Times Square", "US", "město"),
    ("Times Square Crossroads", "nyc-times-square-2", "New York – Times Square (EarthCam)", "US", "město"),
    ("LAS Vegas Airport", "las-vegas-letiste", "Las Vegas – letiště", "US", "letiště"),
    ("Fremont Street", "las-vegas-fremont", "Las Vegas – Fremont Street", "US", "město"),
    ("Venice Beach · Venice V Hotel", "los-angeles-venice-beach", "Los Angeles – Venice Beach", "US", "moře"),
    ("Deerfield", "deerfield-beach", "Florida – Deerfield Beach", "US", "moře"),
    ("Jackson Hole Town Square", "jackson-hole", "Jackson Hole – náměstí", "US", "město"),
    ("Yellowstone River", "yellowstone-reka", "Yellowstone – řeka u Gardineru", "US", "příroda"),
    ("Brooks Falls", "aljaska-brooks-falls", "Aljaška – medvědi u Brooks Falls", "US", "zvířata"),
    ("River Watch - Katmai", "aljaska-katmai-reka", "Aljaška – řeka v Katmai", "US", "zvířata"),
    ("Fort Lauderdale", "fort-lauderdale-letiste", "Fort Lauderdale – letiště", "US", "letiště"),
    ("LAX Airport", "los-angeles-letiste", "Los Angeles – letiště LAX", "US", "letiště"),
    ("FNN", "tokio-shibuya", "Tokio – křižovatka Shibuya", "JP", "město"),
    ("Lake Kawagu", "fudzi-kawaguchiko", "Fudži od jezera Kawaguchi", "JP", "hory"),
    ("Oshino Hakk", "fudzi-oshino", "Fudži z Oshino Hakkai", "JP", "hory"),
    ("Namib Desert", "namibie-poust", "Namibie – poušť Namib", "NA", "zvířata"),
    ("Okaukuejo", "namibie-etosha", "Namibie – napajedlo Etosha", "NA", "zvířata"),
    ("ol Donyo", "kena-ol-donyo", "Keňa – ol Donyo Lodge", "KE", "zvířata"),
    ("Lentorre", "kena-lentorre", "Keňa – Lentorre", "KE", "zvířata"),
    ("WebcamSydney 1", "sydney-pristav", "Sydney – přístav", "AU", "přístav"),
    ("Bridge & Opera House", "sydney-opera", "Sydney – most a opera", "AU", "přístav"),
]


def main():
    k = json.load(open(os.path.join(D, "kandidati.json"), encoding="utf-8"))
    out, chybi = [], []
    for sub, cid, nazev, zeme, kat in VYBER:
        x = next((x for x in k if x["ok"] and sub.lower() in (x["titul"] or "").lower() + " " + (x["kanal"] or "").lower()), None)
        if not x:
            chybi.append(sub)
            continue
        out.append({"id": cid, "name": nazev, "group": "Ostatní", "country": zeme, "category": kat, "source": x["source"],
                    "type": "ytdlp", "headers": {}, "mode": "auto", "enabled": True, "regex": ""})
    json.dump(out, open(os.path.join(D, "vyber.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(len(out), "vybráno; nenalezeno:", chybi)
    assert len({c["id"] for c in out}) == len(out) and all(re.fullmatch(r"[a-z0-9_-]{2,40}", c["id"]) for c in out)


main()
