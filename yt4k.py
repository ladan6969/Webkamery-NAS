"""YT_4K_V1 (16. 11. 2026) – kamera z YouTube ve 4K (Kodi i přehrávač ve správě), volba „uhd“ u kamery.

Na kameru běží nejvýš jeden stahovač (yt4k_proud.py, vlastní venv yt4k/venv) a jeho fragmentované MP4 se rozesílá
všem divákům: nový divák dostane hlavičku (ftyp+moov) a naváže od dalšího fragmentu (začíná klíčovým snímkem).
PO token dává bgutil server (Deno, 127.0.0.1:4416), který se spustí při prvním 4K a po POT_NECINNOST bez 4K se zastaví.
Bez instalace (yt4k/venv, yt4k/bgutil) je funkce vypnutá – volající použije běžnou cestu (HLS do 1080p).
"""
import asyncio
import logging
import os
import re
import time

import store

log = logging.getLogger("webcams")

KOREN = store.path("yt4k")
PY = os.path.join(KOREN, "venv", "bin", "python")
DENO = os.path.join(KOREN, "venv", "bin", "deno")
BGUTIL = os.path.join(KOREN, "bgutil", "server")
DENO_DIR = os.path.join(KOREN, "deno-cache")
SKRIPT = store.path("yt4k_proud.py")
POT_PORT = 4416
POT_NECINNOST = 600      # s bez 4K → zastavit server tokenů
DOBEH = 20               # s po odchodu posledního diváka stahovač ještě běží (Kodi se při spuštění připojuje víckrát)
FRONTA = 6               # fragmentů na diváka; pomalejší divák se odpojí

_proudy = {}
_pot = {"proc": None, "posledni": 0.0}


def dostupne(cfg):
    if not cfg.get("yt4k", True):
        return False
    deno = DENO if os.path.exists(DENO) else store.path("venv/bin/deno")
    return all(os.path.exists(p) for p in (PY, SKRIPT, os.path.join(BGUTIL, "src", "main.ts"), deno))


def _deno():
    return DENO if os.path.exists(DENO) else store.path("venv/bin/deno")


def video_id(cam, url_hls=None):
    """ID videa z adresy kamery (watch?v=, youtu.be/, /live/) nebo z vyřešené HLS adresy YouTube (/id/<ID>.1/)"""
    for t in (cam.get("source") or "", url_hls or ""):
        m = re.search(r"(?:[?&]v=|youtu\.be/|/live/|/embed/|/id/)([A-Za-z0-9_-]{11})(?:[.&?/#]|$)", t)
        if m:
            return m.group(1)
    return None


async def _port_bezi(port):
    try:
        r, w = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 2)
        w.close()
        return True
    except Exception:
        return False


