"""zeme.py – kód země (ISO 3166-1 alpha-2) → český název; kategorie playlistu podle zemí (3. 11. 2026)."""
ZEME = {
    "CZ": "Česko", "SK": "Slovensko", "PL": "Polsko", "DE": "Německo", "AT": "Rakousko", "HU": "Maďarsko",
    "HR": "Chorvatsko", "SI": "Slovinsko", "IT": "Itálie", "FR": "Francie", "ES": "Španělsko", "PT": "Portugalsko",
    "CH": "Švýcarsko", "NL": "Nizozemsko", "BE": "Belgie", "LU": "Lucembursko", "GB": "Velká Británie", "IE": "Irsko",
    "IS": "Island", "NO": "Norsko", "SE": "Švédsko", "FI": "Finsko", "DK": "Dánsko", "EE": "Estonsko", "LV": "Lotyšsko",
    "LT": "Litva", "GR": "Řecko", "CY": "Kypr", "MT": "Malta", "TR": "Turecko", "BG": "Bulharsko", "RO": "Rumunsko",
    "RS": "Srbsko", "BA": "Bosna a Hercegovina", "ME": "Černá Hora", "AL": "Albánie", "MK": "Severní Makedonie",
    "UA": "Ukrajina", "MD": "Moldavsko", "MC": "Monako", "AD": "Andorra", "SM": "San Marino", "VA": "Vatikán",
    "LI": "Lichtenštejnsko", "US": "USA", "CA": "Kanada", "MX": "Mexiko", "BR": "Brazílie", "AR": "Argentina",
    "CL": "Chile", "PE": "Peru", "CO": "Kolumbie", "CR": "Kostarika", "CU": "Kuba", "DO": "Dominikánská republika",
    "JM": "Jamajka", "BS": "Bahamy", "JP": "Japonsko", "KR": "Jižní Korea", "CN": "Čína", "TW": "Tchaj-wan",
    "HK": "Hongkong", "TH": "Thajsko", "VN": "Vietnam", "ID": "Indonésie", "MY": "Malajsie", "SG": "Singapur",
    "PH": "Filipíny", "IN": "Indie", "NP": "Nepál", "LK": "Srí Lanka", "MV": "Maledivy", "AE": "Spojené arabské emiráty",
    "IL": "Izrael", "JO": "Jordánsko", "EG": "Egypt", "MA": "Maroko", "TN": "Tunisko", "ZA": "Jihoafrická republika",
    "KE": "Keňa", "TZ": "Tanzanie", "NA": "Namibie", "BW": "Botswana", "AU": "Austrálie", "NZ": "Nový Zéland",
    "FJ": "Fidži", "VI": "Americké Panenské ostrovy", "BB": "Barbados", "BZ": "Belize", "BM": "Bermudy", "BO": "Bolívie",
    "CV": "Kapverdy", "BQ": "Karibské Nizozemsko", "SV": "Salvador", "GD": "Grenada", "GP": "Guadeloupe", "HN": "Honduras",
    "LB": "Libanon", "MQ": "Martinik", "MU": "Mauricius", "PA": "Panama", "PR": "Portoriko", "SN": "Senegal", "SC": "Seychely",
    "SX": "Svatý Martin", "UY": "Uruguay", "VE": "Venezuela", "ZM": "Zambie", "EC": "Ekvádor", "AW": "Aruba", "CW": "Curaçao",
    "GT": "Guatemala", "NI": "Nikaragua", "PY": "Paraguay", "EE": "Estonsko", "AM": "Arménie", "LT": "Litva", "LU": "Lucembursko", "AQ": "Antarktida", "GL": "Grónsko", "FO": "Faerské ostrovy", "RU": "Rusko", "GE": "Gruzie",
}


def nazev(kod):
    k = (kod or "").strip().upper()
    return ZEME.get(k, k)


# zaměření kamery → sjednocená skupina (ruční seznam i sběr používaly různé názvy)
TEMA = {"město": "Města", "mesto": "Města", "hory": "Hory", "železnice": "Doprava", "letiště": "Doprava", "doprava": "Doprava",
        "přístav": "Voda", "moře": "Voda", "voda": "Voda", "ptáci": "Ptáci", "zvířata": "Zvířata", "příroda": "Příroda"}
TEMA_SVET = {"Města": "Města", "Hory": "Hory", "Doprava": "Doprava (nádraží, letiště)", "Voda": "Moře, pláže a přístavy",
             "Ptáci": "Ptáci", "Zvířata": "Zvířata", "Příroda": "Příroda", "Ostatní": "Ostatní"}
DOMACI = ("CZ",)          # země dělené podle zaměření (ostatní jen podle země)


def tema(cam):
    return TEMA.get((cam.get("category") or "").strip().lower(), "Ostatní")


VELKA_ZEME = 150      # země s víc kamerami se dělí podle zaměření jako Česko (USA, Itálie…)


def skupina(cam, cfg, podle=None, cz=None, velke=()):
    """kategorie v playlistu / bouquetu (jedna úroveň – M3U ani bouquet strom neumí):
    výchozí: země, u domácích zemí „Česko – Hory“ podle zaměření (cz=kraj → „Česko – Liberecký kraj“);
    podle=tema: celý svět podle zaměření („Hory“, „Moře, pláže a přístavy“…); config group_by=group: volná skupina"""
    podle = podle or cfg.get("group_by", "country")
    if podle == "tema":
        return TEMA_SVET.get(tema(cam), "Ostatní")
    kod = (cam.get("country") or "").strip().upper()
    if podle == "country" and kod:
        if kod in DOMACI or kod in velke:
            g = (cam.get("group") or "").strip()
            if kod in DOMACI and (cz or cfg.get("cz_by", "tema")) == "kraj" and g and g != "Ostatní":
                return nazev(kod) + " – " + g
            t = tema(cam)
            return nazev(kod) + " – " + (TEMA_SVET.get(t, t) if kod not in DOMACI else t)
        return nazev(kod)
    return cam.get("group") or "Ostatní"
