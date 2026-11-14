"""中日地震预警监视台 —— 本地服务

用法：python server.py [--port 8765] [--no-browser]
浏览器打开 http://127.0.0.1:8765/
"""
import argparse
import json
import ssl
import math
import re
import os
import queue
import random
import shutil
import sys
import threading
import time
import traceback
import datetime
import urllib.parse
import urllib.request
import webbrowser
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler

import sources
import history
import auth
import stations as station_data
import ttable
try:
    import gq_client
except Exception:
    gq_client = None

FROZEN = bool(getattr(sys, "frozen", False))
# 打包后：资源在 _MEIPASS，可写数据放在 exe 旁边
APP_DIR = os.path.dirname(sys.executable) if FROZEN else \
    os.path.dirname(os.path.abspath(__file__))
RES_DIR = getattr(sys, "_MEIPASS", APP_DIR)
WEB_DIR = os.path.join(RES_DIR, "web")
DATA_DIR = os.path.join(APP_DIR, "data")


def _data_file(name):
    """可写数据目录里的文件；打包时随 exe 带的那份先复制出来（只复制一次）"""
    dst = os.path.join(DATA_DIR, name)
    if os.path.exists(dst):
        return dst
    src = os.path.join(RES_DIR, "data", name)
    if os.path.exists(src) and os.path.abspath(src) != os.path.abspath(dst):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            shutil.copyfile(src, dst)
            return dst
        except Exception:
            return src
    return dst


import pointgeo
POINTGEO = pointgeo.PointGeo(os.path.join(DATA_DIR, "point_geo.json"),
                             _data_file("jp_places.json"))
_geo_busy = [False]
CUSTOM_STATIONS = os.path.join(DATA_DIR, "custom_stations.json")
USER_DATA = os.path.join(DATA_DIR, "users")


def user_dir(name):
    safe = re.sub(r"[^\w\u4e00-\u9fa5.-]", "_", name or "_")[:48] or "_"
    p = os.path.join(USER_DATA, safe)
    os.makedirs(p, exist_ok=True)
    return p


def user_file(name, fn):
    return os.path.join(user_dir(name), fn)
VERSION = "1.11.1"

# ---------------- 可调阈值（settings.json 里的 gqMag） ----------------
# 前端设置面板改「全球检测最小震级」后会 POST /api/settings 落盘，
# 这里按文件 mtime 惰性重读，列表和进行中事件都用同一个阈值筛。
_SET_CACHE = {"mtime": 0.0, "data": {}}