async def _zajisti_pot():
    _pot["posledni"] = time.time()
    if await _port_bezi(POT_PORT):
        return True
    p = _pot["proc"]
    if p is None or p.returncode is not None:
        env = dict(os.environ, DENO_DIR=DENO_DIR)
        nm = os.path.join(BGUTIL, "node_modules")
        log.info("4K: spouštím server PO tokenů (bgutil)")
        _pot["proc"] = await asyncio.create_subprocess_exec(
            _deno(), "run", "--allow-env", "--allow-net", "--allow-ffi=.", "--allow-read=.", "../src/main.ts", "--port", str(POT_PORT),
            cwd=nm, env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        asyncio.get_running_loop().create_task(_hlidac_pot())
    for _ in range(40):
        if await _port_bezi(POT_PORT):
            return True
        await asyncio.sleep(0.5)
    log.warning("4K: server PO tokenů nenaběhl")
    return False


async def _hlidac_pot():
    while True:
        await asyncio.sleep(60)
        p = _pot["proc"]
        if p is None or p.returncode is not None:
            return
        if not _proudy and time.time() - _pot["posledni"] > POT_NECINNOST:
            log.info("4K: zastavuji server PO tokenů (nečinnost)")
            p.terminate()
            try:
                await asyncio.wait_for(p.wait(), 10)
            except asyncio.TimeoutError:
                p.kill()
            _pot["proc"] = None
            return


class Proud:
    def __init__(self, cid, vid):
        self.cid, self.vid = cid, vid
        self.init = b""
        self.pripraveno = asyncio.Event()
        self.klienti = set()
        self.proc = None
        self.konec = False
        self.chyba = ""
        self._stop = None

    async def start(self, cfg):
        env = dict(os.environ, VYSKA=str(cfg.get("yt4k_vyska", 2160)), DENO=_deno(), POT_URL="http://127.0.0.1:%d" % POT_PORT,
                   PYTHONUNBUFFERED="1")
        self.proc = await asyncio.create_subprocess_exec(
            PY, SKRIPT, "https://www.youtube.com/watch?v=" + self.vid, env=env, cwd=store.BASE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=1 << 20)
        loop = asyncio.get_running_loop()
        loop.create_task(self._cti())
        loop.create_task(self._chyby())
        log.info("4K %s: start (video %s)", self.cid, self.vid)

    async def _chyby(self):
        async for radek in self.proc.stderr:
            t = radek.decode("utf-8", "replace").strip()
            if t:
                self.chyba = t[:300]
                log.info("4K %s: %s", self.cid, self.chyba)

    async def _cti(self):
        buf = bytearray()
        frag = bytearray()
        try:
            while True:
                b = await self.proc.stdout.read(1 << 18)
                if not b:
                    break
                buf += b
                while len(buf) >= 8:
                    vel = int.from_bytes(buf[0:4], "big")
                    typ = bytes(buf[4:8])
                    if vel == 1:
                        if len(buf) < 16:
                            break
                        vel = int.from_bytes(buf[8:16], "big")
                    if vel < 8:                          # poškozený proud
                        raise ValueError("MP4 box velikosti %d" % vel)
                    if len(buf) < vel:
                        break
                    box = bytes(buf[:vel])
                    del buf[:vel]
                    if typ in (b"ftyp", b"moov"):
                        self.init += box
                        if typ == b"moov":
                            self.pripraveno.set()
                    elif typ == b"moof":
                        frag = bytearray(box)
                    elif typ == b"mdat" and frag:
                        frag += box
                        self._rozeslat(bytes(frag))
                        frag = bytearray()
                    elif frag:
                        frag += box
        except Exception as ex:
            log.warning("4K %s: čtení %s", self.cid, ex)
        finally:
            self.konec = True
            self.pripraveno.set()
            for q in list(self.klienti):
                self._vloz(q, None)
            if self.proc.returncode is None:
                self.proc.kill()
            kod = await self.proc.wait()
            log.info("4K %s: konec (kód %s)", self.cid, kod)
            if _proudy.get(self.cid) is self:
                del _proudy[self.cid]

    @staticmethod
    def _vloz(q, x):
        try:
            q.put_nowait(x)
        except asyncio.QueueFull:
            pass

    def _rozeslat(self, frag):
        for q in list(self.klienti):
            if q.full():                                 # divák nestíhá → odpojit (jinak by rostla paměť)
                self.klienti.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(None)
            else:
                q.put_nowait(frag)

    def ukoncit(self):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()

    async def divak(self):
        q = asyncio.Queue(FRONTA)
        self.klienti.add(q)
        if self._stop:
            self._stop.cancel()
            self._stop = None
        try:
            yield self.init
            while True:
                x = await q.get()
                if x is None:
                    return
                yield x
        finally:
            self.klienti.discard(q)
            if not self.klienti and not self.konec:
                self._stop = asyncio.get_running_loop().call_later(DOBEH, self._bez_divaku)

    def _bez_divaku(self):
        if not self.klienti:
            log.info("4K %s: žádný divák, končím", self.cid)
            self.ukoncit()


async def proud(cfg, cid, vid, cekat=60):
    """běžící (nebo nově spuštěný) proud kamery; None = 4K teď nejde (volající použije běžnou cestu)"""
    p = _proudy.get(cid)
    if p and (p.konec or p.vid != vid):
        p.ukoncit()
        p = None
    if not p:
        if len(_proudy) >= int(cfg.get("yt4k_max", 2)):
            log.info("4K %s: limit souběžných 4K (%s)", cid, cfg.get("yt4k_max", 2))
            return None
        if not await _zajisti_pot():
            return None
        p = Proud(cid, vid)
        _proudy[cid] = p
        await p.start(cfg)
    try:
        await asyncio.wait_for(p.pripraveno.wait(), cekat)
    except asyncio.TimeoutError:
        log.warning("4K %s: do %d s nepřišel obraz", cid, cekat)
        return None
    if p.konec or not p.init:
        return None
    _pot["posledni"] = time.time()
    return p


def stav():
    return {cid: {"divaku": len(p.klienti), "video": p.vid, "chyba": p.chyba} for cid, p in _proudy.items()}
