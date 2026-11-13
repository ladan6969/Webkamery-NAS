"""Překlad názvů kamer z azbuky do češtiny (13. 11. 2026). Běží jen na mojetv1 (Argos z ~/epg-preklad/venv):
  ~/epg-preklad/venv/bin/python tools/preklad_azbuky.py cams.json [další.json…] > preklad.json
Vypíše {id: {"z": původní, "na": přeložený}}. Části v azbuce se přeloží (ru → en → cs), latinka zůstane.
Když název obsahuje stejný text latinkou i azbukou („Nevskiy avenue … Невский пр.“), azbuka se vynechá.
"""
import json
import re
import sys

import argostranslate.translate as t

CYR = re.compile(r"[Ѐ-ӿ]")
USEK = re.compile(r"[Ѐ-ӿ][Ѐ-ӿ0-9\s.,:;\"«»()'№/-]*[Ѐ-ӿ0-9.)»\"]|[Ѐ-ӿ]")
L = {l.code: l for l in t.get_installed_languages()}
RU_CS = L["ru"].get_translation(L["cs"])
_cache = {}
# ruční překlady: Argos u krátkých jmen měst a zvířat chybuje („Ноябрьск“ → listopad, „Одинцово“ → Sám.)
SLOVNIK = {
    "Алматы - ГК Шымбулак": "Almaty – Šymbulak", "Астрахань": "Astrachaň", "Байкальский Музей": "Bajkalské muzeum",
    "Белград": "Bělehrad", "Белоярский": "Bělojarskij", "Верхняя Салда": "Verchňaja Salda", "Вологда": "Vologda",
    "Греция": "Řecko", "Кингисепп": "Kingisepp", "Киржач": "Kiržač", "Кормушка": "krmítko",
    "Красноярск - Бобровый Лог": "Krasnojarsk – Bobrovyj Log", "Кузбасс": "Kuzbas", "Литва - Вильнюс": "Litva – Vilnius",
    "Литва - Игналина": "Litva – Ignalina", "Львов - Церковь": "Lvov – kostel", "Люксембург": "Lucembursko",
    "Междуреченск": "Mežduričensk", "Назарово": "Nazarovo", "Нелидово": "Nelidovo", "Нидерланды": "Nizozemsko",
    "Нидерланды - Морские котики": "Nizozemsko – lachtani", "Ноябрьск": "Nojabrsk", "Одинцово": "Odincovo",
    "Оренбург": "Orenburg", "Пингвины": "tučňáci", "Полевской": "Polevskoj",
    "Полевской Карла Маркса 6а": "Polevskoj – Karla Marxe 6a", "Полевской Коммунистическая": "Polevskoj – Komunističeskaja",
    "Полевской Перекресток": "Polevskoj – křižovatka", "Полевской площадь Солдата": "Polevskoj – náměstí Vojáka",
    "Польша - Гданьск": "Polsko – Gdaňsk", "Польша - Гдыня": "Polsko – Gdyně", "Польша - Микошево": "Polsko – Mikoszewo",
    "Польша - Сопот": "Polsko – Sopoty", "Пушкино": "Puškino", "Рифы": "korálové útesy", "США - Выдры": "USA – vydry",
    "США - Морской лев": "USA – lachtan", "США - Утконос": "USA – ptakopysk", "Симферополь": "Simferopol",
    "Словакия": "Slovensko", "Ставрополь": "Stavropol", "Томск - Площадь Южная": "Tomsk – Jižní náměstí",
    "Томск - Река Томь": "Tomsk – řeka Tom", "Тулун": "Tulun", "Турция": "Turecko", "Улан-Удэ": "Ulan-Ude",
    "Усинск": "Usinsk", "Фламинго": "plameňáci", "Франция": "Francie", "Херсон": "Cherson", "Шарыпово": "Šarypovo",
    "Шахтинск": "Šachtinsk", "Эстония": "Estonsko",
}


def preloz_usek(s):
    m = re.fullmatch(r"(.*?)(\s*\d+)?", s.strip())
    if m.group(1) in SLOVNIK:
        return SLOVNIK[m.group(1)] + (m.group(2) or "")
    if s not in _cache:
        _cache[s] = (RU_CS.translate(s) or s).strip()
    return _cache[s]


def preloz(nazev):
    if not CYR.search(nazev):
        return nazev
    lat = CYR.sub("", nazev)
    if len(re.findall(r"[A-Za-z]{3,}", lat)) >= 3:      # latinkou už je popsáno – azbuku vynechat
        out = USEK.sub("", nazev)
    else:
        out = USEK.sub(lambda m: preloz_usek(m.group(0)), nazev)
    out = re.sub(r"\s+([,.])", r"\1", out).replace(" - ", " – ")
    out = out[:1].upper() + out[1:]
    out = re.sub(r"\s{2,}", " ", out).strip(" .,-–|")
    return out or nazev


if __name__ == "__main__":
    vysl = {}
    for soubor in sys.argv[1:]:
        for c in json.load(open(soubor, encoding="utf-8")):
            if c["id"] not in vysl and CYR.search(c["name"]):
                vysl[c["id"]] = {"z": c["name"], "na": preloz(c["name"])}
    json.dump(vysl, sys.stdout, ensure_ascii=False, indent=1)