def _load_settings():
    p = os.path.join(DATA_DIR, "settings.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return {}
    if mt != _SET_CACHE["mtime"]:
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            _SET_CACHE["data"] = d if isinstance(d, dict) else {}
            _SET_CACHE["mtime"] = mt
        except (OSError, ValueError):
            pass
    return _SET_CACHE["data"]


def _gq_min_mag():
    try:
        v = float(_load_settings().get("gqMag", 3.0))
    except (TypeError, ValueError):
        v = 3.0
    return max(0.5, min(8.0, v))

# 桌面版：声音交给 Windows 直接播，绕开浏览器自动播放限制
DESKTOP = os.environ.get("EEW_DESKTOP") == "1"
try:
    import winsound
    HAVE_WINSOUND = True
except Exception:
    winsound = None
    HAVE_WINSOUND = False

# ---------------- 声音调度 ----------------
# 以前是一条「evt 队列」把所有播报串起来：队列一堵后面全延误，而且不同类别
# 共用同一个 MCI 别名，警报音一来就把正在念的人声掐断——这就是「有时候声音乱掉」。
# 现在改成「每个声音独占一条音频实例」：
#   · 不同类别各播各的，互不打断、互不排队（alarm / event / st0 / st1 / prev）
#   · 同一类别的新请求顶掉同类旧请求，两句人声不会叠着念
#   · 固定 10 条 MCI 别名当池子，播完自动归还，不够用时抢最早那一条
# 没有 MCI 时退回 winsound 单流播放。
_SND_ALIAS_N = 10
_SND_ALIASES = ["eew_p%d" % i for i in range(_SND_ALIAS_N)]
_SND_FREE = list(_SND_ALIASES)      # 空闲别名
_SND_SLOT = {}                      # 类别 → 正在使用的别名
_SND_GEN = {}                       # 类别 → 代号（新请求 +1，旧线程看到变了就退出）
_SND_POOL = threading.Lock()
_SND_CV = threading.Condition()
_MCI_LOCK = threading.Lock()
_MCI_ERR = [0]     # 最近一次 MCI 错误码，/api/sound/log 里带上，方便排查「有时候没声」
_SND_LOG = []      # 最近实际播放/被丢弃的记录，GET /api/sound/log 可查
_SND_LAST = {}     # 去重：tag|文件名 -> 上次请求时间

try:
    import ctypes
    _MCI = ctypes.windll.winmm.mciSendStringW
    HAVE_MCI = True
except Exception:
    _MCI = None
    HAVE_MCI = False


def _wav_dur(p):
    try:
        import wave
        with wave.open(p, "rb") as w:
            return w.getnframes() / float(w.getframerate() or 1)
    except Exception:
        return 0.0


def _mci(cmd):
    """发一条 MCI 命令，返回 (错误码, 输出)"""
    try:
        buf = ctypes.create_unicode_buffer(256)
        code = _MCI(cmd, buf, 256, 0)
        return code, buf.value
    except Exception:
        return -1, ""


def _mci_play(alias, path):
    """在指定别名上开一个新实例并播放（同别名会先关掉，不同别名可以并存）

    偶尔会碰上「设备正被别的程序占着」「上一条还没关干净」导致 open 失败，
    这时候整条语音就没声了。所以失败后稍等再试一次，仍然失败就记下错误码，
    由调用方退回 winsound，宁可品质差一点也不要静音。
    """
    with _MCI_LOCK:
        for attempt in (0, 1):
            _mci("stop " + alias)
            _mci("close " + alias)
            code, _ = _mci('open "%s" type waveaudio alias %s' % (path, alias))
            if not code:
                code, _ = _mci("play " + alias)
                _MCI_ERR[0] = code
                return code == 0
            _MCI_ERR[0] = code
            if attempt == 0:
                time.sleep(0.06)
        return False


def _mci_stop(alias):
    with _MCI_LOCK:
        _mci("stop " + alias)
        _mci("close " + alias)


def _mci_volume(alias, v):
    """调某个别名的音量（0~1000）。台站语音说话时把事件语音压下去，两条都听得清。"""
    with _MCI_LOCK:
        _mci("setaudio %s volume to %d" % (alias, max(0, min(1000, int(v)))))


def _snd_record(kind, tag, files, note=""):
    with _SND_CV:
        _SND_LOG.append({
            "t": round(time.time(), 2), "kind": kind, "tag": tag or "-",
            "files": [os.path.basename(f) for f in files], "note": note,
        })
        if len(_SND_LOG) > 200:
            del _SND_LOG[:-150]


def _play_fallback(path):
    try:
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
        return True
    except Exception:
        return False


def _play_one(alias, path):
    """播一个文件，返回 (是否出声, MCI 错误码)。"""
    if HAVE_MCI:
        if _mci_play(alias, path):
            return True, 0
        if HAVE_WINSOUND:
            # winsound 是单流，一放就把正在响的其它声音全掐掉。只有确实没人在播
            # 的时候才退它，否则宁可这一声哑掉，也别炸掉正在念的播报。
            with _SND_POOL:
                busy = len(_SND_SLOT) > 1
            if not busy and _play_fallback(path):
                return True, _MCI_ERR[0]
        return False, _MCI_ERR[0]
    return _play_fallback(path), 0


def _snd_alloc(tag):
    """给这一类别分配一条独占别名。同类旧的一路先停掉，不同类别各播各的。"""
    with _SND_POOL:
        old = _SND_SLOT.pop(tag, None)
        if old is not None:
            if HAVE_MCI:
                _mci_stop(old)
            _snd_record("cut", tag, [], "旧的一路被新的顶掉")
            if old not in _SND_FREE:
                _SND_FREE.append(old)
        alias = _SND_FREE.pop() if _SND_FREE else None
        if alias is None:                      # 池子用光：抢最早占着的那条
            victim = next(iter(_SND_SLOT.items()), None)
            if victim:
                _SND_SLOT.pop(victim[0], None)
                alias = victim[1]
                _SND_GEN[victim[0]] = _SND_GEN.get(victim[0], 0) + 1
        if alias is None:
            return None, 0
        _SND_SLOT[tag] = alias
        gen = _SND_GEN.get(tag, 0) + 1
        _SND_GEN[tag] = gen
        return alias, gen


def _snd_release(tag, alias, gen):
    """播放线程收尾：只有自己那一代还占着这条别名时才归还。
    别名会被新请求立刻回收复用，按 gen 判定才不会把新请求的占用误删掉。"""
    with _SND_POOL:
        if _SND_SLOT.get(tag) == alias and _SND_GEN.get(tag) == gen:
            _SND_SLOT.pop(tag, None)
            if alias not in _SND_FREE:
                _SND_FREE.append(alias)


def _duck_event(on):
    """台站音响的时候把事件人声压低到 32%，两条一起响也听得清"""
    if not HAVE_MCI:
        return
    with _SND_POOL:
        alias = _SND_SLOT.get("event")
    if alias:
        _mci_volume(alias, 320 if on else 1000)


def _snd_play(tag, files, delay=0.0):
    """把一段语音播到它自己的别名上（不阻塞调用方）"""
    alias, gen = _snd_alloc(tag)
    if alias is None:
        _snd_record("fail", tag, files, "no-alias")
        return False
    duck = tag.startswith("st") and HAVE_MCI

    def run():
        if delay > 0:
            time.sleep(delay)
        if _SND_GEN.get(tag) != gen:
            _snd_release(tag, alias, gen)
            return
        if duck:
            _duck_event(True)
        try:
            for p in files:
                if _SND_GEN.get(tag) != gen:
                    if HAVE_MCI:
                        _mci_stop(alias)
                    break
                ok, err = _play_one(alias, p)
                if not ok:
                    _snd_record("fail", tag, [p], "mci=%d" % err)
                    continue
                if err:
                    _snd_record("fallback", tag, [p], "mci=%d" % err)
                _snd_record("play", tag, [p])
                end = time.time() + max(0.2, _wav_dur(p) + 0.04)
                while time.time() < end:
                    if _SND_GEN.get(tag) != gen:
                        if HAVE_MCI:
                            _mci_stop(alias)
                        break
                    time.sleep(0.03)
        finally:
            if duck:
                _duck_event(False)
            _snd_release(tag, alias, gen)

    threading.Thread(target=run, daemon=True).start()
    return True


# 台站音两路轮换：最多两路同时响，第三声才顶掉最早那一路
_SND_ST_SLOT = [0]


def _snd_enqueue(files, prio=0, tag="", ttl=8.0):
    """alarm → 警报音；event → 人声播报；st → 台站震度；preview → 停全部再试听

    每个类别各占一条别名，互不排队、互不打断。event 晚 0.25s 起播，
    让警报音先亮出来，但不必等它播完（以前就是串在一起等，听起来慢半拍）。
    """
    if not files or not (HAVE_MCI or HAVE_WINSOUND):
        return False
    # 同一组文件 + 同一个 tag，短时间内只播一次：
    # 两个窗口（桌面窗口 + 浏览器标签）同时开着也不会叠着念两遍
    files = list(dict.fromkeys(files))
    key = "%s|%s" % (tag, ",".join(os.path.basename(f) for f in files))
    now0 = time.time()
    # 台站音允许同一档连着响（两声一组），去重窗口收窄到 0.15s，否则第二声被吞
    dup_win = 0.15 if tag == "st" else 1.5
    if now0 - _SND_LAST.get(key, 0) < dup_win:
        _snd_record("skip-dup", tag, files)
        return False
    _SND_LAST[key] = now0
    if len(_SND_LAST) > 120:
        _SND_LAST.clear()

    if tag == "preview":
        _stop_all()
        return _snd_play("prev", files)

    if tag == "st":
        _SND_ST_SLOT[0] = 1 - _SND_ST_SLOT[0]
        return _snd_play("st%d" % _SND_ST_SLOT[0], files)

    if tag == "alarm":
        return _snd_play("alarm", files)

    # 人声播报：稍等半拍，别被警报音的三连音盖住开头
    return _snd_play("event", files, delay=0.25)


def _stop_tag(tag):
    """停掉某一类别当前那一路（只影响它自己，别的类别继续播）"""
    with _SND_POOL:
        alias = _SND_SLOT.pop(tag, None)
        _SND_GEN[tag] = _SND_GEN.get(tag, 0) + 1
        if alias is not None and alias not in _SND_FREE:
            _SND_FREE.append(alias)
    if alias is not None and HAVE_MCI:
        _mci_stop(alias)


def _stop_station():
    """只停台站那两路（最终报之后不该再冒人声）"""
    for tag in ("st0", "st1"):
        _stop_tag(tag)


def _stop_all():
    """全停：所有类别、所有别名"""
    with _SND_POOL:
        tags = list(_SND_SLOT.keys())
    for t in tags:
        _stop_tag(t)
    if HAVE_MCI:
        for a in list(_SND_ALIASES):
            _mci_stop(a)
    else:
        _snd_halt()


def _snd_halt():
    if winsound:
        try:
            winsound.PlaySound(None, 0)
        except Exception:
            pass


LOCK = threading.RLock()   # 可重入：防止锁内再进 build_state() 造成自死锁


class Hub:
    """SSE 客户端广播"""

    def __init__(self):
        self.clients = []
        self.lock = threading.Lock()

    def add(self):
        q = queue.Queue(maxsize=8)
        with self.lock:
            self.clients.append(q)
        return q

    def drop(self, q):
        with self.lock:
            if q in self.clients:
                self.clients.remove(q)

    def broadcast(self, text):
        with self.lock:
            targets = list(self.clients)
        for q in targets:
            try:
                q.put_nowait(text)
            except queue.Full:
                pass


HUB = Hub()

STATE = {
    "ts": 0,
    "version": VERSION,
    "sources": [],
    "eew": {"JP": None, "CN": None, "TW": None},
    "catalogs": {"JP": [], "CN": []},
    "events": [],
    "alerts": [],
    "obs": None,
    "tsunami": None,
    "lpgm": None,
    "sim": None,
    "primary": {},          # {"eew": {...}, "obs": {...}} 每 10 秒重算的「延迟最低源」
    "palert": None,         # 台湾 P-Alert 测站级实时 PGA
    "started": time.time(),
}

SRC_RUNTIME = {}
LOGIN_FAILS = {}          # ip -> [失败时间]


SIM_PRESETS = {
    "JP": [("千葉県東方沖", 34.95, 140.55), ("宮城県沖", 38.35, 142.15),
           ("熊本県熊本地方", 32.75, 130.75), ("北海道胆振地方", 42.70, 141.90)],
    "CN": [("四川雅安芦山", 30.25, 102.95), ("云南大理漾濞", 25.65, 99.95),
           ("甘肃陇南", 33.40, 104.90), ("青海玉树", 33.10, 96.80)],
}

# ---------------- 地名反查 ----------------
# 演练时用户可以在地图上点任意位置当震中。以前这里图省事，从 SIM_PRESETS 里
# random.choice 一个地名贴上去——点在北海道近海却报「熊本県熊本地方」，
# 这就是「地名乱取」。现在按经纬度找最近的市町村/都道府县，自己拼地名。
_PLACES = None
_PLACES_LOCK = threading.Lock()


def _jp_places():
    """扁平化 [(都道府县, 市町村, lat, lon), ...]，只加载一次"""
    global _PLACES
    if _PLACES is not None:
        return _PLACES
    with _PLACES_LOCK:
        if _PLACES is not None:
            return _PLACES
        arr = []
        try:
            p = _data_file("jp_places.json")
            with open(p, encoding="utf-8") as f:
                raw = json.load(f)
            for pref, towns in (raw.get("prefs") or {}).items():
                for t in towns:
                    try:
                        arr.append((pref, str(t[0]), float(t[1]), float(t[2])))
                    except (TypeError, ValueError, IndexError):
                        continue
        except Exception:
            traceback.print_exc()
        _PLACES = arr
        return _PLACES


_COAST = None
_COAST_LOCK = threading.Lock()


_PALERT_TBL = {}


def _palert_stations():
    """P-Alert 测站表（tools/gen_palert.py 生成，786 站，50KB 左右）。

    实时 PGA 只给「站号 → gal」，要画到地图上还得有经纬度。表基本不变，
    加载一次缓存住，别每帧去读盘。
    """
    if _PALERT_TBL.get("stations") is not None:
        return _PALERT_TBL
    try:
        with open(_data_file("palert_stations.json"), encoding="utf-8") as f:
            d = json.load(f)
        _PALERT_TBL.update({
            "version": d.get("version"),
            "ts": d.get("ts"),
            "stations": d.get("stations") or [],
        })
    except Exception:
        _PALERT_TBL.update({"stations": []})
    return _PALERT_TBL


def _coast_places():
    """沿海市町村表 {prefs: {県: [[市町村, lat, lon, 岸lon, 岸lat], ...]}}

    由 tools/gen_coast_places.py 离线生成（用 ne10 陆地数据判定「这个市町村到底
    临不临海」）。海啸预报按这些地名逐一判定，而不是拿 5 个大城市代表整条海岸。
    """
    global _COAST
    if _COAST is not None:
        return _COAST
    with _COAST_LOCK:
        if _COAST is not None:
            return _COAST
        prefs = {}
        try:
            p = _data_file("coast_places.json")
            with open(p, encoding="utf-8") as f:
                prefs = (json.load(f) or {}).get("prefs") or {}
        except Exception:
            traceback.print_exc()
        _COAST = {"prefs": prefs, "n": sum(len(v) for v in prefs.values())}
        return _COAST


def jp_nearest_place(lat, lon):
    """返回 (都道府县, 市町村, 距离km)；找不到返回 None"""
    arr = _jp_places()
    if not arr:
        return None
    best = None
    bd = 1e18
    kx = 111.0 * math.cos(math.radians(max(-80.0, min(80.0, lat))))
    for pref, name, plat, plon in arr:
        dx = (plon - lon) * kx
        dy = (plat - lat) * 111.0
        d = dx * dx + dy * dy
        if d < bd:
            bd = d
            best = (pref, name)
    if not best:
        return None
    return best[0], best[1], math.sqrt(bd)


def place_of(lat, lon, country="JP"):
    """按坐标生成地名（只用于演练，真实事件一律用气象厅/台网电文里的地名）"""
    if country != "JP":
        ns = "北纬" if lat >= 0 else "南纬"
        ew = "东经" if lon >= 0 else "西经"
        return "%s%.1f° %s%.1f° 附近" % (ns, abs(lat), ew, abs(lon))
    r = jp_nearest_place(lat, lon)
    if not r:
        return "不明"
    pref, town, d = r
    if d <= 20.0:
        return pref + town + "付近"
    if d <= 60.0:
        return pref + "中部"
    if d <= 120.0:
        return pref + "沖"
    if d <= 300.0:
        return pref + "遠海"
    # 离日本本土太远（比如把日本选成国家却点在中国/太平洋深处），别硬套县名
    return "北纬%.1f° 东经%.1f° 附近" % (abs(lat), abs(lon))


def pgv_surface(mag, depth, r):
    """地表 PGV（cm/s）。旧気象庁式算的是 Vs600 工学基盘上的 PGV600，
    直接当代入计测震度式会整体低约一档——熊本 M7.3 震中实测震度7、
    3.11 宮城海岸实测 6強，原式都只给 5強。这里补两件事：
      ① 场地放大 1.5 倍（基盘→地表）；
      ② 大震级长周期项：M>6.5 起强震动区明显外扩，M7.3 封顶。
    前端 app.js 的 pgv() 是同一个公式，两端必须保持一致。"""
    rr = max(r, 3.0)
    ml = 10 ** (0.35 * min(max(mag - 6.5, 0.0), 0.8))
    return 1.5 * ml * 10 ** (0.58 * mag + 0.0038 * depth - 1.29 - math.log10(rr) - 0.002 * rr)


def sim_pgv(mag, depth):
    return pgv_surface(mag, depth, depth)


def sim_intensity(mag, depth, country):
    p = sim_pgv(mag, depth)
    if country == "JP":
        v = 2.68 + 1.72 * math.log10(max(p, 0.01))
        if v < 0.5: return "0"
        if v < 1.5: return "1"
        if v < 2.5: return "2"
        if v < 3.5: return "3"
        if v < 4.5: return "4"
        if v < 5.0: return "5弱"
        if v < 5.5: return "5強"
        if v < 6.0: return "6弱"
        if v < 6.5: return "6強"
        return "7"
    du = 6 + math.log2(max(p / 100.0, 1e-5) / 0.06)
    return str(round(max(1, min(12, du)), 1))


def station_intensity(mag, depth, lat, lon, country):
    """事件的最大震度 = 所有台站里最高的那一档。

    以前这里直接拿「深度」当震中距代进衰减式，算出来的值比地图上任何一个
    台站都高一整档——于是语音念「震度5弱」，地图上最亮的台站却只到震度4，
    听起来就是"念了但颜色没变"。改成遍历台站取最大，语音、区块、台站三处
    永远同一个数。
    """
    if lat is None or lon is None:
        return sim_intensity(mag, depth, country)
    best = -99.0
    for st in station_data.STATIONS:
        if st.get("country") != country:
            continue
        epi = station_data.distance_km(lat, lon, st["lat"], st["lon"])
        r = max(math.hypot(epi, depth), 3.0)
        p = pgv_surface(mag, depth, r)
        v = (2.68 + 1.72 * math.log10(max(p, 0.01))) if country == "JP" \
            else (6 + math.log2(max(p / 100.0, 1e-5) / 0.06))
        if v > best:
            best = v
    if country == "JP":
        return shindo_label(best)
    return str(round(max(1, min(12, best)), 1))


def shindo_label(v):
    if v < 0.5: return "0"
    if v < 1.5: return "1"
    if v < 2.5: return "2"
    if v < 3.5: return "3"
    if v < 4.5: return "4"
    if v < 5.0: return "5弱"
    if v < 5.5: return "5強"
    if v < 6.0: return "6弱"
    if v < 6.5: return "6強"
    return "7"


def sim_pref_intensity(mag, depth, lat, lon):
    """演练用：按台站代表点算每个都道府县的最大预想震度"""
    out = {}
    if lat is None or lon is None:
        return out
    for st in station_data.STATIONS:
        if st.get("country") != "JP":
            continue
        epi = station_data.distance_km(lat, lon, st["lat"], st["lon"])
        r = max(math.hypot(epi, depth), 3.0)
        p = pgv_surface(mag, depth, r)
        v = 2.68 + 1.72 * math.log10(max(p, 0.01))
        lab = shindo_label(v)
        pref = st.get("region") or ""
        cur = out.get(pref)
        if cur is None or v > cur["value"]:
            out[pref] = {"label": lab, "value": round(v, 2), "area": st["name"]}
    return {k: {"label": v["label"], "value": v["value"], "area": v["area"]}
            for k, v in out.items() if v["value"] >= 0.5}


def sim_scale_of(v):
    """计测震度 → JMA scale 数值（10=1, 20=2 ... 70=7），与 sources.SCALE_LABEL 对应"""
    if v < 0.5: return 0
    if v < 1.5: return 10
    if v < 2.5: return 20
    if v < 3.5: return 30
    if v < 4.5: return 40
    if v < 5.0: return 45
    if v < 5.5: return 50
    if v < 6.0: return 55
    if v < 6.5: return 60
    return 70


def sim_obs_report(ev, mag, depth, lat, lon):
    """演练「震度速報」：照气象厅震度速報的电文样式出一条报文。

    震度速報只讲两件事——「最大震度」和「震度○以上を観測した地域」。
    它不是把每个观测点铺到地图上（那是「各地の震度に関する情報」）。
    以前演练把全日本 1700 多个台站逐个算成震度、当成实测点写进
    STATE["obs"]，图上糊成一片亮斑，既不对也不像电文。现在演练只给
    按县聚合的「地域」级速报：地图上是都道府县色块，右侧是一张
    电文卡。真实电文（带 points）来了照旧走同一条路铺实测点。
    """
    if lat is None or lon is None:
        return None
    pref = {}
    n_pts = 0
    n3 = 0
    for st in station_data.STATIONS:
        if st.get("country") != "JP":
            continue
        epi = station_data.distance_km(lat, lon, st["lat"], st["lon"])
        r = max(math.hypot(epi, depth), 3.0)
        p = pgv_surface(mag, depth, r)
        v = 2.68 + 1.72 * math.log10(max(p, 0.01))
        sc = sim_scale_of(v)
        if sc < 10:                       # 震度 0（没摇）不计数
            continue
        region = (st.get("region") or "").strip()
        if not region or region == "-":    # 表里有 6 个台站地区字段是占位符
            continue
        n_pts += 1
        if sc >= 30:                      # 震度速報只报「震度3以上」的地域
            n3 += 1
        cell = pref.get(region)
        if cell is None:
            pref[region] = {"scale": sc, "label": sources.SCALE_LABEL.get(sc, "?"), "n": 1}
        else:
            cell["n"] += 1
            if sc > cell["scale"]:
                cell["scale"] = sc
                cell["label"] = sources.SCALE_LABEL.get(sc, "?")
    if not pref:
        return None

    max_scale = max(c["scale"] for c in pref.values())
    # 电文口径：从最大震度那一档往下，每档列一次「震度○以上を観測した地域」，
    # 震度 3 未満（震度速報不报震度 1/2）不列。
    groups = []
    for sc in sorted({c["scale"] for c in pref.values() if c["scale"] >= 30}, reverse=True):
        names = sorted([k for k, c in pref.items() if c["scale"] >= sc],
                       key=lambda k: (-pref[k]["scale"], k))
        groups.append({"scale": sc, "label": sources.SCALE_LABEL.get(sc, "?"), "prefs": names})

    jst = datetime.timezone(datetime.timedelta(hours=9))
    now_dt = datetime.datetime.now(jst)
    org = datetime.datetime.fromtimestamp(ev.get("origin_ts") or time.time(), jst)
    issue_str = "%d年%d月%d日%02d時%02d分 気象庁発表" % (
        now_dt.year, now_dt.month, now_dt.day, now_dt.hour, now_dt.minute)
    quake_str = "%d日%02d時%02d分頃の地震" % (org.day, org.hour, org.minute)

    return {
        "kind": "obs",
        "report_type": "震度速報",
        "issue_no": 1,
        "issue_str": issue_str,
        "quake_str": quake_str,
        "source": "jma_obs",
        "source_name": "気象庁 震度速報（演練）",
        "origin_ts": ev.get("origin_ts") or time.time(),
        "report_ts": time.time(),
        "place": ev.get("place") or "不明",
        "lat": lat, "lon": lon, "depth": depth, "mag": mag,
        "max_scale": max_scale,
        "max_label": sources.SCALE_LABEL.get(max_scale, "?"),
        "point_count": n3,                # 震度3以上的观测点数（只作参考，不铺图）
        "pref": pref,                     # 都道府县 → 最大震度（地图色块用这个）
        "regions": groups,                # 电文里的「震度○以上を観測した地域」
        "points": [],                     # 演练没有真实观测点，绝不冒充实测
        "scales": dict(sources.SCALE_LABEL),
        "sim": True,
        "estimated": True,
    }


# ---------------- 演练模型（台站驱动） ----------------
# 老版本是「按时间表把震级一格一格往上抬」：第 1 报 M6.2、第 2 报 M6.4……，
# 位置、深度、预想震度全程不动。真实的 EEW 不长这样：台站先摇，系统拿「已经
# 报到摇了」的台站去反演，报得越多解越准，中间还会跳位置、跳深度。
# 这里照同一套逻辑重写：
#   ① 用真实震源算出每个台站「摇到什么程度」「P/S 波什么时候到」；
#   ② 只有 P 波到了、且震度 >= 0.5 的台站才算「报了」（没感觉的不报）；
#   ③ 每一报都只用「此刻已经报了的台站」反演：拿 S-P 走时差做网格搜索，
#      震源位置和深度都是解出来的，不是抄真值——所以前几报会偏、会跳，
#      台站越多越收敛；
#   ④ 终报 = S 波扫过有感范围（虚拟时间有上限，不然一场演练要看几分钟）；
#   ⑤ 震度速報在终报之后再等一会儿才发，绝不在还在摇的时候冒出来。
SIM_SPEED = {"v": 1.0}
# 走时以 AK135 表为准（ttable.py）；这两个常数只在表不可用时兜底
SIM_S_KMPS = 4.2
SIM_P_KMPS = 6.0
SIM_MIN_ST = 3            # 至少 3 个台站报了才开始估算（= 第一报）
SIM_PREROLL = 8.0         # 点下演练时，地震其实已经发生 8 秒（波已经在路上）
# 演练「不设上限」：报数由有感范围决定，震级越大、范围越广，报得越多；
# 终报 = S 波真正扫完有感范围（可能好几分钟，3× 倍速下也就一两分钟）。
# 只保留一个物理上限：S 波表最多查到 2000km（JMA2001）/ 125°（AK135）。
SIM_REPORT_STEP = 8.0     # 每隔多少虚拟秒一报
SIM_MIN_REPORTS = 6       # 再小的地震也至少 6 报，别一闪就完
SIM_OBS_LAG = 15.0        # 终报之后再过这么久才发震度速報
SIM_DETECT_MAX = 0        # 0 = 不限，已报台站全部下发（前端本来就是按点画）
SIM_FIT_ST = 40           # 反演只用摇得最强的这些台站（够用且快）


def _sim_value(country, mag, depth, r):
    """某个斜距 r 上的震度值（JP=计测震度，CN=中国烈度）"""
    p = pgv_surface(mag, depth, r)
    if country == "JP":
        return 2.68 + 1.72 * math.log10(max(p, 0.01))
    return 6 + math.log2(max(p / 100.0, 1e-5) / 0.06)


def _sim_model(country):
    """日本用 JMA2001（气象厅自己的表），其他地方用 AK135。"""
    return "jma2001" if country == "JP" else "ak135"


def _sim_tt(epi, depth, model="ak135"):
    """台站到震中的 P/S 走时（官方表）。表不可用时退回常数速度。"""
    tp = ttable.time_km("P", epi, depth, model)
    ts = ttable.time_km("S", epi, depth, model)
    if tp is None or ts is None:
        r = max(math.hypot(epi, depth), 3.0)
        return r / SIM_P_KMPS, r / SIM_S_KMPS
    return tp, ts


def _sim_field(country, lat, lon, depth, mag):
    """真实场：每个台站摇到什么程度、P/S 波什么时候到（按 P 波到达升序）

    阈值取「有感」：JP 计测震度 0.5（绿框那一档），CN 烈度 1.5。
    没到阈值的不进列表——现实里那些台站也不会报「摇了」。
    """
    out = []
    if lat is None or lon is None:
        return out, 1.0
    thr = 0.5 if country == "JP" else 1.5
    model = _sim_model(country)
    for st in station_data.STATIONS:
        if st.get("country") != country:
            continue
        epi = station_data.distance_km(lat, lon, st["lat"], st["lon"])
        r = max(math.hypot(epi, depth), 3.0)
        v = _sim_value(country, mag, depth, r)
        if v < thr:
            continue
        tp, ts = _sim_tt(epi, depth, model)
        out.append({"lat": st["lat"], "lon": st["lon"], "v": round(v, 2),
                    "epi": epi, "tp": tp, "ts": ts, "dt": ts - tp,
                    "region": st.get("region") or "", "name": st.get("name") or ""})
    out.sort(key=lambda a: a["tp"])
    if not out:
        # 震中选在了离任何台站都很远的地方（比如海里点了个 CN 演练）：
        # 降低阈值再试一轮，还是没有就按「最近的 12 个台站、至少有点微动」兜底，
        # 不然演练会卡在「台站检测中」永远等不到第一报。
        for st in station_data.STATIONS:
            if st.get("country") != country:
                continue
            epi = station_data.distance_km(lat, lon, st["lat"], st["lon"])
            r = max(math.hypot(epi, depth), 3.0)
            v = max(_sim_value(country, mag, depth, r), 0.2 if country == "JP" else 0.8)
            tp, ts = _sim_tt(epi, depth, model)
            out.append({"lat": st["lat"], "lon": st["lon"], "v": round(v, 2),
                        "epi": epi, "tp": tp, "ts": ts, "dt": ts - tp,
                        "region": st.get("region") or "", "name": st.get("name") or ""})
        out.sort(key=lambda a: a["epi"])
        for s in out[:12]:
            s["v"] = max(s["v"], 0.5)
        out = out[:12]
        # 到得太晚的把走时整体压缩到「最近台站 13 秒内到」，不然演练要干等几分钟
        f = 13.0 / max(1.0, out[2]["tp"]) if len(out) > 2 else 1.0
        for s in out:
            s["tp"] *= f
            s["ts"] *= f
            s["dt"] *= f
        out.sort(key=lambda a: a["tp"])
        return out, f
    return out, 1.0


def _sim_felt_km(country, mag, depth, level):
    """震度 >= level 的地表半径（km）：二分反解衰减式"""
    lo, hi = 3.0, 3000.0
    for _ in range(40):
        mid = (lo + hi) / 2.0
        if _sim_value(country, mag, depth, math.hypot(mid, depth)) >= level:
            lo = mid
        else:
            hi = mid
    return lo


def _sim_locate(seen, country, rng, model="ak135"):
    """只用已报台站的 S-P 走时差反演震源（位置 + 深度）

    走时差是「每站自己量自己」的量，跟发震时刻无关，所以能压住深度——
    只用 S 波到时的话深度和发震时刻会互相抵消，解出来永远是 10km。
    早期台站少、方位又挤在一边，解会明显偏（这正是 EEW 前几报的样子）；
    台站铺开之后很快收敛。
    """
    use = sorted(seen, key=lambda s: -s["v"])[:SIM_FIT_ST]
    n = max(1, len(use))
    w = [max(0.3, s["v"]) ** 2 for s in use]
    ws = sum(w) or 1.0
    clat = sum(s["lat"] * wi for s, wi in zip(use, w)) / ws
    clon = sum(s["lon"] * wi for s, wi in zip(use, w)) / ws
    # 读数误差：3 个站的时候一秒多（走时读数本来就糙），几十个站之后只剩几十毫秒
    sig = 1.4 * math.exp(-n / 12.0) + 0.05
    obs = [(s["lat"], s["lon"], s["dt"] + rng.gauss(0, sig)) for s in use]

    def score(la, lo, dep):
        # 预测的 S-P 差直接查 AK135 表（与台站「报到」用的同一张表）
        tot = 0.0
        for a, b, t in obs:
            dist_km = station_data.distance_km(la, lo, a, b)
            dt_pred = ttable.dt_km(dist_km, dep, model)
            if dt_pred is None:
                dt_pred = math.hypot(dist_km, dep) * (
                    1.0 / SIM_S_KMPS - 1.0 / SIM_P_KMPS)
            d = dt_pred - t
            tot += d * d
        return tot / n

    best = (1e18, clat, clon, 30.0)
    for dep in (5, 10, 20, 30, 40, 60, 80, 100, 130, 170):
        for dlat in range(-6, 7):
            for dlon in range(-6, 7):
                la = clat + dlat * 25.0 / 111.0
                lo = clon + dlon * 25.0 / (111.0 * max(0.25, math.cos(math.radians(clat))))
                s = score(la, lo, dep)
                if s < best[0]:
                    best = (s, la, lo, float(dep))
    _, bl, bg, bd = best
    for dep in (max(5.0, bd - 10), bd, bd + 10):
        for dlat in range(-4, 5):
            for dlon in range(-4, 5):
                la = bl + dlat * 5.0 / 111.0
                lo = bg + dlon * 5.0 / (111.0 * max(0.25, math.cos(math.radians(bl))))
                s = score(la, lo, dep)
                if s < best[0]:
                    best = (s, la, lo, dep)
    # 解本身还有一层「模型误差」：每报重算都会漂几公里，台站越多漂得越少。
    # 没有这一层，后几报会像复制粘贴一样一动不动，看着反而不像估算。
    drift = 9.0 * math.exp(-n / 45.0) + 2.2
    ang = rng.uniform(0, 2 * math.pi)
    la = best[1] + math.cos(ang) * drift / 111.0
    lo = best[2] + math.sin(ang) * drift / (111.0 * max(0.25, math.cos(math.radians(best[1]))))
    return {"lat": round(la, 3), "lon": round(lo, 3),
            "depth": int(round(best[3])), "n": len(seen),
            "rms": round(math.sqrt(max(0.0, best[0])), 2)}


def _sim_mag(seen, est, true_mag):
    """震级：近场台站先到、震源尺度还没展开，早期系统性偏小

    不做强度反演（近场早就饱和了，反演出来只会更大），就按「台站越多越准」
    收敛；这是 EEW 里最常见的一条：M 从 5.x 一路修到 7.x。
    """
    n = max(1, len(seen))
    return max(2.0, round(true_mag - 2.2 * math.exp(-n / 8.0), 1))


def sim_sequence(uid, true, country, place):
    """按台站反应推进的演练时间轴（虚拟秒，倍速随时可改）

    true = {lat, lon, depth, mag}：只有服务端知道的地震「真值」。
    每次发布出去的都不是真值，而是「此刻已报台站能反演出来的那个解」。
    """
    lat, lon = true["lat"], true["lon"]
    depth, base_mag = true["depth"], true["mag"]
    field, tt_scale = _sim_field(country, lat, lon, depth, base_mag)
    model = _sim_model(country)
    rng = random.Random(uid + "-est")

    # 真值算一次就够：震度是「摇出来的」，不该跟着估算震源漂。
    # 卡片上的震级/深度/位置写估算值，但最大震度和各县预想震度必须用这套——
    # 否则估算偏 100km 时，地图上亮着的台站和卡片报的震度会互相打架。
    max_int_true = station_intensity(base_mag, depth, lat, lon, country)
    pref_true = sim_pref_intensity(base_mag, depth, lat, lon) if country == "JP" else {}

    # tt_scale：兜底场景（震中在远海、附近没台站）会把整条走时曲线压缩，
    # 前端按同一比例缩放虚拟时间，圈和台站亮起仍然严丝合缝。

    # 时间轴：第一报 = 第 3 个台站的 P 波到了（+0.8s 处理时间）；
    # 终报 = S 波扫完有感范围。虚拟 0 秒 = 用户点下按钮的那一刻，
    # 而地震已经发生过 SIM_PREROLL 秒了（波在路上），所以都要减去它。
    pref = SIM_PREROLL
    if len(field) >= SIM_MIN_ST:
        t_first = max(0.6, field[SIM_MIN_ST - 1]["tp"] + 0.8 - pref)
    else:
        t_first = 4.0
    felt = _sim_felt_km(country, base_mag, depth, 1.0 if country == "JP" else 1.5)
    ts_felt = ttable.time_km("S", felt, depth, model)
    if ts_felt is None:
        ts_felt = felt / SIM_S_KMPS
    # 虚拟时刻 τ 对应的真实走时是 τ / tt_scale（压缩过就回到真实时间轴查表）
    t_end = max(t_first + 26.0, ts_felt * tt_scale - pref)   # 不再封顶
    _wave = ttable.dist_km("S", (t_end + pref) / max(1e-6, tt_scale), depth, model)
    wave_km = min(felt, _wave if _wave is not None else felt)

    # 报数跟着时长走：每 ~8 虚拟秒一报，不设上限（大地震自然报得多）
    span = max(1.0, t_end - t_first)
    n_rep = max(SIM_MIN_REPORTS, int(math.ceil(span / SIM_REPORT_STEP)) + 1)
    times = [t_first + (t_end - t_first) * k / (n_rep - 1) for k in range(n_rep)]

    i, obs_done, vt = 0, False, 0.0
    last = time.time()
    while True:
        now = time.time()
        sp = SIM_SPEED["v"] if SIM_SPEED["v"] > 0 else 1.0
        vt += (now - last) * sp
        last = now

        with LOCK:
            cur = STATE.get("sim")
            alive = bool(cur and cur.get("uid") == uid)
        if not alive:
            return

        if i < n_rep and vt >= times[i]:
            t_rep = times[i] + pref          # 换算回「发震后第几秒」
            seen = [s for s in field if s["tp"] <= t_rep]
            if len(seen) < SIM_MIN_ST:
                time.sleep(0.08)
                continue
            est = _sim_locate(seen, country, rng, model)
            mag_e = _sim_mag(seen, est, base_mag)
            i += 1
            with LOCK:
                cur = STATE.get("sim")
                if not cur or cur.get("uid") != uid:
                    return
                ev = dict(cur)
                ev.pop("pending", None)
                ev.update({
                    "lat": est["lat"], "lon": est["lon"],
                    "depth": est["depth"], "mag": mag_e,
                    "serial": i,
                    "report_ts": time.time(),
                    "sim_t": round(t_rep, 2),      # 该报的虚拟时刻（发震后秒数）
                    "speed": sp,
                    # 震度（以及下面的各县预想）一律用真值：摇出来的东西不能被
                    # 估算震源带偏，否则「台站明明在晃、卡片却报没震度」。
                    "max_intensity": max_int_true,
                    "final": i >= n_rep,
                    "sim_wave_km": round(wave_km, 1),
                    "sim_seen": len(seen),
                    "sim_rms": est["rms"],
                    # 真震源 / 真实波速：只给「波前 + 台站震度场」用，卡片不显示。
                    # 波前要按真实的 P/S 传播才和台站亮起的时刻对得上；
                    # 靶标和文字仍用上面那套估算值（正确震源是要藏起来的）。
                    "sim_true": {"lat": lat, "lon": lon, "depth": depth,
                                 "mag": round(base_mag, 1)},
                    "sim_tt_scale": round(tt_scale, 4),
                    # 这一报「已经报到的台站」：图上一个个亮起来的就是它们
                    "sim_det": [[s["lat"], s["lon"], s["v"]]
                                for s in (seen if not SIM_DETECT_MAX else seen[:SIM_DETECT_MAX])],
                })
                if country == "JP":
                    ev["pref_intensity"] = pref_true
                STATE["sim"] = ev
                # 与真实报文同构：演练也占 eew 槽位（真实电文一到会被顶掉）
                STATE["eew"][country] = dict(ev)
                STATE["alerts"].insert(0, {
                    "key": "%s#%d" % (uid, i), "uid": uid, "country": country,
                    "place": ev.get("place"), "mag": ev["mag"], "depth": ev["depth"],
                    "max_intensity": ev["max_intensity"], "serial": i,
                    "origin_ts": ev["origin_ts"], "report_ts": ev["report_ts"],
                    "warn": True, "cancel": False, "training": True,
                    "received": time.time(),
                })
                del STATE["alerts"][60:]
            publish()
            continue

        # 震度速報：真实链路也是「EEW 终报 → 摇完 → 气象厅发震度速報」。
        # 这里等终报之后再 SIM_OBS_LAG 虚拟秒（S 波这时已经扫完有感范围）才发。
        if i >= n_rep and not obs_done and vt >= t_end + SIM_OBS_LAG:
            obs = None
            with LOCK:
                cur = STATE.get("sim")
                if cur and cur.get("uid") == uid and cur.get("country") == "JP":
                    obs = sim_obs_report(cur, base_mag, depth, lat, lon)
                    if obs:
                        STATE["obs"] = obs
            obs_done = True
            if obs:
                publish()
            continue

        if i >= n_rep and obs_done and vt >= t_end + SIM_OBS_LAG + 40:
            return
        time.sleep(0.08)


def load_custom(user=None, legacy=False):
    path = user_file(user, "custom_stations.json") if user else CUSTOM_STATIONS
    if user and not os.path.exists(path) and legacy:
        path = CUSTOM_STATIONS            # 旧版全局文件只给管理员兜底
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def save_custom(items, user=None):
    path = user_file(user, "custom_stations.json") if user else CUSTOM_STATIONS
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=1)


