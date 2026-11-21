# -*- coding: utf-8 -*-
"""GlobalQuake 官方服务器客户端

连接 lab596/xspanger3770 GlobalQuake 的官方服务器
（server.globalquake.net:38000），协议是 Java 原生对象序列化流
（新版客户端 1.1.x 起 API 包名从 gqserver.api 迁移到 net.globalquake.api）：

  客户端 → HandshakePacket(compatVersion=9, ServerClientConfig(true,false))
  服务器 → HandshakeSuccessfulPacket / TerminationPacket(原因)
  客户端 → HeartbeatPacket（每 10s）、EarthquakesRequestPacket（每 20s）
  服务器 → HypocenterDataPacket（震源修订推送，全球台网检测结果）

握手/心跳/请求频率与官方 GlobalQuakeClient 完全一致，compatVersion 取自
GQApi.COMPATIBILITY_VERSION（主干 2024 = 9）。
这里手写了 Java Object Serialization Stream 的最小实现：
写出我们需要的 3 种包，读入时按字段名泛解析（认识任意包类型）。
"""
import zh_place
import socket
import struct
import threading
import time
import uuid as uuidlib

HOST_DEFAULT = "server.globalquake.net"
PORT_DEFAULT = 38000
COMPAT_VERSION = 15   # 2026-11 实测 server.globalquake.net 当前值（v15 握手成功）

# 官方客户端 client.properties 里写死了两台：primaryServerIP / secondaryServerIP，
# 端口都是 38000，协议完全一样。2026-11-01 实测备用机握手 0.99s、18 秒收 2450 包
# （比主服务器还勤），所以这边也照官方做「主备自动切换」。
SERVERS = [
    ("server.globalquake.net", 38000, "主服务器"),
    ("server-backup.globalquake.net", 38000, "备用服务器"),
]
FAILS_TO_SWITCH = 2   # 连续失败几次就换机（1 次太急，网络抖一下就跳）
STALL_SEC = 75        # 连着但这么久一个包都没来 = 半死，主动断开换机

# ---- 自主调度：不只是「坏了才换」，还要「开机挑最快」和「主机好了自己回来」 ----
AUTO_PICK = True      # 启动时先探一遍，谁的握手快就用谁
PROBE_EVERY = 240     # 后台体检间隔（秒）。只做一次短握手，不影响正在收包的那条连接
PROBE_TIMEOUT = 8     # 单台体检超时
HOME_STREAK = 2       # 跑在备机上时，主机连续几次体检正常就切回去（防抖）
LIVE_HOLD_SEC = 150   # 有刚发生的检测事件时不切回，别把正在看的震中画面清掉
MANUAL_HOLD = 1800    # 用户手动切过之后，这段时间内不自动推翻他的选择（选中的机器挂了照常故障切换）


JAVA_MAGIC = b"\xac\xed\x00\x05"

TC_NULL = 0x70
TC_REF = 0x71
TC_CLASSDESC = 0x72
TC_OBJ = 0x73
TC_STR = 0x74
TC_ARR = 0x75
TC_CLASS = 0x76
TC_BLKDATA = 0x77
TC_ENDBLK = 0x78
TC_RESET = 0x79
TC_LONGSTR = 0x7C
TC_PROXY = 0x7D
TC_ENUM = 0x7E
SC_WRITE = 0x01

_PRIM_SIZE = {"B": 1, "C": 2, "D": 8, "F": 4, "I": 4, "J": 8, "S": 2, "Z": 1}


# ---------------- 写：客户端 → 服务器 ----------------

def _jutf(s):
    b = s.encode("utf-8")
    return struct.pack(">H", len(b)) + b


def _desc(name, fields, flags=0x02):
    """TC_CLASSDESC：suid=0（GQ 所有 API 类都是 0），SC_SERIALIZABLE"""
    out = bytearray([TC_CLASSDESC])
    out += _jutf(name)
    out += struct.pack(">q", 0)     # serialVersionUID
    out.append(flags)               # 注意：SUID 后直接就是 flags，没有 newHandle 字段
    out += struct.pack(">h", len(fields))
    for tc, fname, sig in fields:
        out.append(tc)
        out += _jutf(fname)
        if tc in (0x4C, 0x5B):      # 'L' '['
            out.append(TC_STR)
            out += _jutf(sig)
    out += bytes([TC_ENDBLK, TC_NULL])   # annotation 结束 + 无父类
    return bytes(out)


def pkt_handshake():
    cfg_desc = _desc("net.globalquake.api.data.system.ServerClientConfig", [
        (ord("Z"), "earthquakeData", None),
        (ord("Z"), "stationData", None),
    ])
    hs_desc = _desc("net.globalquake.api.packets.system.HandshakePacket", [
        (ord("I"), "compatVersion", None),
        (ord("L"), "clientConfig", "Lnet/globalquake/api/data/system/ServerClientConfig;"),
    ])
    out = bytearray([TC_OBJ])
    out += hs_desc
    out += struct.pack(">i", COMPAT_VERSION)
    out.append(TC_OBJ)
    out += cfg_desc
    out += bytes([1, 1])            # earthquakeData=true, stationData=true（与官方客户端一致，
                                    # 实测 earthquakeData=false 时服务器连地震数据都不推）
    return bytes(out)


