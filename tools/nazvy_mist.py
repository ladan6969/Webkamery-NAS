#!/usr/bin/env python3
"""Názvy kamer bez místa (4. 11. 2026, uživatel: „Kamera JIH atd bez místa – patří to Brno hvězdárna“).
Název z YouTube přenosu často místo nemá („Kamera JIH“, „Live Cam 2“) – místo je v názvu kanálu.
Najde kamery z YouTube, jejichž název po odebrání obecných slov (kamera, live, světová strana, číslo, HD…) nic nenese,
zjistí jméno kanálu (yt-dlp, mezipaměť tools/kanaly.json) a navrhne „Kanál – Kamera JIH“.
  python3 tools/nazvy_mist.py CAMS.json [VYSTUP.json]   → mapování {id: nový název} (nic nemění)
"""
import json, os, re, subprocess, sys, time
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KANALY = os.path.join(BASE, "tools", "kanaly.json")
OBECNE = r"""kamera|webkamera|web|camera|cam|webcam|cams|live|livestream|stream|streaming|online|hd|uhd|fhd|4k|2k|1080p|720p|2160p|
sever|jih|západ|zapad|východ|vychod|severovýchod|severozápad|jihovýchod|jihozápad|north|south|east|west|nord|sud|süd|ost|
pohled|výhled|vyhled|view|směr|smer|na|do|z|a|the|of|on|live-cam|24/7|247|nonstop|1|2|3|4|5|6|7|8|9|10|č|no|nr|č\."""
OBECNE_RE = re.compile(r"\b(?:%s)\b" % OBECNE.replace("\n", ""), re.I)

def obecny(nazev):
    zbytek = OBECNE_RE.sub(" ", nazev or "")
    zbytek = re.sub(r"[\W_]+", " ", zbytek).strip()
    return len(zbytek) < 3

def kanal(url, cache):
    if url in cache: return cache[url]
    out = ""
    try:
        r = subprocess.run([os.path.join(BASE, "venv/bin/yt-dlp"), "--flat-playlist", "-I", "1", "--print", "%(playlist_title)s",
                            url.rstrip("/") + "/streams"], capture_output=True, text=True, timeout=60)
        out = (r.stdout.strip().splitlines() or [""])[0]
        out = re.sub(r"\s*-\s*(Live|Videos|Streams|Živě)\s*$", "", out).strip()
        if out in ("NA", ""): out = ""
    except Exception:
        pass
    cache[url] = out
    json.dump(cache, open(KANALY, "w"), ensure_ascii=False, indent=1)
    time.sleep(1)
    return out

def main():
    cams = json.load(open(sys.argv[1]))
    cache = json.load(open(KANALY)) if os.path.exists(KANALY) else {}
    mapa, bez = {}, []
    for c in cams:
        n = c.get("name", "")
        if not obecny(n): continue
        src = c.get("source", "")
        if "youtube.com/" not in src: bez.append(n); continue
        k = kanal(src, cache)
        if not k: bez.append(n); continue
        if k.lower() in n.lower(): continue
        mapa[c["id"]] = "%s – %s" % (k, n.strip())
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(BASE, "tools", "nazvy_mist.json")
    json.dump(mapa, open(out, "w"), ensure_ascii=False, indent=1)
    print("přejmenovat: %d, obecný název bez kanálu (nechávám): %d → %s" % (len(mapa), len(bez), out))
    for i, (cid, n) in enumerate(mapa.items()):
        if i < 25: print("  ", n)
    if bez: print("bez kanálu:", ", ".join(bez[:20]))

if __name__ == "__main__":
    main()