def all_stations(user=None, legacy=False):
    return station_data.STATIONS + load_custom(user, legacy)


def poll_once(src):
    """拉一次数据源，成功返回 payload，失败抛异常"""
    if src.get("fetch") == "sokuho":
        return sources.sokuho_fetch()
    if src.get("fetch") == "lpgm":
        # 气象厅长周期：HTML 列表 + Shift_JIS CSV，不能走 JSON 通道
        return sources.lpgm_fetch()
    if src.get("post") == "palert":
        # GraphQL POST，不走 session_json（那条路是 GET + 系统代理）
        return sources.palert_realtime(0)
    tmo = {"eew": 8, "catalog": 25, "obs": 30}.get(src["kind"], 12)
    return sources.session_json(src["url"], timeout=tmo)


def source_loop(src):
    rt = SRC_RUNTIME[src["id"]]
    last_payload = None
    errors = 0
    while True:
        started = time.time()
        try:
            payload = poll_once(src)
            lat = int((time.time() - started) * 1000)
            hist = rt.setdefault("lat_hist", [])
            hist.append(lat)
            del hist[:-5]                          # 只留最近 5 次，给中位数用
            rt.update({
                "ok": True,
                "err": None,
                "latency_ms": lat,
                "last_ok_ts": time.time(),
                "last_ok_str": time.strftime("%H:%M:%S"),
                "errors_total": errors,
                "has_data": True,
            })
            if payload != last_payload:
                last_payload = payload
                apply_payload(src, payload)
                publish()
            errors = 0
        except Exception as exc:
            errors += 1
            rt.update({
                "ok": False,
                "err": "%s: %s" % (type(exc).__name__, exc),
                "latency_ms": int((time.time() - started) * 1000),
                "errors_total": errors,
                "last_err_ts": time.time(),
            })
            publish()
        time.sleep(src["poll"])