def _pkt_bare(cls):
    """无字段包（心跳 / 地震列表请求）"""
    return bytes([TC_OBJ]) + _desc(cls, [])


def pkt_heartbeat():
    return _pkt_bare("net.globalquake.api.packets.system.HeartbeatPacket")


def pkt_quakes():
    return _pkt_bare("net.globalquake.api.packets.earthquake.EarthquakesRequestPacket")


def pkt_archived():
    return _pkt_bare("net.globalquake.api.packets.earthquake.ArchivedQuakesRequestPacket")


def pkt_stations():
    return _pkt_bare("net.globalquake.api.packets.station.StationsRequestPacket")


# ---------------- 读：服务器 → 客户端 ----------------

class JavaStream:
    """Java 对象序列化流的最小泛解析器。

    返回结构：对象 → {"__class__": 类名, 字段名: 值, ...}
    字符串 → str；数组 → list；枚举 → {"__class__": 类名, "__enum__": 名}
    """

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.handles = {}
        self.next_handle = 0x7E0000
        self._hdr = False

    def _need(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("连接已关闭")
            self.buf += chunk

    def _read(self, n):
        self._need(n)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def u1(self):
        return self._read(1)[0]

    def u4(self):
        return struct.unpack(">I", self._read(4))[0]

    def _assign(self, obj):
        h = self.next_handle
        self.next_handle += 1
        self.handles[h] = obj
        return obj

    def read_object(self):
        if not self._hdr:
            self._hdr = True
            magic = self._read(4)
            if magic != JAVA_MAGIC:
                raise ValueError("不是 Java 序列化流: %s" % magic.hex())
        return self._obj(self.u1())

    def _obj(self, t):
        if t == TC_NULL:
            return None
        if t == TC_REF:
            return self.handles.get(self.u4())
        if t == TC_OBJ:
            cls = self._classdesc()
            obj = {"__class__": (cls or {}).get("name", "?")}
            self._assign(obj)
            self._data(obj, cls)
            return obj
        if t == TC_STR:
            n = struct.unpack(">H", self._read(2))[0]
            return self._assign(self._read(n).decode("utf-8", "replace"))
        if t == TC_LONGSTR:
            n = struct.unpack(">Q", self._read(8))[0]
            return self._assign(self._read(n).decode("utf-8", "replace"))
        if t == TC_ARR:
            cls = self._classdesc()
            arr = []
            self._assign(arr)
            n = self.u4()
            comp = (cls or {}).get("name", "")
            tc = comp[-1] if comp and comp.startswith("[") else ""
            if tc in _PRIM_SIZE:
                sz = _PRIM_SIZE[tc]
                fmt = {"B": "b", "C": "H", "D": "d", "F": "f",
                       "I": "i", "J": "q", "S": "h", "Z": "b"}[tc]
                for _ in range(n):
                    arr.append(struct.unpack(">" + fmt, self._read(sz))[0])
            else:
                for _ in range(n):
                    arr.append(self._obj(self.u1()))
            return arr
        if t == TC_CLASS:
            return {"__class__": (self._classdesc() or {}).get("name", "?")}
        if t == TC_ENUM:
            cls = self._classdesc()
            enum = {"__class__": (cls or {}).get("name", "?"), "__enum__": None}
            self._assign(enum)
            enum["__enum__"] = self._obj(self.u1())
            return enum
        if t == TC_RESET:
            self.handles = {}
            self.next_handle = 0x7E0000
            return None
        raise ValueError("未知的流标记 0x%02x" % t)

    def _classdesc(self):
        t = self.u1()
        if t == TC_NULL:
            return None
        if t == TC_REF:
            return self.handles.get(self.u4())
        if t == TC_PROXY:
            d = {"name": "$proxy", "fields": [], "flags": 0}
            self._assign(d)
            for _ in range(self.u4()):
                n = struct.unpack(">H", self._read(2))[0]
                self._read(n)
            self.u1()                     # TC_ENDBLK
            d["super"] = self._classdesc()
            return d
        if t != TC_CLASSDESC:
            raise ValueError("期望类描述符，得到 0x%02x" % t)
        d = {"fields": [], "flags": 0}
        self._assign(d)                   # 类描述符自己的句柄
        n = struct.unpack(">H", self._read(2))[0]
        d["name"] = self._read(n).decode("utf-8", "replace")
        self._read(8)                     # serialVersionUID（GQ 全是 0，不校验）
        d["flags"] = self.u1()
        for _ in range(struct.unpack(">H", self._read(2))[0]):
            tc = self.u1()
            fn = struct.unpack(">H", self._read(2))[0]
            fname = self._read(fn).decode("utf-8", "replace")
            sig = None
            if tc in (0x4C, 0x5B):
                sig = self._obj(self.u1())
            d["fields"].append({"tc": tc, "name": fname})
        self.u1()                         # TC_ENDBLK
        d["super"] = self._classdesc()
        return d

    def _data(self, obj, cls):
        if not cls:
            return
        for f in cls["fields"]:
            tc = f["tc"]
            nm = f["name"]
            ch = chr(tc)
            if ch in _PRIM_SIZE:
                fmt = {"B": "b", "C": "H", "D": "d", "F": "f",
                       "I": "i", "J": "q", "S": "h", "Z": "B"}[ch]
                obj[nm] = struct.unpack(">" + fmt, self._read(_PRIM_SIZE[ch]))[0]
            else:
                obj[nm] = self._obj(self.u1())
        if cls["flags"] & SC_WRITE:
            # 自定义 writeObject 的注解区：块数据 + 嵌套对象，直到 TC_ENDBLK。
            # ArrayList 就是这种：元素全在注解区，必须收起来（__ann__），否则列表永远是空的
            ann = obj.setdefault("__ann__", [])
            while True:
                t = self.u1()
                if t == TC_ENDBLK:
                    break
                if t == TC_BLKDATA:
                    self._read(self.u1())
                elif t == 0x4B:           # TC_LONGBLOCKDATA
                    self._read(struct.unpack(">Q", self._read(8))[0])
                else:
                    x = self._obj(t)
                    if x is not None:
                        ann.append(x)
        self._data(obj, cls.get("super"))


# ---------------- 事件归一化 ----------------

def _country(lat, lon):
    if 23.0 <= lat <= 46.5 and 122.0 <= lon <= 148.0:
        return "JP"
    if 17.0 <= lat <= 55.0 and 73.0 <= lon <= 136.0:
        return "CN"
    return "OT"


def _uuid_str(d):
    ms = int(d["uuid"]["mostSigBits"]) & 0xFFFFFFFFFFFFFFFF
    ls = int(d["uuid"]["leastSigBits"]) & 0xFFFFFFFFFFFFFFFF
    return str(uuidlib.UUID(int=(ms << 64) | ls))


GQ_QUALITY = "SABCD"          # qualityID 0..4 → S/A/B/C/D


def _quality_of(d):
    q = d.get("qualityID")
    if q is None:
        return None
    try:
        q = int(q)
    except (TypeError, ValueError):
        return None
    return GQ_QUALITY[q] if 0 <= q < len(GQ_QUALITY) else None


def _pga_of(d):
    v = d.get("maxPGA")
    try:
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def _inten_of_pga(pga):
    """峰值 PGA(gal) → JMA 震度标签 + 数值。

    GQ 只给震源和台站振幅，没有官方震度；用经验式把 PGA 换算成「計測震度」
    （I = 2.0·log10(PGA) + 0.7，100 gal≈計測震度4.7≈震度5弱），再按气象厅
    的档位切成震度标签。卡上会标「估算」，不冒充官方数。
    """
    try:
        p = float(pga)
    except (TypeError, ValueError):
        return None, 0
    if p <= 0:
        return None, 0
    import math
    i = 2.0 * math.log10(max(p, 0.1)) + 0.7
    for th, lab, val in ((6.5, "7", 6.5), (6.0, "6強", 6.0), (5.5, "6弱", 5.5),
                         (5.0, "5強", 5.0), (4.5, "5弱", 4.5), (3.5, "4", 4.0),
                         (2.5, "3", 3.0), (1.5, "2", 2.0), (0.5, "1", 1.0)):
        if i >= th:
            return lab, val
    return "0", 0


def _base_event(u, lat, lon, depth, mag, origin, region, serial=1):
    now = time.time()
    return {
        "uid": "GQ-" + u,
        "country": _country(lat, lon),
        "source": "gq",
        "source_name": "GlobalQuake 全球台网",
        "event_id": u,
        "serial": serial,
        "origin_ts": origin,
        "report_ts": now,
        # GQ 的 region 是 GeoNames 的英文地名，这里翻成中文再给前端；
        # 原始英文留在 place_en 里，翻不动的部分会原样保留（宁可不翻，也别翻错）
        "place": zh_place.zh_place(region) if region else "全球台网检测",
        "place_en": str(region or ""),
        "lat": lat, "lon": lon, "depth": depth, "mag": round(mag, 1),
        "max_intensity": "?",
        "max_intensity_num": 0,
        "warn": False, "final": False, "cancel": False, "training": False,
        "is_sea": False, "areas": [],
        "kind": "gq",
    }


def _hypo_event(d):
    """HypocenterData → 本程序统一事件模型（实时检测修订）"""
    try:
        u = _uuid_str(d)
        lat = float(d["lat"]); lon = float(d["lon"])
        mag = float(d["magnitude"])
        depth = max(0.0, float(d["depth"]))
        origin = int(d["origin"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or origin <= 0:
        return None
    ev = _base_event(u, lat, lon, depth, mag, origin,
                     d.get("region"), int(d.get("revisionID") or 1))
    ev["report_ts"] = (int(d.get("lastUpdate") or 0) / 1000.0) or time.time()
    ev["gq_live"] = True          # 实时检测包（区别于批量下发的归档数据）
    q = _quality_of(d)
    if q:
        ev["gq_quality"] = q
    pga = _pga_of(d)
    if pga is not None:
        ev["gq_maxpga"] = pga
        lab, val = _inten_of_pga(pga)
        if lab:
            ev["max_intensity"] = lab
            ev["max_intensity_num"] = val
            ev["intensity_est"] = True   # 前端据此标「估算」
    return ev


def _arch_event(d):
    """ArchivedQuakeData → 事件模型（已归档的检测结果，连接时批量下发）"""
    try:
        u = _uuid_str(d)
        lat = float(d["lat"]); lon = float(d["lon"])
        mag = float(d["magnitude"])
        depth = max(0.0, float(d["depth"]))
        origin = int(d["origin"]) / 1000.0
    except (KeyError, TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180) or origin <= 0:
        return None
    ev = _base_event(u, lat, lon, depth, mag, origin, d.get("region"))
    ev["report_ts"] = (int(d.get("finalUpdateMillis") or 0) / 1000.0) or ev["report_ts"]
    ev["final"] = True
    q = _quality_of(d)
    if q:
        ev["gq_quality"] = q
    pga = _pga_of(d)
    if pga is not None:
        ev["gq_maxpga"] = pga
        lab, val = _inten_of_pga(pga)
        if lab:
            ev["max_intensity"] = lab
            ev["max_intensity_num"] = val
            ev["intensity_est"] = True
    return ev


# ---------------- 客户端线程 ----------------

def _list_items(obj):
    """取出包/ArrayList 字典里的元素列表（元素在 __ann__ 注解区）"""
    if not isinstance(obj, dict):
        return None
    for v in obj.values():
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            ann = v.get("__ann__")
            if isinstance(ann, list) and ann:
                return ann
    ann = obj.get("__ann__")
    return ann if isinstance(ann, list) and ann else None


class GQClient:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = {}              # uuid → 事件
        self.stations = []            # 测站列表 [{i,lat,lon,net,code,loc,mi}]
        self._st_chunks = {}          # index → 测站（StationsInfoPacket 分块累积）
        self._st_key = None           # stationsIndexing UUID（服务器重启会变，变了要重置）
        self.inten = {}               # index → (maxRatio, ts)  实时观测振幅比
        self.status = {
            "ok": False, "err": None, "latency_ms": 0,
            "last_ok_ts": 0, "last_ok_str": "", "errors_total": 0,
            "last_err_ts": 0, "has_data": False,
            "host": HOST_DEFAULT, "port": PORT_DEFAULT,
            "role": SERVERS[0][2], "host_idx": 0,
            "switch_count": 0, "last_switch_str": "", "last_switch_reason": "",
            "auto": AUTO_PICK, "pick_str": "", "home_streak": 0,
            "manual": False, "manual_until": 0, "manual_str": "",
            "packets": 0, "quakes": 0, "stations": 0,
        }
        # 每台机自己的体检表：失败几次、最后一次成功/为的什么错、是不是刚被切走
        self.servers = list(SERVERS)
        self.hosts = [{
            "host": h, "port": p, "name": n,
            "ok": None, "fails": 0, "connects": 0,
            "last_ok_ts": 0, "last_ok_str": "", "last_err": "",
            "last_err_ts": 0,
            # 体检（后台短握手）结果：延迟、是否通、什么时候测的
            "probe_ms": 0, "probe_ok": None, "probe_ts": 0, "probe_str": "",
            "probe_err": "",
        } for h, p, n in self.servers]
        self._publish = None
        self._wlock = threading.Lock()

    # -- 对外 --
    def start(self, publish_cb, host=None, port=None):
        # 指定了别的机（调试/自建服务器）就插到最前面，官方主备表照样留着兜底
        if host and self.servers[0][0] != host:
            p = int(port or PORT_DEFAULT)
            self.servers.insert(0, (host, p, "自定义"))
            self.hosts.insert(0, {"host": host, "port": p, "name": "自定义",
                                  "ok": None, "fails": 0, "connects": 0,
                                  "last_ok_ts": 0, "last_ok_str": "",
                                  "last_err": "", "last_err_ts": 0,
                                  "probe_ms": 0, "probe_ok": None,
                                  "probe_ts": 0, "probe_str": "", "probe_err": ""})
            self.status.update(host=host, port=p, role="自定义", host_idx=0)
        self._publish = publish_cb
        # 开机先探一遍：两台都通的话，谁握手快就从谁开始（省掉「先连慢的再切」那一步）
        if self.status.get("auto") and len(self.servers) > 1:
            threading.Thread(target=self._boot_pick, name="gq-pick",
                             daemon=True).start()
        threading.Thread(target=self._loop, name="gq-client", daemon=True).start()
        threading.Thread(target=self._health_loop, name="gq-health",
                         daemon=True).start()

    def status_snapshot(self):
        with self.lock:
            st = dict(self.status)
            st["hosts"] = [dict(h) for h in self.hosts]
            return st

    def recent_events(self, min_mag=3.0, limit=40):
        with self.lock:
            evs = [e for e in self.events.values() if (e.get("mag") or 0) >= min_mag]
        evs.sort(key=lambda e: e.get("origin_ts") or 0, reverse=True)
        return evs[:limit]

    def active_events(self, max_origin_age=480.0, max_report_age=180.0,
                      min_mag=3.0, limit=6):
        """同时进行的多起实时检测（多地同时发震时全部返回，按震级/新旧排序）"""
        now = time.time()
        out = []
        with self.lock:
            for e in self.events.values():
                if not e.get("gq_live"):
                    continue
                if (e.get("mag") or 0) < min_mag:
                    continue
                o_age = now - (e.get("origin_ts") or 0)
                r_age = now - (e.get("report_ts") or 0)
                if o_age < -30 or o_age > max_origin_age or r_age > max_report_age:
                    continue
                out.append(e)
        # 排序：「强且新」优先——每过 2 分钟相当于让 1 个震级
        def prio(e):
            o_age = max(0.0, now - (e.get("origin_ts") or now))
            return (e.get("mag") or 0) - o_age / 120.0
        out.sort(key=prio, reverse=True)
        res = []
        for e in out[:limit]:
            d = dict(e)
            d["active"] = True
            d["age"] = round(now - (e.get("origin_ts") or now), 1)
            res.append(d)
        return res

    def active_event(self, max_origin_age=480.0, max_report_age=180.0, min_mag=3.0):
        """正在进行的实时检测：GQ 服务器刚推过修订、发震不久的全球地震。
        满足条件时作为「进行中事件」交给前端驱动波前动画/横幅
        （沿用官方客户端语义：GQ 检测可能误报，不保证与正式速报一致）。
        """
        lst = self.active_events(max_origin_age, max_report_age, min_mag, limit=1)
        return lst[0] if lst else None

    def stations_snapshot(self):
        with self.lock:
            return list(self.stations)

    def stations_count(self):
        with self.lock:
            return len(self.stations)

    def active_intensities(self, max_age=90.0, min_val=1.05, cap=6000):
        """最近有观测的测站 [[index, 振幅比], ...]（给前端按 GQ 官方公式着色）

        min_val 别再卡 3.0：噪声底之上 1.05 倍就算「有动静」，交给前端按
        0.9+6.2*pct 的宽曲线判档，微动也亮一个蓝点，不然日本那些中小振幅台站
        永远进不了列表，看上去像整片没观测。cap 提到 6000：全球在线台站上万，
        卡 3000 会把「数值小但确实在报」的那一半直接砍掉。
        """
        now = time.time()
        out = []
        with self.lock:
            for i, (v, ts) in self.inten.items():
                if now - ts > max_age or v < min_val:
                    continue
                out.append([i, round(v, 2)])
        out.sort(key=lambda p: -p[1])
        return out[:cap]

    # -- 内部 --
    def _set(self, **kw):
        with self.lock:
            self.status.update(kw)
            if kw.get("ok"):
                self.status["last_ok_ts"] = time.time()
                self.status["last_ok_str"] = time.strftime("%H:%M:%S")

    def _pub(self):
        if self._publish:
            try:
                self._publish()
            except Exception:
                pass

    # -- 体检 / 自主选路 --
    def _probe(self, idx, timeout=PROBE_TIMEOUT):
        """短握手体检：连上去、等 HandshakeSuccessful，然后立刻断开。
        只用来看「这台现在还活不活、握手多少毫秒」，不碰收包和状态。"""
        idx %= len(self.servers)
        host, port, _name = self.servers[idx]
        t0 = time.time()
        sock = None
        ms = None
        err = ""
        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.sendall(JAVA_MAGIC + pkt_handshake())
            js = JavaStream(sock)
            deadline = time.time() + timeout
            while True:
                sock.settimeout(max(0.5, deadline - time.time()))
                obj = js.read_object()
                cls = (obj or {}).get("__class__", "")
                if cls.endswith("HandshakeSuccessfulPacket"):
                    ms = int((time.time() - t0) * 1000)
                    break
                if cls.endswith("TerminationPacket"):
                    err = "拒绝: %s" % (obj.get("cause") or "未知")
                    break
                if not cls:
                    err = "响应异常"
                    break
        except Exception as exc:
            err = "%s: %s" % (type(exc).__name__, exc)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        with self.lock:
            h = self.hosts[idx]
            h["probe_ms"] = ms or 0
            h["probe_ok"] = ms is not None
            h["probe_ts"] = time.time()
            h["probe_str"] = time.strftime("%H:%M:%S")
            h["probe_err"] = err[:120]
        return ms

    def _boot_pick(self):
        """启动择优：两台并行体检，谁快从谁开始。都探不通就按原顺序（主→备）。
        给 _loop 一点时间再落定，避免两边同时改 host_idx 打架。"""
        res = {}
        def one(i):
            res[i] = self._probe(i, timeout=6)
        ths = [threading.Thread(target=one, args=(i,), daemon=True)
               for i in range(len(self.servers))]
        for t in ths:
            t.start()
        for t in ths:
            t.join(7.5)
        ok = [(ms, i) for i, ms in res.items() if ms]
        if not ok:
            return
        ok.sort()
        best_ms, best = ok[0]
        if best == 0:
            self._set(pick_str="启动择优：%s 最快 %dms" %
                      (self.servers[0][2], best_ms))
            return
        # 已经连上了就别硬拉回去，等 health_loop 慢慢切
        if self.status.get("ok"):
            return
        self._switch(best, "启动择优：%s 握手 %dms 最快" %
                     (self.servers[best][2], best_ms))
        self._set(pick_str="按体检结果从%s开始（%dms）" %
                  (self.servers[best][2], best_ms))

    def _live_busy(self):
        """有正在进行的检测（近 LIVE_HOLD_SEC 秒报过）时不切机：
        切机会清空事件快照，用户正看着的震中画面会突然消失。"""
        now = time.time()
        with self.lock:
            for e in self.events.values():
                if e.get("gq_live") and now - (e.get("report_ts") or 0) < LIVE_HOLD_SEC:
                    return True
        return False

    def _health_loop(self):
        """后台体检 + 自动回主：
        跑在备机上时，每 PROBE_EVERY 秒给主机做一次短握手，
        连续 HOME_STREAK 次正常、且当前没有正在播的检测事件 → 切回主机。
        用户手动切过的机器在 MANUAL_HOLD 内不吃这套（尊重用户的选择）。"""
        time.sleep(25)
        while True:
            try:
                cur = self.status["host_idx"] % len(self.servers)
                for i in range(len(self.servers)):
                    if i == cur:
                        continue
                    self._probe(i)
                held = time.time() < (self.status.get("manual_until") or 0)
                if (self.status.get("auto") and not held and cur != 0 and
                        len(self.servers) > 1):
                    h = self.hosts[0]
                    if h.get("probe_ok"):
                        streak = (self.status.get("home_streak") or 0) + 1
                        self._set(home_streak=streak)
                        if streak >= HOME_STREAK and not self._live_busy():
                            self._switch(0, "主服务器已恢复（握手 %dms），自动切回" %
                                         (h.get("probe_ms") or 0))
                            self._set(home_streak=0, manual=False, manual_until=0)
                    else:
                        self._set(home_streak=0)
                else:
                    if self.status.get("home_streak"):
                        self._set(home_streak=0)
            except Exception:
                pass
            time.sleep(PROBE_EVERY)

    def _loop(self):
        """主备自动切换：连续 FAILS_TO_SWITCH 次失败就换下一台，
        换过去后至少用得稳就不回来（避免两台互相打架、来回抖）。"""
        backoff = 4
        fails = 0
        while True:
            idx = self.status["host_idx"] % len(self.servers)
            host, port, role = self.servers[idx]
            self._set(host=host, port=port, role=role, host_idx=idx)
            try:
                self._session(host, port)
                # 会话正常返回（极少数情况）→ 视为一次成功，接着连同一台
                fails = 0
                backoff = 4
            except Exception as exc:
                fails += 1
                msg = "%s: %s" % (type(exc).__name__, exc)
                self._host_fail(idx, msg)
                self._set(ok=False, err=msg,
                          errors_total=self.status["errors_total"] + 1,
                          last_err_ts=time.time())
                self._pub()
                if fails >= FAILS_TO_SWITCH and len(self.servers) > 1:
                    nxt = (idx + 1) % len(self.servers)
                    self._switch(nxt, msg)
                    fails = 0
                    backoff = 3        # 刚换机就别睡太久，尽快接上
            time.sleep(backoff)
            backoff = min(backoff * 1.6, 60)

    def _host_ok(self, idx):
        with self.lock:
            h = self.hosts[idx]
            h["ok"] = True
            h["fails"] = 0
            h["connects"] += 1
            h["last_ok_ts"] = time.time()
            h["last_ok_str"] = time.strftime("%H:%M:%S")

    def _host_fail(self, idx, msg):
        with self.lock:
            h = self.hosts[idx]
            h["ok"] = False
            h["fails"] += 1
            h["last_err"] = msg
            h["last_err_ts"] = time.time()

    def _switch(self, nxt, reason):
        nxt %= len(self.servers)
        host, port, role = self.servers[nxt]
        with self.lock:
            self.status.update(host_idx=nxt, host=host, port=port, role=role,
                               switch_count=self.status["switch_count"] + 1,
                               last_switch_str=time.strftime("%H:%M:%S"),
                               last_switch_reason=reason[:120], err=None)
            # 换机 = 另一套服务器状态（stationsIndexing 不同、事件 uuid 可能重排），
            # 本地快照先清空，新机一连上会重新下发，混着用只会串台。
            self._st_key = None
            self._st_chunks.clear()
            self.stations = []
            self.inten.clear()
            self.events.clear()
            self.status["stations"] = 0
            self.status["has_data"] = False
        self._pub()

    def _send(self, sock, data):
        with self._wlock:
            sock.sendall(data)

    # -- 用户手动切 --
    def servers_view(self):
        """给界面用的机位表：谁在用、各自体检结果。"""
        with self.lock:
            cur = self.status["host_idx"] % len(self.servers)
            out = []
            for i, (h, p, n) in enumerate(self.servers):
                d = dict(self.hosts[i])
                d.update(role=n, host=h, port=p, current=(i == cur))
                out.append(d)
            return out

    def manual_switch(self, idx=None, role=None):
        """用户自己指定用哪台。role 可以传「主服务器 / 备用服务器 / 自定义」。
        手动切之后一段时间内暂停「自动回主」，免得系统马上把用户的决定推翻
        （但选中的那台真挂了，故障切换照常生效）。"""
        with self.lock:
            n = len(self.servers)
            if idx is None and role:
                for i, (_h, _p, nm) in enumerate(self.servers):
                    if nm == role:
                        idx = i
                        break
            if idx is None:
                raise ValueError("没指定要切到哪一台")
            idx = int(idx) % n
            if idx == self.status["host_idx"] % n:
                return {"ok": True, "already": True, "role": self.servers[idx][2]}
            name = self.servers[idx][2]
        self._switch(idx, "用户手动切换到%s" % name)
        self._set(manual=True, manual_until=time.time() + MANUAL_HOLD,
                  manual_str=time.strftime("%H:%M:%S"), home_streak=0)
        self._pub()
        return {"ok": True, "role": name, "idx": idx,
                "hold_min": int(MANUAL_HOLD // 60)}

    def set_auto(self, on):
        """「自主切换」总开关。关掉后只做故障切换，不主动选最快的、也不自动回主。"""
        self._set(auto=bool(on), home_streak=0)
        self._pub()
        return {"ok": True, "auto": bool(on)}

    def _session(self, host=None, port=None):
        host = host or self.status["host"]
        port = int(port or self.status["port"])
        sock = socket.create_connection((host, port), timeout=12)
        try:
            self._set(ok=False, err=None)
            # 客户端先写流头 + 握手包（与官方 ClientSocket 一致）
            sock.sendall(JAVA_MAGIC + pkt_handshake())
            js = JavaStream(sock)
            # 等 HandshakeSuccessfulPacket
            deadline = time.time() + 20
            handshaked = False
            while not handshaked:
                sock.settimeout(max(1.0, deadline - time.time()))
                obj = js.read_object()
                cls = (obj or {}).get("__class__", "")
                if cls.endswith("HandshakeSuccessfulPacket"):
                    handshaked = True
                elif cls.endswith("TerminationPacket"):
                    raise RuntimeError("服务器拒绝: %s" % (obj.get("cause") or "未知"))
                elif not cls:
                    raise RuntimeError("握手响应异常")
            idx = self.status["host_idx"] % len(self.hosts)
            self._host_ok(idx)
            self._set(ok=True, err=None)
            self._pub()
            self._reader(js, sock)
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def _reader(self, js, sock):
        """收包线程：官方客户端的心跳/请求节奏
        （抓包实测官方客户端顺序：握手 → EarthquakesRequest →
          ArchivedQuakesRequest → 心跳10s → 请求20s）"""
        def writer():
            # 单次 send 失败不能把整个写线程带走：以前一个异常就让这个会话
            # 再也不发请求，服务器还在推测站包（看着「已连接」），但归档事件
            # 一次都拉不到——表现就是「GQ 收不到地震」。
            def safe(pkt):
                try:
                    self._send(sock, pkt)
                except Exception:
                    pass
            safe(pkt_quakes())
            safe(pkt_stations())
            safe(pkt_archived())
            next_hb = time.time() + 10
            next_q = time.time() + 20
            next_arch = time.time() + 45
            while True:
                time.sleep(1.0)
                now = time.time()
                if now >= next_hb:
                    safe(pkt_heartbeat()); next_hb += 10
                if now >= next_q:
                    safe(pkt_quakes()); next_q += 20
                if now >= next_arch:
                    # 归档只在连上时要过一次。要是那一次丢了（切机、握手刚完就
                    # 被掐、服务器重启），事件列表会一直空着，这里兜底补要。
                    if not self.events:
                        safe(pkt_archived()); safe(pkt_stations())
                    next_arch = now + 30

        wt = threading.Thread(target=writer, name="gq-writer", daemon=True)
        wt.start()
        # 服务器平时几秒就有一包（测站强度/归档/心跳回执），静默 STALL_SEC 说明
        # 连接还在但服务器已经"半死"——socket 不会报错，只能靠读超时自己判，
        # 抛出去让 _loop 换到备用机。
        sock.settimeout(STALL_SEC)
        while True:
            try:
                obj = js.read_object()
            except socket.timeout:
                raise ConnectionError("服务器 %d 秒无数据" % STALL_SEC)
            self._on_packet(obj)

    def _on_packet(self, obj):
        if not isinstance(obj, dict):
            return
        cls = obj.get("__class__", "")
        if cls.endswith("TerminationPacket"):
            raise RuntimeError("服务器终止连接: %s" % (obj.get("cause") or ""))
        if cls.endswith("HypocenterDataPacket"):
            ev = _hypo_event(obj.get("data") or {})
            self._set(ok=True, has_data=True,
                      packets=self.status["packets"] + 1)
            # 入库下限压到 M1.0：用户可把展示/预警阈值调到 M1/M1.5，
            # 这里只负责「都收下来」，筛选取决于 settings 里的 gqMag
            if ev and (ev["mag"] or 0) >= 1.0:
                self._keep(ev)
                self._pub()
        elif cls.endswith("ArchivedQuakePacket"):
            ev = _arch_event(obj.get("archivedQuakeData") or {})
            self._set(ok=True, has_data=True,
                      packets=self.status["packets"] + 1)
            if ev and (ev["mag"] or 0) >= 1.0:
                self._keep(ev)
        elif cls.endswith("StationsInfoPacket") or cls.endswith("StationsIntensityPacket"):
            # 两种包都带 stationsIndexing（UUID）。变了 = 服务器重启过，本地缓存全部作废
            uid = obj.get("stationsIndexing")
            key = None
            if isinstance(uid, dict):
                try:
                    key = ((int(uid.get("mostSigBits") or 0) << 64)
                           | (int(uid.get("leastSigBits") or 0) & 0xFFFFFFFFFFFFFFFF))
                except (TypeError, ValueError):
                    key = None
            if key is not None and key != self._st_key:
                with self.lock:
                    self._st_key = key
                    self._st_chunks.clear()
                    self.stations = []
                    self.inten.clear()
            if cls.endswith("StationsInfoPacket"):
                # 分块下发：每包 ≤64 站。maxIntensity 实为 maxRatio（相对噪声底的振幅比）
                lst = _list_items(obj)
                got = []
                if lst:
                    for d in lst:
                        if not isinstance(d, dict):
                            continue
                        try:
                            got.append({
                                "i": int(d.get("index") if d.get("index") is not None else 0),
                                "lat": float(d["lat"]), "lon": float(d["lon"]),
                                "net": str(d.get("network") or ""),
                                "code": str(d.get("station") or ""),
                                "loc": str(d.get("location") or ""),
                                "mi": float(d.get("maxIntensity") or 0),
                            })
                        except (KeyError, TypeError, ValueError):
                            continue
                if got:
                    with self.lock:
                        for s in got:
                            self._st_chunks[s["i"]] = s
                        self.stations = [self._st_chunks[k]
                                         for k in sorted(self._st_chunks)]
                        self.status["stations"] = len(self.stations)
            else:
                lst = _list_items(obj)
                if lst:
                    now = time.time()
                    with self.lock:
                        for d in lst:
                            if not isinstance(d, dict):
                                continue
                            try:
                                self.inten[int(d["index"])] = (float(d["maxIntensity"]), now)
                            except (KeyError, TypeError, ValueError):
                                continue
            self._set(ok=True, has_data=True,
                      packets=self.status["packets"] + 1)
        else:
            self._set(ok=True, has_data=True,
                      packets=self.status["packets"] + 1)

    def _keep(self, ev):
        with self.lock:
            key = ev["event_id"]
            cur = self.events.get(key)
            if cur is not None:
                # 实时修正报往往没有 qualityID，别把归档里已有的质量冲掉
                for k in ("gq_quality", "gq_maxpga"):
                    if not ev.get(k) and cur.get(k) is not None:
                        ev[k] = cur[k]
            if cur is None or ev["serial"] >= cur.get("serial", 0):
                self.events[key] = ev
            # 只留最近 24 小时、最多 800 条（调到 M1 后全球小震一天几百条，
            # 留足容量才有「历史感」；超出按发震时间淘汰最旧的）
            horizon = time.time() - 24 * 3600
            stale = [k for k, e in self.events.items()
                     if (e.get("origin_ts") or 0) < horizon]
            for k in stale:
                del self.events[k]
            if len(self.events) > 800:
                for k in sorted(self.events,
                                key=lambda k: self.events[k]["origin_ts"])[:-800]:
                    del self.events[k]
            self.status["quakes"] = len(self.events)


CLIENT = GQClient()


def start(publish_cb=None, host=None, port=None):
    CLIENT.start(publish_cb, host, port)


def status_snapshot():
    return CLIENT.status_snapshot()


def servers_view():
    return CLIENT.servers_view()


def manual_switch(idx=None, role=None):
    return CLIENT.manual_switch(idx=idx, role=role)


def set_auto(on):
    return CLIENT.set_auto(on)


def recent_events(min_mag=3.0, limit=40):
    return CLIENT.recent_events(min_mag, limit)


def active_event(min_mag=3.0):
    return CLIENT.active_event(min_mag=min_mag)


def active_events(min_mag=3.0, limit=6):
    return CLIENT.active_events(min_mag=min_mag, limit=limit)


def stations_snapshot():
    return CLIENT.stations_snapshot()


def stations_count():
    return CLIENT.stations_count()


def active_intensities():
    return CLIENT.active_intensities()


if __name__ == "__main__":
    def show():
        pass
    start(None)
    t0 = time.time()
    while time.time() - t0 < 90:
        time.sleep(5)
        st = CLIENT.status_snapshot()
        evs = CLIENT.recent_events(min_mag=3.0, limit=12)
        print("[%3ds] ok=%s err=%s pkts=%s quakes=%s"
              % (time.time() - t0, st["ok"], st["err"], st["packets"], st["quakes"]))
        for e in evs:
            print("   M%.1f %s (%.1f,%.1f) d=%.0fkm %s"
                  % (e["mag"], e["country"], e["lat"], e["lon"], e["depth"], e["place"]))