def _apply_eew(ev):
    """把一条 EEW 合进 eew 槽位（调用方必须已持 LOCK）。

    同一场地震现在可能有 2~3 个源同时推（Wolfx 轮询 / Jian 长连接 / EQSC），
    谁的报次都一样，但到达顺序会乱。规则：
      · 同一场（uid 相同，或发震时刻相差 60 秒内）→ 只让报次更大的覆盖，
        否则后到的「第 1 报」会把「第 4 报」顶回去，界面会倒退；
      · 不同场次 → 只有发震更晚的才允许顶掉当前，免得旧事件回魂。
    """
    country = ev["country"]
    cur = STATE["eew"].get(country)
    if cur:
        same = (cur.get("uid") == ev.get("uid")) or (
            cur.get("origin_ts") and ev.get("origin_ts")
            and abs(cur["origin_ts"] - ev["origin_ts"]) <= 60)
        if same:
            if (ev.get("serial") or 0) < (cur.get("serial") or 0):
                return False
        elif (ev.get("origin_ts") or 0) <= (cur.get("origin_ts") or 0):
            return False
    STATE["eew"][country] = ev
    key = "%s#%s" % (ev["uid"], ev["serial"])
    prev = cur or {}
    if ("%s#%s" % (prev.get("uid"), prev.get("serial"))) != key:
        STATE["alerts"].insert(0, {
            "key": key,
            "uid": ev["uid"],
            "country": ev["country"],
            "place": ev["place"],
            "mag": ev["mag"],
            "depth": ev["depth"],
            "max_intensity": ev["max_intensity"],
            "serial": ev["serial"],
            "origin_ts": ev["origin_ts"],
            "report_ts": ev["report_ts"],
            "warn": ev["warn"],
            "cancel": ev["cancel"],
            "training": ev["training"],
            "received": time.time(),
        })
        del STATE["alerts"][60:]
    return True


def apply_payload(src, payload):
    sid = src["id"]
    with LOCK:
        if sid == "palert":
            # 测站级实时 PGA：{站号: gal}。不塞进 STATE["obs"]（那是电文，
            # 两套口径不能混），单独存一份，前端按需叠在地图上看「哪一片在摇」。
            STATE["palert"] = {
                "vals": payload.get("vals") or {},
                "ts": payload.get("ts") or "",
                "at": time.time(),
            }
            return
        if src["kind"] == "eew":
            ev = sources.NORMALIZERS[sid](payload)
            if ev:
                _apply_eew(ev)
        elif src["kind"] == "tsunami":
            tsu = sources.apply_of(sid, payload)
            if tsu is not None:
                STATE["tsunami"] = tsu
        elif src["kind"] == "lpgm":
            if payload:
                STATE["lpgm"] = payload
        elif src["kind"] == "sokuho":
            if payload:
                cur = STATE.get("obs")
                # 只让更新的报文上屏：震度速報(10:54) 不能被更早的观测顶掉，
                # 后来的「震源・震度」(10:56) 反而要能接替它。
                if (not cur) or (payload.get("report_ts") or 0) >= (cur.get("report_ts") or 0):
                    STATE["obs"] = payload
        elif src["kind"] == "obs":
            obs = sources.apply_of(sid, payload)
            if obs and obs.get("points"):
                # 电文里只有观测点地点名、没有坐标。先用离线市町村表一次性补上
                # （瞬时，断网也能画），前端拿到的就是「这场地震实际报到的测站」。
                try:
                    POINTGEO.resolve(obs["points"], limit=0)
                except Exception:
                    pass
            # 演练的震度速報刚出来时，别让一场无关的 M3 小震把它顶掉——点演练是
            # 想看完整流程，被真事打断会很莫名（实测里就撞上了）。只保护 3 分钟，
            # 且真实速報报到震度5弱以上时照常接管：那是真的有事，演练让路。
            cur = STATE.get("obs")
            sim = STATE.get("sim")
            protected = bool(
                sim and cur and cur.get("sim")
                and time.time() - (sim.get("report_ts") or 0) < 180
                and (obs or {}).get("max_scale", 0) < 50
            )
            if not protected and obs is not None:
                # 只让更新的电文上屏：P2P 慢的时候会先回旧的一场，
                # 不能把刚到的「震度速報」又顶回上一场地震。
                new_ts = obs.get("report_ts") or obs.get("origin_ts") or 0
                old_ts = (cur or {}).get("report_ts") or (cur or {}).get("origin_ts") or 0
                if (not cur) or new_ts >= old_ts:
                    STATE["obs"] = obs
            if obs and obs.get("points") and not protected:
                enrich_obs_points(obs["points"])   # 离线匹配不上的再慢慢联网补
            items = []
        else:
            items = sources.catalog_of(sid, payload)
            STATE["catalogs"][src["country"]] = items
    if src["kind"] == "catalog":
        try:
            history.harvest(items)
        except Exception:
            pass


def build_state():
    now = time.time()
    minmag = _gq_min_mag()   # 全球检测最小震级（设置面板可调，M1~M8）
    with LOCK:
        gqev = gq_client.recent_events(min_mag=minmag, limit=40) if gq_client else []
        merged = list(STATE["catalogs"]["CN"]) + list(STATE["catalogs"]["JP"]) + list(gqev)
        merged.sort(key=lambda e: e.get("origin_ts") or 0, reverse=True)
        eew = dict(STATE["eew"])
        for c in ("JP", "CN", "TW"):
            e = eew.get(c)
            if e:
                e = dict(e)
                e["active"] = sources.is_active(e, now)
                e["age"] = round(now - (e.get("origin_ts") or now), 1)
                eew[c] = e
        # GlobalQuake 实时检测：刚推过修订、发震不久的全球地震也作为「进行中事件」
        # 交给前端（波前动画/横幅）。active_event 内部已判新鲜度并置 active/age。
        gq_act = []
        gq_multi = []
        if gq_client:
            # 「进行中事件」另有下限：全球台网一天几十个 M1~M2 自动检测，
            # 若都算在跑，主卡和镜头会被它们轮番抢走（日本真在摇时镜头还停在美国）。
            # M3 起才算「值得占主卡」，更小的仍留在最近地震列表里。
            # 这里的下限跟「全球检测最小震级」脱钩：那个只管「最近地震」列表，
            # 不该顺手把弹窗也滤掉（列表调到 M5 时，M4 的 GQ 仍要能弹）。
            # 活跃事件一律从 M2.5 起给，前端再按「弹窗震级」二次筛选。
            gq_multi = gq_client.active_events(min_mag=2.5, limit=6)
            if gq_multi:
                eew["GQ"] = gq_multi[0]
            gq_act = gq_client.active_intensities()
        # 多事件适配：同时进行的地震全部交给前端（中日各一 + GQ 最多 6 起），
        # 前端据此画多个震中、卡片可切换
        eew_list = [eew[c] for c in ("JP", "CN", "TW") if eew.get(c) and eew[c].get("active")]
        seen_uid = set(e["uid"] for e in eew_list)
        for e in gq_multi:
            if e["uid"] not in seen_uid:
                eew_list.append(e)
        # 演练自动过期：最后一次更新 5 分钟后清掉。以前 STATE["sim"] 永不清理，
        # 残留的旧演练会让前端的 simEnd 判定失灵，第二次演练就显示不出来。
        # pending 阶段还没有报（report_ts=0），按 origin_ts 算，不然刚点下去就被清掉
        sim = STATE["sim"]
        if sim and now - (sim.get("report_ts") or sim.get("origin_ts") or 0) > 300:
            STATE["sim"] = None
            sim = None
        ob = STATE["obs"]
        if ob and ob.get("sim") and now - (ob.get("report_ts") or 0) > 600:
            STATE["obs"] = None
        return {
            "ts": now,
            "version": VERSION,
            "uptime": int(now - STATE["started"]),
            "sources": STATE["sources"],
            "eew": eew,
            "eew_list": eew_list,
            "events": merged[:40],
            "alerts": STATE["alerts"][:30],
            "obs": STATE["obs"],
            "tsunami": STATE["tsunami"],
            "lpgm": STATE["lpgm"],
            "sim": STATE["sim"],
            "sim_speed": SIM_SPEED["v"],
            "primary": STATE.get("primary") or {},
            "palert": STATE.get("palert"),
            "gq_act": gq_act,
        }


def enrich_obs_points(points):
    """把观测点地点名解析成经纬度（后台线程，一组一组来）"""
    todo = [p for p in points if p.get("lat") is None]
    if not todo or _geo_busy[0]:
        return

    def run():
        _geo_busy[0] = True
        try:
            got = POINTGEO.resolve(points)
            if got:
                publish()
        except Exception:
            pass
        finally:
            _geo_busy[0] = False

    threading.Thread(target=run, daemon=True).start()


def publish():
    payload = build_state()
    text = "data: " + json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n\n"
    HUB.broadcast(text)


def refresh_sources_meta():
    out = []
    now = time.time()
    for src in sources.SOURCES:
        rt = SRC_RUNTIME.setdefault(src["id"], {
            "ok": None, "err": None, "latency_ms": 0,
            "last_ok_ts": 0, "last_ok_str": "", "errors_total": 0,
            "last_err_ts": 0, "has_data": False,
        })
        item = {k: src[k] for k in ("id", "country", "kind", "name", "name_zh", "url", "poll")}
        item["provider"] = src.get("provider") or ("P2PQuake" if src["id"] == "jma_obs" else "Wolfx")
        item["need_token"] = bool(src.get("need_token"))
        item["has_token"] = sources.has_token(src)
        item.update(rt)
        age = now - (rt["last_ok_ts"] or now)
        if src.get("wip"):
            # 接口还没打通（TREM 的域名在本机解析不到）：显示「接入中」，
            # 既不冒充故障，也不占线程。
            item["state"] = "wip"
            item["ok"] = None
            item["age_s"] = None
            out.append(item)
            continue
        if not sources.has_token(src):
            # 需要授权又没令牌：不是「故障」，是没启用。前端单独一档显示。
            item["state"] = "noauth"
            item["ok"] = None
            item["age_s"] = None
            out.append(item)
            continue
        if rt["ok"]:
            item["state"] = "ok"
        elif rt["has_data"] and age < 90:
            item["state"] = "stale"
        elif not rt["has_data"] and age < 45:
            item["state"] = "init"
        else:
            item["state"] = "down"
        item["age_s"] = round(age, 1) if rt["last_ok_ts"] else None
        out.append(item)
    if gq_client:
        st = gq_client.status_snapshot()
        grole = st.get("role") or "主服务器"
        item = {"id": "globalquake", "country": "OT", "kind": "gq",
                "name": "GlobalQuake Server",
                "name_zh": "GlobalQuake 全球台网（%s）" % grole,
                "url": "tcp://%s:%s" % (st.get("host"), st.get("port")),
                "poll": 20, "provider": "GlobalQuake",
                "role": grole, "switch_count": st.get("switch_count") or 0,
                "last_switch_str": st.get("last_switch_str") or "",
                "last_switch_reason": st.get("last_switch_reason") or "",
                "auto": bool(st.get("auto")), "pick_str": st.get("pick_str") or "",
                "manual": bool(st.get("manual")),
                "manual_str": st.get("manual_str") or "",
                "host_idx": st.get("host_idx") or 0,
                "hosts": st.get("hosts") or []}
        for k in ("ok", "err", "latency_ms", "last_ok_ts", "last_ok_str",
                  "errors_total", "has_data"):
            item[k] = st.get(k)
        gage = now - (item["last_ok_ts"] or now)
        if item["ok"]:
            item["state"] = "ok"
        elif item["last_ok_ts"] and gage < 150:
            item["state"] = "stale"
        elif item["last_ok_ts"]:
            item["state"] = "down"
        else:
            item["state"] = "init"
        item["age_s"] = round(gage, 1) if item["last_ok_ts"] else None
        out.append(item)
    with LOCK:
        STATE["sources"] = out


PRIMARY_KEEP = 0.25          # 新源要比现主源快 25% 以上才换（防抖）
_PRIMARY_PREV = {}


def pick_primary():
    """每 10 秒挑一次「延迟最低的源」，同类之间比（eew 对 eew、obs 对 obs）。

    用的是最近几次延迟的中位数，不是单次值：单次延迟抖一下就让主源跳来跳去，
    界面上的「当前主源」会闪，也会让事件优先级反复横跳。
    """
    buckets = {}
    for it in STATE.get("sources", []):
        if it.get("state") != "ok":
            continue
        # 只比预警和观测：目录是几十秒后才更新的，波形流（wave）没有「延迟」可言
        if it.get("kind") not in ("eew", "obs"):
            continue
        hist = it.get("lat_hist") or []
        if not hist:
            continue
        lat = sorted(hist)[len(hist) // 2]          # 中位数
        # 按「国家 + 类型」分组：日本速报只能和日本速报比，不能因为中国源
        # 快 200ms 就让日本的预警改走中国源——那是两套完全不同的台网。
        buckets.setdefault((it.get("country"), it.get("kind")), []).append((lat, it["id"], it))

    out = {}
    for (country, kind), arr in buckets.items():
        arr.sort(key=lambda x: x[0])
        best = arr[0]
        # 防抖：台湾有 P-Alert 和 TREM 两个测站源，两边延迟接近（都在 1~2 秒）时
        # 谁赢谁输全靠抖动。这里要求新源比现主源快 25% 才换手，否则保持原样——
        # 前端同一时刻只画主源那一个，换手太勤画面就会来回闪。
        prev = _PRIMARY_PREV.get((country, kind))
        if prev and prev != best[1]:
            cur = next((x for x in arr if x[1] == prev), None)
            if cur is not None and best[0] * (1 + PRIMARY_KEEP) > cur[0]:
                best = cur
        _PRIMARY_PREV[(country, kind)] = best[1]
        out.setdefault(kind, {})[country or "OT"] = {
            "id": best[1],
            "latency_ms": best[0],
            "name": best[2].get("name_zh") or best[2].get("name"),
            "provider": best[2].get("provider"),
            "candidates": [
                {"id": i, "latency_ms": l, "name": n.get("name_zh") or n.get("name")}
                for l, i, n in arr
            ],
        }
    return out


def primary_loop():
    """10 秒一轮：重算主源，写进 STATE 并推给前端"""
    while True:
        with LOCK:
            STATE["primary"] = pick_primary()
        publish()
        time.sleep(10)


def heartbeat_loop():
    while True:
        time.sleep(4)
        refresh_sources_meta()
        publish()


# ---------------- NIED 强震モニタ(kmoni) 实时图代理 ----------------
# 每秒一张 jma_s（リアルタイム震度）GIF。浏览器端原生解码后逐台站取色，
# 这里只做透明代理（浏览器直连会被 CORS 拦住），不做任何解析。

_KMONI = {"ts": "", "gif": b"", "at": 0.0, "ok": False, "err": "", "hits": 0,
           "req200": 0, "req204": 0, "req_ts": 0.0}
_KMONI_LOCK = threading.Lock()
# 复用 TCP 连接：urllib 每次新建连接要 300ms~2s 的握手抖动，是「不到一秒一帧」的主因
_KMONI_CONN = [None]


def _kmoni_get(ts):
    """经 keep-alive 连接抓一帧，返回字节；失败抛异常并复位连接"""
    import http.client
    day = ts[:8]
    path = ("/data/map_img/RealTimeImg/jma_s/" + day + "/" + ts + ".jma_s.gif")
    for attempt in (0, 1):            # 第一次失败多半是连接被服务端关了，重连一次
        conn = _KMONI_CONN[0]
        try:
            if conn is None:
                conn = http.client.HTTPConnection("www.kmoni.bosai.go.jp", timeout=5)
                _KMONI_CONN[0] = conn
            conn.request("GET", path, headers={
                "Referer": "http://www.kmoni.bosai.go.jp/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            })
            r = conn.getresponse()
            data = r.read()
            if r.status != 200:
                raise ValueError("http %d" % r.status)
            return data
        except Exception:
            try:
                if conn is not None:
                    conn.close()
            except Exception:
                pass
            _KMONI_CONN[0] = None
            if attempt:
                raise
    raise OSError("unreachable")


def _kmoni_fetch_once(target):
    """抓 target 整秒帧；发布延迟抖动时向旧回退，最多 3 秒"""
    last = None
    for back in range(0, 4):
        ts = (datetime.datetime.strptime(target, "%Y%m%d%H%M%S")
              - datetime.timedelta(seconds=back)).strftime("%Y%m%d%H%M%S")
        try:
            data = _kmoni_get(ts)
            if len(data) < 1000 or not data[:6].startswith(b"GIF"):
                raise ValueError("bad gif %dB" % len(data))
            with _KMONI_LOCK:
                if ts >= _KMONI["ts"]:          # 只保留更新的
                    _KMONI["ts"] = ts
                    _KMONI["gif"] = data
                    _KMONI["at"] = time.time()
                    _KMONI["ok"] = True
                    _KMONI["err"] = ""
                    _KMONI["hits"] += 1
            return ts
        except Exception as e:
            last = e
    raise last


def kmoni_loop():
    # 对齐节拍：目标是每 1 秒拿到一张新帧。图像发布延迟实测 0.8~1.5s，
    # 取 1.4s 前的整秒（更贴近实时，抓不到就自动回退）；抓完对齐到下一秒，网络快时节奏就是精确的 1s/帧。
    err_n = 0
    while True:
        try:
            t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9, seconds=-1.4)
            target = t.replace(microsecond=0).strftime("%Y%m%d%H%M%S")
            with _KMONI_LOCK:
                fresh = _KMONI["ts"] >= target
            if not fresh:
                _kmoni_fetch_once(target)
            err_n = 0
        except Exception as e:
            err_n += 1
            with _KMONI_LOCK:
                _KMONI["err"] = str(e)[:120]
                if err_n >= 5 and _KMONI["ok"] and time.time() - _KMONI["at"] > 30:
                    _KMONI["ok"] = False   # 连续失败超过 30s 才标记断线
        # 对齐到下一个整秒（JST 时间轴），抓帧耗时大就自然顺延
        now = time.time()
        time.sleep(max(0.02, 1.0 - (now - math.floor(now)) if err_n == 0 else min(0.5 + err_n * 0.4, 4.0)))


# ---------------- HTTP ----------------

class Handler(SimpleHTTPRequestHandler):
    server_version = "EEWMonitor/" + VERSION
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200, cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        if self._https():
            self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.end_headers()
        self.wfile.write(body)

    def _https(self):
        if isinstance(self.connection, ssl.SSLSocket):
            return True
        if os.environ.get("EEW_TRUST_PROXY") in ("1", "true", "True"):
            return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"
        return False

    def _client_ip(self):
        if os.environ.get("EEW_TRUST_PROXY") in ("1", "true", "True"):
            xff = (self.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if xff:
                return xff
        return self.client_address[0]

    def _token(self):
        raw = self.headers.get("Cookie") or ""
        for part in raw.split(";"):
            k, _, v = part.strip().partition("=")
            if k == "eew_session":
                return v
        return ""

    def _user(self):
        try:
            return auth.user_by_token(self._token())
        except Exception:
            return None

    def _redirect(self, to):
        self.send_response(302)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cookie(self, token, clear=False):
        sec = "; Secure" if self._https() else ""
        if clear:
            return "eew_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0" + sec
        return "eew_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d%s" % (
            token, 30 * 86400, sec)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def end_headers(self):
        p = self.path.split("?")[0]
        if p.endswith(".html") or p == "/":
            self.send_header("Cache-Control", "no-store, must-revalidate")
        elif p.endswith((".js", ".css")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]
        # ---- 登录页与鉴权 ----
        if path in ("/login", "/login.html"):
            if self._user():
                return self._redirect("/")
            self.path = "/login.html"
            return super().do_GET()
        if path == "/api/me":
            u = self._user()
            return self._json({"ok": bool(u), "user": u}, 200 if u else 401)
        if path.startswith("/api/") and path not in ("/api/health", "/api/login", "/api/register"):
            u = self._user()
            if not u:
                return self._json({"ok": False, "error": "未登录", "login": True}, 401)
        if path in ("/", "/index.html") and not self._user():
            return self._redirect("/login")
        try:
            if path == "/api/state":
                self._json(build_state())
                return
            if path == "/api/stream":
                self._stream()
                return
            if path == "/api/kmoni":
                # kmoni 实时震度 GIF 代理（浏览器端解码取色）
                # ?ts= 条件请求：帧没更新就回 204，前端可以放心高频轮询
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                since = (qs.get("ts") or [""])[0]
                with _KMONI_LOCK:
                    gif = _KMONI["gif"]
                    ts = _KMONI["ts"]
                    ok = _KMONI["ok"]
                with _KMONI_LOCK:
                    _KMONI["req_ts"] = time.time()
                if ok and gif and since and ts and ts <= since:
                    with _KMONI_LOCK:
                        _KMONI["req204"] += 1
                    self.send_response(204)
                    self.send_header("X-Kmoni-Ts", ts)
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                if not ok or not gif:
                    self.send_response(503)
                    self.send_header("Content-Length", "0")
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    return
                with _KMONI_LOCK:
                    _KMONI["req200"] += 1
                self.send_response(200)
                self.send_header("Content-Type", "image/gif")
                self.send_header("Content-Length", str(len(gif)))
                self.send_header("X-Kmoni-Ts", ts)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(gif)
                return
            if path == "/api/kmoni/status":
                with _KMONI_LOCK:
                    self._json({"ts": _KMONI["ts"], "ok": _KMONI["ok"],
                                "err": _KMONI["err"], "hits": _KMONI["hits"],
                                "req200": _KMONI["req200"], "req204": _KMONI["req204"],
                                "req_age": round(time.time() - _KMONI["req_ts"], 1) if _KMONI["req_ts"] else None,
                                "age": round(time.time() - _KMONI["at"], 1) if _KMONI["at"] else None})
                return
            if path == "/api/place":
                # 演练选点时前端来问「这个坐标该叫什么」
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                try:
                    la = float((qs.get("lat") or ["0"])[0])
                    lo = float((qs.get("lon") or ["0"])[0])
                except (TypeError, ValueError):
                    self._json({"ok": False, "error": "参数错误"}, 400)
                    return
                cc = "JP" if (qs.get("country") or ["JP"])[0].upper() == "JP" else "CN"
                r = jp_nearest_place(la, lo) if cc == "JP" else None
                self._json({"ok": True, "place": place_of(la, lo, cc),
                            "pref": r[0] if r else "", "town": r[1] if r else "",
                            "km": round(r[2], 1) if r else None})
                return
            if path == "/api/coast":
                # 沿海市町村表（离线用 tools/gen_coast_places.py 生成）：海啸按地名判定，
                # 每个地名有自己的距离/波高/到达时间，而不是拿几个大城市代表整条海岸
                self._json(_coast_places())
                return
            if path == "/api/palert_stations":
                # 台湾 P-Alert 786 个测站的经纬度（tools/gen_palert.py 生成）。
                # 实时值走 /api/state 的 palert 字段，两边按站号对。
                self._json(_palert_stations())
                return
            if path == "/api/stations":
                u = self._user() or {}
                self._json({"stations": all_stations(u.get("name"), bool(u.get("admin"))),
                            "builtin": len(station_data.STATIONS)})
                return
            if path == "/api/gq_stations":
                if gq_client:
                    st = gq_client.stations_snapshot()
                    # 紧凑格式：[index, lat, lon, net, code, loc]。
                    # index 必须带上：GQ 的台站编号是稀疏的（全局目录 9000+，
                    # 当前在线只有一部分），前端拿数组下标当 index 会整体错位，
                    # 结果就是「有些测站不显示、亮起来的还不是那几个站」。
                    self._json({"n": len(st),
                                "s": [[p["i"], p["lat"], p["lon"], p["net"], p["code"], p["loc"]]
                                      for p in st]})
                else:
                    self._json({"n": 0, "s": []})
                return
            if path == "/api/settings":
                u = self._user() or {}
                name = u.get("name") or ""
                p = user_file(name, "settings.json") if name else os.path.join(DATA_DIR, "settings.json")
                if name and not os.path.exists(p) and u.get("admin"):
                    p = os.path.join(DATA_DIR, "settings.json")   # 旧全局设置归管理员
                try:
                    with open(p, encoding="utf-8") as f:
                        self._json({"ok": True, "settings": json.load(f)})
                except (OSError, ValueError):
                    self._json({"ok": True, "settings": None})
                return
            if path == "/api/history":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                days = max(1, min(3650, int(float((qs.get("days") or ["30"])[0]))))
                minmag = max(0.0, min(9.0, float((qs.get("minmag") or ["4.5"])[0])))
                limit = max(20, min(2000, int(float((qs.get("limit") or ["400"])[0]))))
                local = (qs.get("local") or ["0"])[0] in ("1", "true")
                self._json(history.query(days, minmag, limit, local_only=local))
                return
            if path == "/api/stats":
                qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                days = max(1, min(90, int(float((qs.get("days") or ["7"])[0]))))
                self._json(history.stats(days))
                return
            if path == "/api/sound/log":
                with _SND_CV:
                    self._json({"log": list(_SND_LOG)[-80:]})
                return
            if path == "/api/health":
                self._json({"ok": True, "version": VERSION,
                            "desktop": DESKTOP, "server_audio": DESKTOP and HAVE_WINSOUND,
                            "engine": "mci" if HAVE_MCI else ("winsound" if HAVE_WINSOUND else "none"),
                            "uptime": int(time.time() - STATE["started"])})
                return
        except (BrokenPipeError, ConnectionResetError):
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        path = self.path.split("?")[0]
        body = self._body()
        # ---- 账号 ----
        if path in ("/api/login", "/api/register"):
            ip = self._client_ip()
            now = time.time()
            fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 300]
            LOGIN_FAILS[ip] = fails
            if len(fails) >= 8:
                return self._json({"ok": False, "error": "尝试太频繁，请 5 分钟后再试"}, 429)
            user = str(body.get("username") or "").strip()
            pwd = str(body.get("password") or "")
            try:
                if path == "/api/register":
                    u = auth.register(user, pwd, body.get("invite"))
                else:
                    u = auth.verify(user, pwd)
                    if not u:
                        fails.append(now)
                        return self._json({"ok": False, "error": "用户名或密码不对"}, 401)
            except ValueError as e:
                return self._json({"ok": False, "error": str(e)}, 400)
            token = auth.new_session(u, self.headers.get("User-Agent") or "")
            return self._json({"ok": True, "user": u},
                              cookie=self._cookie(token))
        if path == "/api/logout":
            auth.revoke(self._token())
            return self._json({"ok": True}, cookie=self._cookie("", clear=True))
        if path.startswith("/api/") and path != "/api/health":
            if not self._user():
                return self._json({"ok": False, "error": "未登录", "login": True}, 401)
        if path == "/api/stations/custom":
            u0 = self._user() or {}
            if body.get("user") and body.get("user") != u0.get("name"):
                return self._json({"ok": False, "error": "账号已切换"}, 409)
            items = body.get("stations") or []
            clean = []
            for it in items[:2000]:
                try:
                    lat = float(it.get("lat")); lon = float(it.get("lon"))
                except (TypeError, ValueError):
                    continue
                cc = str(it.get("country") or "CN").upper()
                if cc not in ("CN", "JP"):
                    cc = "CN"
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    continue
                clean.append({
                    "code": str(it.get("code") or "USR%03d" % (len(clean) + 1))[:24],
                    "name": str(it.get("name") or "自定义台站")[:40],
                    "country": cc,
                    "region": str(it.get("region") or "自定义")[:40],
                    "lat": round(lat, 3),
                    "lon": round(lon, 3),
                    "net": str(it.get("net") or "自定义")[:20],
                    "major": False,
                    "custom": True,
                })
            u = self._user() or {}
            save_custom(clean, u.get("name"))
            self._json({"ok": True, "count": len(clean)})
            return
        if path == "/api/gq/switch":
            # 用户手动指定 GQ 用哪台服务器（主 / 备用 / 自定义）
            if not gq_client:
                self._json({"ok": False, "error": "GQ 客户端未启动"})
                return
            try:
                res = gq_client.manual_switch(idx=body.get("idx"),
                                              role=body.get("role"))
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)[:120]})
                return
            publish()
            self._json(res)
            return
        if path == "/api/gq/auto":
            # 「自主切换」开关：关掉后只做故障切换，不主动择优/回主
            if not gq_client:
                self._json({"ok": False, "error": "GQ 客户端未启动"})
                return
            res = gq_client.set_auto(bool(body.get("auto", True)))
            publish()
            self._json(res)
            return
        if path == "/api/sim":
            self._sim(body)
            return
        if path == "/api/sim/speed":
            # 演练倍速（空格切换）。只认 1× 与 3×，避免前端传个 0.01 把时间轴拖死。
            try:
                v = float(body.get("speed", 1))
            except (TypeError, ValueError):
                v = 1.0
            SIM_SPEED["v"] = 3.0 if v >= 2 else 1.0
            with LOCK:
                cur = STATE.get("sim")
                if cur:                       # 让前端立刻看到新倍速，不用等下一条报文
                    cur = dict(cur)
                    cur["speed"] = SIM_SPEED["v"]
                    STATE["sim"] = cur
            publish()
            self._json({"ok": True, "speed": SIM_SPEED["v"]})
            return
        if path == "/api/settings":
            st = body.get("settings")
            u0 = self._user() or {}
            if body.get("user") and body.get("user") != u0.get("name"):
                return self._json({"ok": False, "error": "账号已切换"}, 409)
            if isinstance(st, dict) and len(json.dumps(st)) < 200000:
                try:
                    u = self._user() or {}
                    path = user_file(u.get("name"), "settings.json") if u.get("name") else os.path.join(DATA_DIR, "settings.json")
                    os.makedirs(os.path.dirname(path), exist_ok=True)
                    tmp = path + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(st, f, ensure_ascii=False)
                    os.replace(tmp, path)
                    self._json({"ok": True})
                except OSError:
                    self._json({"ok": False})
            else:
                self._json({"ok": False})
            return
        if path == "/api/sim/clear":
            with LOCK:
                STATE["sim"] = None
            publish()
            self._json({"ok": True})
            return
        if path == "/api/sound":
            self._sound(body)
            return
        self._json({"ok": False, "error": "not found"}, 404)

    def _sound(self, body):
        """桌面版：用 winsound 播 wav，不受浏览器自动播放策略影响

        body: {"files": ["voice/quake.wav", ...], "prio": 0, "tag": "event", "ttl": 8}
        prio 越小越急（-1 试听 / 0 事件 / 1 台站）。每个 tag 各占一条独立音频实例：
        不同 tag 并行、互不排队；同一个 tag 的新请求顶掉旧的，不会两句人声叠着念。
        """
        if not HAVE_WINSOUND:
            self._json({"ok": False, "error": "no winsound"}, 501)
            return
        if str(body.get("tag") or "") == "stopst":
            _stop_station()
            self._json({"ok": True, "stopped": "st"})
            return
        names = body.get("files")
        if not isinstance(names, list):
            names = [body.get("file") or "builtin.wav"]
        try:
            prio = int(body.get("prio", 0))
        except (TypeError, ValueError):
            prio = 0
        try:
            ttl = float(body.get("ttl", 8))
        except (TypeError, ValueError):
            ttl = 8.0
        tag = str(body.get("tag") or "")[:12]
        paths = []
        for n in names[:6]:
            n = str(n)
            # 前端可能带 sounds/ 前缀，这里一并容忍
            n = re.sub(r"^/?sounds/", "", n)
            if not re.fullmatch(r"(\d{3}|builtin|voice/[a-z0-9-]+)\.wav", n):
                continue
            p = os.path.join(WEB_DIR, "sounds", n.replace("/", os.sep))
            if os.path.exists(p):
                paths.append(p)
        if not paths:
            self._json({"ok": False, "error": "missing"}, 404)
            return

        _snd_enqueue(paths, prio, tag, ttl)
        self._json({"ok": True, "count": len(paths), "prio": prio, "tag": tag})

    def _sim(self, body):
        country = "JP" if str(body.get("country", "CN")).upper() == "JP" else "CN"
        preset = random.choice(SIM_PRESETS[country])
        has_pos = body.get("lat") is not None and body.get("lon") is not None
        try:
            lat = float(body.get("lat") if has_pos else preset[1])
            lon = float(body.get("lon") if has_pos else preset[2])
            mag = float(body.get("mag", 6.0))
            depth = float(body.get("depth", 10))
        except (TypeError, ValueError):
            self._json({"ok": False, "error": "参数错误"}, 400)
            return
        # 地名必须跟坐标对得上：用户给了坐标就按坐标反查，只有用预设震源时才用预设名
        place = str(body.get("place") or "").strip()[:40]
        if not place:
            place = place_of(lat, lon, country) if has_pos else preset[0]
        now = time.time()
        uid = "SIM-%d" % int(now * 1000)
        # 起点只有「真值」，没有任何报——第一报要等台站真的摇起来（见 sim_sequence）。
        # 所以这里先挂一个 pending 对象：前端拿它显示「台站检测中」，
        # 不占主卡、不进警报列表、也不给镜头。
        ev = {
            "uid": uid,
            "country": country,
            "source": "sim",
            "source_name": "演练模式",
            "event_id": str(int(now)),
            "serial": 0,
            "origin_ts": now,
            "report_ts": 0,
            "sim_t": 0.0,
            "speed": SIM_SPEED["v"],
            "place": place,
            "lat": lat, "lon": lon, "depth": depth, "mag": round(mag, 1),
            "max_intensity": "—",
            "max_intensity_num": 0,
            "warn": True, "final": False, "cancel": False,
            "training": True, "is_sea": False, "areas": [],
            "pref_intensity": {},
            "title": "演练（非真实地震）",
            "pending": True,              # 还没到第一报：前端不要当成地震显示
            "raw": {"sim": True, "target_mag": mag},
        }
        with LOCK:
            old_obs = STATE.get("obs")
            if old_obs and old_obs.get("sim"):
                STATE["obs"] = None
            # 上一场演练的 EEW 槽位先撤掉，免得新演练第一报之前还挂着旧卡
            prev = STATE["eew"].get(country)
            if prev and prev.get("source") == "sim":
                STATE["eew"][country] = None
            STATE["sim"] = ev
        publish()
        threading.Thread(target=sim_sequence,
                         args=(uid, {"lat": lat, "lon": lon, "depth": depth, "mag": mag},
                               country, place), daemon=True).start()
        self._json({"ok": True, "event": ev})

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        q = HUB.add()
        try:
            snapshot = "data: " + json.dumps(build_state(), ensure_ascii=False,
                                             separators=(",", ":")) + "\n\n"
            self.wfile.write(snapshot.encode("utf-8"))
            self.wfile.flush()
            while True:
                try:
                    msg = q.get(timeout=15)
                    self.wfile.write(msg.encode("utf-8"))
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            HUB.drop(q)


def load_tokens():
    """读 data/tokens.json：{"whews": "wat_…", "eqsc": "…"}
    用户明确要求「需要申请 API 的源不用」，所以默认是空的：
    没令牌的源只在面板里显示「待授权」，不起线程、不参与延迟竞争。
    哪天想用了，把令牌填进这个文件即可，不用改代码。
    打包版：随 exe 内嵌的那份 tokens.json 会先复制到 exe 旁边再读，
    用户改旁边这份就能换令牌，不用重新打包。"""
    path = _data_file("tokens.json")
    if not os.path.exists(path):
        sources.set_tokens({})
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        sources.set_tokens(d if isinstance(d, dict) else {})
    except Exception:
        sources.set_tokens({})


def _jian_rt():
    return SRC_RUNTIME.setdefault("jian", {
        "ok": None, "err": None, "latency_ms": 0, "last_ok_ts": 0,
        "last_ok_str": "", "errors_total": 0, "last_err_ts": 0,
        "has_data": False, "lat_hist": [],
    })


def jian_alive():
    """Jian 是推送流：延迟就是「服务端发出 → 我们收到」的几十毫秒，
    比 HTTP 轮询（RTT + poll 间隔）先天快一个数量级。这里记 25ms 名义值，
    让它有资格参与「延迟最低源」的竞争，而不是因为比不了 RTT 被排除在外。"""
    rt = _jian_rt()
    hist = rt.setdefault("lat_hist", [])
    hist.append(25)
    del hist[:-5]
    rt.update({
        "ok": True, "err": None, "latency_ms": 25,
        "last_ok_ts": time.time(),
        "last_ok_str": time.strftime("%H:%M:%S"),
        "has_data": True,
    })


def jian_on_event(msg):
    if str(msg.get("type") or "") != "jma-eew":
        return
    ev = sources.norm_jian_eew(msg.get("Data") or {})
    if not ev:
        return
    with LOCK:
        ok = _apply_eew(ev)
    if ok:
        publish()


def jian_loop():
    feed = sources.JianFeed(
        sources.TOKENS.get("jian"), "/jma-eew",
        on_event=jian_on_event, on_alive=jian_alive)
    feed.run()


def boot(host="127.0.0.1", port=8765, tls_cert=None, tls_key=None):
    """起数据源线程 + HTTP(S) 服务，返回 server 对象（不阻塞）"""
    auth.init(os.path.join(DATA_DIR, "users.db"))
    os.makedirs(DATA_DIR, exist_ok=True)
    load_tokens()
    refresh_sources_meta()
    for src in sources.SOURCES:
        if not sources.has_token(src):
            continue                      # 需要授权又没令牌 → 不起线程
        if src.get("id") == "jian" or src.get("wip"):
            continue                      # Jian 走 WebSocket 长连接；wip 的还没实现
        threading.Thread(target=source_loop, args=(src,), daemon=True).start()
    if sources.TOKENS.get("jian"):
        threading.Thread(target=jian_loop, daemon=True).start()
    if gq_client:
        gq_client.start(publish)
    threading.Thread(target=heartbeat_loop, daemon=True).start()
    threading.Thread(target=primary_loop, daemon=True).start()
    threading.Thread(target=kmoni_loop, daemon=True).start()
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    if tls_cert and tls_key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(tls_cert, tls_key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        print("HTTPS 已启用：%s:%d" % (host, port))
    return srv


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--tls-cert", help="HTTPS 证书（PEM）；与 --tls-key 一起用")
    ap.add_argument("--tls-key", help="HTTPS 私钥（PEM）")
    args = ap.parse_args()

    scheme = "https" if (args.tls_cert and args.tls_key) else "http"
    local_host = "127.0.0.1" if args.host in ("0.0.0.0", "::") else args.host
    url = "%s://%s:%d/" % (scheme, local_host, args.port)
    srv = boot(args.host, args.port, args.tls_cert, args.tls_key)
    print("=" * 56)
    print("  震线 EEW · 中日地震预警监视台  v%s" % VERSION)
    print("  %s" % url)
    print("  数据源：日本气象厅 / 中国地震台网 / 中国地震预警 / GlobalQuake")
    print("  按 Ctrl+C 退出")
    print("=" * 56)
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
    except Exception:
        traceback.print_exc()


if __name__ == "__main__":
    main()
