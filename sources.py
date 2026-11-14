"""中日地震预警台 —— 数据源拉取与归一化

统一事件模型：
{
  uid, country, source, source_name, event_id, serial,
  origin_ts, report_ts, place, lat, lon, depth, mag,
  max_intensity, max_intensity_label, warn, final, cancel, training, is_sea, areas
}
"""
import json
import os
import re
import time
import ssl
import base64
import socket
import struct
import http.client
import threading
import urllib.parse
import urllib.request
import urllib.error
import datetime


UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) EEW-Monitor/1.0"

SOURCES = [
    {
        "id": "jma_eew", "country": "JP", "kind": "eew", "poll": 1.2,
        "name": "気象庁 緊急地震速報",
        "name_zh": "日本气象厅 紧急地震速报",
        "url": "https://api.wolfx.jp/jma_eew.json",
        "provider": "Wolfx",
    },
    {
        "id": "cn_eew", "country": "CN", "kind": "eew", "poll": 1.5,
        "name": "中国地震台网 地震速報",
        "name_zh": "中国地震台网 地震速报（CENC）",
        "url": "https://api.wolfx.jp/cenc_eew.json",
        "provider": "Wolfx",
    },
    # ---- 省局速报：目前只有四川/福建/重庆三家有公开实时接口 ----
    # 省局覆盖范围往往超出本省（四川局会报云南、重庆局会报川北），
    # 所以按「发布机构」标注来源，而不是按地震所在省。
    {
        "id": "sc_eew", "country": "CN", "kind": "eew", "poll": 2,
        "name": "四川省地震局 地震速報",
        "name_zh": "四川省地震局 地震速报",
        "url": "https://api.wolfx.jp/sc_eew.json",
        "provider": "Wolfx",
    },
    {
        "id": "fj_eew", "country": "CN", "kind": "eew", "poll": 3,
        "name": "福建省地震局 地震速報",
        "name_zh": "福建省地震局 地震速报",
        "url": "https://api.wolfx.jp/fj_eew.json",
        "provider": "Wolfx",
    },
    {
        "id": "cq_eew", "country": "CN", "kind": "eew", "poll": 3,
        "name": "重庆市地震局 地震速報",
        "name_zh": "重庆市地震局 地震速报",
        "url": "https://api.wolfx.jp/cq_eew.json",
        "provider": "Wolfx",
    },
    # CWA：台湾中央气象署地震速报（Wolfx 中继，免 token）
    {
        "id": "cwa_eew", "country": "TW", "kind": "eew", "poll": 2,
        "name": "CWA 地震速報",
        "name_zh": "台湾中央气象署 地震速报（CWA）",
        "url": "https://api.wolfx.jp/cwa_eew.json",
        "provider": "Wolfx",
    },
    # 气象厅 震度速報（VXSE51）：地震后 1~2 分钟就发，只有地域级震度、没有震源。
    # P2P 那条太慢（实测 60s+），等它回来「震源・震度」已经出了，速报永远被覆盖。
    # 直接读气象厅 bosai 列表（CDN，快），单独一条源。
    {
        "id": "jma_sokuho", "country": "JP", "kind": "sokuho", "poll": 15, "fetch": "sokuho",
        "name": "気象庁 震度速報",
        "name_zh": "日本气象厅 震度速报（VXSE51）",
        "url": "https://www.jma.go.jp/bosai/quake/data/list.json",
        "provider": "JMA",
    },
    # 气象厅 津波予報（官方报文，P2PQuake 中继；30s 一拉，限流 10 次/分）
    # 气象厅长周期地震动观测（HTML+CSV，Shift_JIS；每隔 2 分钟看一次新地震）
    {
        "id": "lpgm", "country": "JP", "kind": "lpgm", "poll": 120, "fetch": "lpgm",
        "name": "気象庁 長周期地震動",
        "name_zh": "日本气象厅 长周期地震动观测",
        "url": "https://www.data.jma.go.jp/eew/data/ltpgm/eq_list.php",
        "provider": "JMA",
    },
    {
        "id": "jma_tsunami", "country": "JP", "kind": "tsunami", "poll": 30,
        "name": "気象庁 津波警報・注意報",
        "name_zh": "日本气象厅 海啸警报·注意报",
        "url": "https://api.p2pquake.net/v2/jma/tsunami?limit=5",
        "provider": "P2PQuake",
    },
    {
        "id": "jma_cat", "country": "JP", "kind": "catalog", "poll": 20,
        "name": "気象庁 震源・震度情報",
        "name_zh": "日本气象厅 震源·震度速报",
        "url": "https://api.wolfx.jp/jma_eqlist.json",
        "provider": "Wolfx",
    },
    {
        "id": "cenc_cat", "country": "CN", "kind": "catalog", "poll": 20,
        "name": "中国地震台网 CENC",
        "name_zh": "中国地震台网 正式测定",
        "url": "https://api.wolfx.jp/cenc_eqlist.json",
        "provider": "Wolfx",
    },
    {
        "id": "jma_obs", "country": "JP", "kind": "obs", "poll": 45,
        "name": "気象庁 震度観測点",
        "name_zh": "日本气象厅 震度观测点（P2PQuake 中继 JMXSEIS）",
        "url": "https://api.p2pquake.net/v2/jma/quake?limit=10",
        "provider": "P2PQuake",
    },
    # ---- 台湾 P-Alert：免 token，公开 GraphQL，每秒一次全台 PGA ----
    # 786 个测站的实时最大地表加速度，是「最新测站」这一层最细的数据。
    # 官方前端也走同一个 query，token 传空字符串就能用。
    {
        "id": "palert", "country": "TW", "kind": "obs", "poll": 10,
        "name": "P-Alert 強震網",
        "name_zh": "台湾 P-Alert 强震网（实时 PGA）",
        "url": "https://palert.earth.sinica.edu.tw/graphql/",
        "provider": "IES Sinica",
        "post": "palert",
    },
    # EQSC：equake.top 的 JMA 速报，无 token 直接 401
    {
        "id": "eqsc", "country": "JP", "kind": "eew", "poll": 2,
        "name": "EQSC JMA EEW",
        "name_zh": "EQSC 気象庁 緊急地震速報",
        "url": "https://equake.top/jma_eew.json",
        "provider": "EQSC",
        "need_token": "eqsc",
    },
    # TREM-Net：ExpTech 的台湾自建测站网，每秒一帧即时震度；WebSocket 要 API key。
    # 和 P-Alert 是同一片地方的两种测站数据——所以前端同一时刻只画其中一个，
    # 由「延迟最低」来轮流坐庄，两套点绝不允许叠在一起（见 app.js drawPalert/drawTrem）。
    {
        "id": "trem", "country": "TW", "kind": "obs", "poll": 1,
        "name": "TREM-Net 即時震度",
        "name_zh": "TREM-Net 台湾即时震度（ExpTech）",
        "url": "wss://lb-2.exptech.dev/websocket",
        "provider": "ExpTech",
        "need_token": "trem",
        # 客户端还没接：core.exptech.dev 在本机解析不到、lb-* 只回 Cloudflare 的 404，
        # 拿令牌也连不上。标记 wip，面板显示「接入中」，不白起线程。
        "wip": True,
    },
    # SeedLink：TCP + MiniSEED 波形流。协议本身免 key，但日本台站（NIED Hi-net/F-net）
    # 要注册账号（hinetwww11.bosai.go.jp/auth/seed/）；公开的 GEOFON 没有日本台站。
    # 填 {"seedlink": "host:port"} 才启用。
    {
        "id": "seedlink", "country": "JP", "kind": "wave", "poll": 0,
        "name": "SeedLink 波形",
        "name_zh": "SeedLink 实时波形（需指定服务器）",
        "url": "tcp://geofon.gfz-potsdam.de:18000",
        "provider": "SeedLink",
        "need_token": "seedlink",
    },
    # Jian Project：wss://api.sismotide.top/all（备用 api.jianproject.cloud），
    # 聚合 JMA/CEA/CWA 预警 + /kmoni、/s-net 测站通道。鉴权分两步：
    # 登录密钥 lk_ → POST auth.sismotide.top/api/refresh 换长期票 rt_ →
    # POST /api/access 换访问令牌 at_ → 连上 15 秒内发纯文本 at_…。
    {
        "id": "jian", "country": "JP", "kind": "eew", "poll": 1,
        "name": "Jian Project 預警流",
        "name_zh": "Jian Project 多源预警（JMA/CEA/CWA）",
        "url": "wss://api.sismotide.top/all",
        "provider": "Jian Project",
        "need_token": "jian",
    },
]

# 需要授权才能用的源：没有 token 时只在数据源面板里显示「待授权」，
# 不起线程、不参与延迟竞争；填了 token 立刻生效。
#
# 令牌按「配置档案」分开存：
#   ""     机器级默认档，读 data/tokens.json，所有没有专属令牌的账号共用
#   "alice" 某个账号自己的档，读 data/users/alice/tokens.json
# 线程绑定了哪个档案，has_token() 就查哪个档案（server.enter() 负责绑定）。
TOKEN_SETS = {"": {}}
TOKENS = TOKEN_SETS[""]          # 兼容旧引用：机器级默认档
_PROFILE_TLS = threading.local()


def set_profile(name):
    """绑定当前线程使用的令牌档案（空字符串=机器级默认）"""
    _PROFILE_TLS.name = name or ""


def current_profile():
    return getattr(_PROFILE_TLS, "name", "")


def set_tokens(d, profile=None):
    p = current_profile() if profile is None else (profile or "")
    t = TOKEN_SETS.get(p)
    if t is None:
        t = TOKEN_SETS[p] = {}
    t.clear()
    t.update(d or {})


def tokens(profile=None):
    p = current_profile() if profile is None else (profile or "")
    return TOKEN_SETS.get(p) or {}


def has_token(src, profile=None):
    key = src.get("need_token")
    if not key:
        return True
    return bool(str(tokens(profile).get(key) or "").strip())

JST = 9
CST = 8


def http_json(url, timeout=8):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/json,text/plain,*/*",
        "Cache-Control": "no-cache",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    if not raw.strip():
        raise ValueError("空响应")
    return json.loads(raw.decode("utf-8", "replace"))


class Session:
    """按来源复用的 HTTPS 长连接。

    直连走 TLS 每次握手要几百毫秒，走了代理更慢（实测 0.4~8 秒），
    所以这里保持 CONNECT 隧道 + TLS 连接复用，失败才重建。
    """

    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.scheme = u.scheme
        self.host = u.hostname
        self.port = u.port or (443 if u.scheme == "https" else 80)
        self.path = u.path + (("?" + u.query) if u.query else "")
        self.conn = None
        self.ctx = ssl.create_default_context()
        self.proxy = self._proxy()

    def _proxy(self):
        if self.scheme != "https":
            return None
        p = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
        if not p:
            return None
        p = p.replace("http://", "").rstrip("/")
        if ":" in p:
            host, port = p.rsplit(":", 1)
            try:
                return (host, int(port))
            except ValueError:
                return None
        return (p, 8080)

    def _open(self, timeout):
        if self.proxy:
            c = http.client.HTTPSConnection(
                self.proxy[0], self.proxy[1], timeout=timeout, context=self.ctx)
            c.set_tunnel(self.host, self.port)
        else:
            c = http.client.HTTPSConnection(
                self.host, self.port, timeout=timeout, context=self.ctx)
        self.conn = c
        return c

    def close(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
        self.conn = None

    def get_json(self, timeout=12):
        err = None
        for attempt in (1, 2):
            try:
                c = self.conn or self._open(timeout)
                c.request("GET", self.path, headers={
                    "User-Agent": UA,
                    "Accept": "application/json,text/plain,*/*",
                    "Cache-Control": "no-cache",
                })
                resp = c.getresponse()
                raw = resp.read()
                status = resp.status
                if resp.will_close:
                    self.close()
                if status != 200:
                    raise ValueError("HTTP %d" % status)
                if not raw.strip():
                    raise ValueError("空响应")
                return json.loads(raw.decode("utf-8", "replace"))
            except Exception as exc:
                err = exc
                self.close()
                if attempt == 2:
                    break
                time.sleep(0.25)
        raise err


_SESSIONS = {}


def session_json(url, timeout=12):
    s = _SESSIONS.get(url)
    if s is None:
        s = _SESSIONS[url] = Session(url)
    return s.get_json(timeout)


def parse_dt(s, tz_hours):
    """'2026/10/31 09:32:51' 或 '2026-10-31 06:15' → epoch"""
    if not s:
        return None
    s = str(s).strip().replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.datetime.strptime(s, fmt)
            return dt.replace(tzinfo=datetime.timezone(
                datetime.timedelta(hours=tz_hours))).timestamp()
        except ValueError:
            continue
    return None


def fmt_ts(ts, tz_hours):
    if not ts:
        return ""
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone(datetime.timedelta(hours=tz_hours))
    ).strftime("%m-%d %H:%M:%S")


def to_float(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(str(v).replace("km", "").strip())
    except (TypeError, ValueError):
        return default


def jma_grade(max_intensity):
    """JMA 震度字符串标准化：'5-' → '5弱'，'5+' → '5強'"""
    s = str(max_intensity or "").strip()
    if not s:
        return "?"
    if s in ("5-", "5弱"):
        return "5弱"
    if s in ("5+", "5強"):
        return "5強"
    if s in ("6-", "6弱"):
        return "6弱"
    if s in ("6+", "6強"):
        return "6強"
    return s


def grade_value(label):
    """震度标签 → 0~7 数值（5弱=4.5 等）"""
    m = {"0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
         "5弱": 4.5, "5強": 5.0, "6弱": 5.5, "6強": 6.0, "7": 6.5}
    return m.get(label, 0)


JP_PREFECTURES = [
    "北海道", "青森県", "岩手県", "宮城県", "秋田県", "山形県", "福島県",
    "茨城県", "栃木県", "群馬県", "埼玉県", "千葉県", "東京都", "神奈川県",
    "新潟県", "富山県", "石川県", "福井県", "山梨県", "長野県", "岐阜県",
    "静岡県", "愛知県", "三重県", "滋賀県", "京都府", "大阪府", "兵庫県",
    "奈良県", "和歌山県", "鳥取県", "島根県", "岡山県", "広島県", "山口県",
    "徳島県", "香川県", "愛媛県", "高知県", "福岡県", "佐賀県", "長崎県",
    "熊本県", "大分県", "宮崎県", "鹿児島県", "沖縄県",
]
_PREF_SORTED = sorted(JP_PREFECTURES, key=len, reverse=True)


def pref_of_area(name):
    """'宮城県北部' → '宮城県'；找不到返回 None"""
    if not name:
        return None
    for p in _PREF_SORTED:
        if str(name).startswith(p):
            return p
    return None


def parse_pref_areas(areas):
    """把气象厅的予想震度地域表压成「都道府县 → 最大予想震度」

    兼容几种上游字段命名（Wolfx / P2PQuake 的写法不完全一致）。
    """
    out = {}
    for a in areas or []:
        if not isinstance(a, dict):
            continue
        nm = (a.get("Chiiki") or a.get("Area") or a.get("Name")
              or a.get("Region") or a.get("area") or "")
        lab = (a.get("Shindo2") or a.get("Shindo1") or a.get("Shindo")
               or a.get("Intensity") or a.get("MaxIntensity"))
        if lab is None:
            continue
        lab = jma_grade(lab)
        if not lab or lab == "?":
            continue
        pref = pref_of_area(nm)
        if not pref:
            continue
        v = grade_value(lab)
        cur = out.get(pref)
        if cur is None or v > cur["value"]:
            out[pref] = {"label": lab, "value": v, "area": str(nm)}
    return out


def _digits(s):
    return re.sub(r"[^\d.\-]", "", str(s or "")) or "0"


def norm_jma_eew(d):
    if not d or not d.get("EventID"):
        return None
    lat, lon = to_float(d.get("Latitude")), to_float(d.get("Longitude"))
    if lat is None or lon is None:
        return None
    origin = parse_dt(d.get("OriginTime"), JST)
    report = parse_dt(d.get("AnnouncedTime"), JST)
    label = jma_grade(d.get("MaxIntensity"))
    areas = d.get("WarnArea") or []
    pref_int = parse_pref_areas(areas)
    return {
        "uid": "JP-" + str(d.get("EventID")),
        "country": "JP",
        "source": "jma_eew",
        "source_name": "気象庁 緊急地震速報",
        "event_id": str(d.get("EventID")),
        "serial": int(to_float(d.get("Serial"), 1) or 1),
        "origin_ts": origin,
        "report_ts": report,
        "place": d.get("Hypocenter") or "不明",
        "lat": lat, "lon": lon,
        "depth": to_float(d.get("Depth"), 10.0) or 10.0,
        "mag": to_float(d.get("Magnitude") or d.get("Magunitude"), 0.0) or 0.0,
        "max_intensity": label,
        "max_intensity_num": grade_value(label),
        "warn": bool(d.get("isWarn")),
        "final": bool(d.get("isFinal")),
        "cancel": bool(d.get("isCancel")),
        "training": bool(d.get("isTraining")) or bool(d.get("isAssumption")),
        "is_sea": bool(d.get("isSea")),
        "areas": areas,
        "pref_intensity": pref_int,
        "title": d.get("Title") or "緊急地震速報",
        "raw": {k: d.get(k) for k in
                ("Title", "CodeType", "Serial", "AnnouncedTime", "OriginTime",
                 "Hypocenter", "MaxIntensity", "MaxIntChange", "isWarn", "isFinal",
                 "isCancel", "isTraining", "Accuracy", "OriginalText")},
    }


def _iso_ts(s):
    """ISO8601（带 +09:00 / Z）→ epoch"""
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(
            str(s).strip().replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def norm_jian_eew(d):
    """Jian Project 的 jma-eew 帧 → 统一事件模型。

    帧长这样：
      {"type":"jma-eew","Data":{"id":"20261026131230","originTime":"2026-10-26T13:12:22+09:00",
        "latitude":40.4,"longitude":142.2,"depth":50,"magnitude":3.7,"placeName":"岩手県沖",
        "infoTypeName":"予報","intensity":"2","serial":3,"isWarn":false,"isFinal":true,
        "isCancel":false,"isTraining":false,"isPLUM":false,"warnArea":[]},"md5":"..."}
    注意没有「発表時刻」字段，report_ts 只能用收到的时间——这也是它的延迟优势所在。
    """
    if not d:
        return None
    lat, lon = to_float(d.get("latitude")), to_float(d.get("longitude"))
    if lat is None or lon is None:
        return None
    label = jma_grade(d.get("intensity"))
    areas = d.get("warnArea") or []
    return {
        "uid": "JP-" + str(d.get("id")),
        "country": "JP",
        "source": "jian",
        "source_name": "Jian Project",
        "event_id": str(d.get("id")),
        "serial": int(to_float(d.get("serial"), 1) or 1),
        "origin_ts": _iso_ts(d.get("originTime")),
        "report_ts": time.time(),
        "place": d.get("placeName") or "不明",
        "lat": lat, "lon": lon,
        "depth": to_float(d.get("depth"), 10.0) or 10.0,
        "mag": to_float(d.get("magnitude"), 0.0) or 0.0,
        "max_intensity": label,
        "max_intensity_num": grade_value(label),
        "warn": bool(d.get("isWarn")),
        "final": bool(d.get("isFinal")),
        "cancel": bool(d.get("isCancel")),
        "training": bool(d.get("isTraining")),
        "is_sea": False,
        "areas": areas,
        "pref_intensity": parse_pref_areas(areas),
        "title": d.get("infoTypeName") or "緊急地震速報",
        "plum": bool(d.get("isPLUM")),
        "raw": d,
    }


CN_PROVINCES = [
    "北京", "天津", "河北", "山西", "内蒙古", "辽宁", "吉林", "黑龙江",
    "上海", "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南",
    "湖北", "湖南", "广东", "广西", "海南", "重庆", "四川", "贵州",
    "云南", "西藏", "陕西", "甘肃", "青海", "宁夏", "新疆",
    "香港", "澳门", "台湾",
]


def province_of(place):
    """从「四川绵阳市北川县」这类地名里取省级前缀"""
    p = str(place or "").strip()
    for name in CN_PROVINCES:
        if p.startswith(name):
            return name
    return ""


def norm_cn_eew(d, source_id="cn_eew", source_name="中国地震台网"):
    if not d or not (d.get("EventID") or d.get("ID")):
        return None
    lat, lon = to_float(d.get("Latitude")), to_float(d.get("Longitude"))
    if lat is None or lon is None:
        return None
    origin = parse_dt(d.get("OriginTime"), CST)
    report = parse_dt(d.get("ReportTime"), CST)
    mi = to_float(d.get("MaxIntensity"), 0.0) or 0.0
    place_raw = d.get("HypoCenter") or "未知"
    return {
        "uid": "CN-" + str(d.get("EventID") or d.get("ID")),
        "country": "CN",
        "source": source_id,
        "source_name": source_name,
        "province": province_of(place_raw),
        "event_id": str(d.get("EventID") or d.get("ID")),
        "serial": int(to_float(d.get("ReportNum"), 1) or 1),
        "origin_ts": origin,
        "report_ts": report,
        "place": place_raw,
        "lat": lat, "lon": lon,
        "depth": to_float(d.get("Depth"), 10.0) or 10.0,
        "mag": to_float(d.get("Magnitude") or d.get("Magunitude"), 0.0) or 0.0,
        "max_intensity": ("%.1f" % mi).rstrip("0").rstrip(".") if mi else "?",
        "max_intensity_num": mi,
        "warn": True,
        "final": False,
        "cancel": False,
        "training": False,
        "is_sea": False,
        "areas": [],
        "title": "中国地震预警",
        "raw": d,
    }


SCALE_LABEL = {
    -1: "不明", 0: "0", 10: "1", 20: "2", 30: "3", 40: "4",
    45: "5弱", 50: "5強", 55: "6弱", 60: "6強", 70: "7",
}


def norm_jma_obs(payload):
    """气象厅震度观测点 → 按都道府县聚合的最新一次地震实测震度。

    P2PQuake 中继的是气象厅电文（震度速报 / 详细震度），每个观测点带
    市町村名与震度，没有经纬度，所以按「都道府县 → 最大震度」聚合，
    再落到内置城市代表点上。
    """
    if not isinstance(payload, list) or not payload:
        return None

    groups = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        if item.get("code") not in (551, 556):
            continue
        eq = item.get("earthquake") or {}
        hp = eq.get("hypocenter") or {}
        t = eq.get("time") or item.get("time")
        if not t:
            continue
        key = (t, hp.get("name") or "")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "time": t,
                "origin_ts": parse_dt(t, JST),
                "place": hp.get("name") or "不明",
                "lat": to_float(hp.get("latitude")),
                "lon": to_float(hp.get("longitude")),
                "depth": to_float(hp.get("depth"), 0.0) or 0.0,
                "mag": to_float(hp.get("magnitude"), 0.0) or 0.0,
                "max_scale": -1,
                "reported_ts": 0,
                "pref": {},
                "points": 0,
                "pts": {},          # addr -> 最大震度（真实观测点，稍后补经纬度）
            }
        # 同一地震多条电文（速報→詳細）取观测点最全的那条
        pts = item.get("points") or []
        if len(pts) >= g["points"]:
            g["points"] = len(pts)
            for pt in pts:
                if not isinstance(pt, dict):
                    continue
                sc = pt.get("scale")
                pref = pt.get("pref") or ""
                addr = pt.get("addr") or ""
                if sc is None or sc < 0 or not pref:
                    continue
                cell = g["pref"].get(pref)
                if cell is None:
                    g["pref"][pref] = {"scale": sc, "label": SCALE_LABEL.get(sc, "?"),
                                       "points": [addr] if addr else []}
                else:
                    cell["points"].append(addr)
                    if sc > cell["scale"]:
                        cell["scale"] = sc
                        cell["label"] = SCALE_LABEL.get(sc, "?")
                if addr:
                    # 同一观测点可能出现在速报和詳細里，取较大值
                    old_pt = g["pts"].get(addr)
                    if old_pt is None or sc > old_pt["scale"]:
                        g["pts"][addr] = {"scale": sc, "pref": pref}
                if sc > g["max_scale"]:
                    g["max_scale"] = sc
            g["reported_ts"] = parse_dt(str(item.get("time") or "")[:19], JST) or g["reported_ts"]

    if not groups:
        return None
    best = max(groups.values(), key=lambda g: (g.get("origin_ts") or 0))
    return {
        "kind": "obs",
        "source": "jma_obs",
        "source_name": "気象庁 震度観測点",
        "origin_ts": best["origin_ts"],
        "report_ts": best["reported_ts"] or best["origin_ts"],
        "place": best["place"],
        "lat": best["lat"], "lon": best["lon"],
        "depth": best["depth"], "mag": best["mag"],
        "max_scale": best["max_scale"],
        "max_label": SCALE_LABEL.get(best["max_scale"], "?"),
        "point_count": best["points"],
        # 真实观测点（气象厅电文里只有地点名，没有经纬度；由服务端异步补齐坐标）
        "points": [{"addr": a, "pref": v["pref"], "scale": v["scale"],
                    "label": SCALE_LABEL.get(v["scale"], "?")}
                   for a, v in sorted(best["pts"].items(), key=lambda kv: -kv[1]["scale"])][:1200],
        # pref -> {scale, label, n}
        "pref": {k: {"scale": v["scale"], "label": v["label"], "n": len(v["points"])}
                 for k, v in best["pref"].items()},
        "scales": dict(SCALE_LABEL),
    }


def norm_jma_catalog(d):
    out = []
    if not isinstance(d, dict):
        return out
    for key in sorted(d.keys()):
        it = d.get(key)
        if not isinstance(it, dict) or not it.get("latitude"):
            continue
        ts = parse_dt(it.get("time_full") or it.get("time"), JST)
        label = jma_grade(it.get("shindo"))
        out.append({
            "uid": "JPC-" + str(it.get("EventID") or key),
            "country": "JP",
            "source": "jma_cat",
            "event_id": str(it.get("EventID") or key),
            "origin_ts": ts,
            "place": it.get("location") or "不明",
            "lat": to_float(it.get("latitude"), 0.0),
            "lon": to_float(it.get("longitude"), 0.0),
            "depth": to_float(it.get("depth"), 0.0),
            "mag": to_float(it.get("magnitude"), 0.0),
            "max_intensity": label,
            "max_intensity_num": grade_value(label),
            "info": it.get("info") or "",
            "kind": "catalog",
        })
    return out


def norm_cenc_catalog(d):
    out = []
    if not isinstance(d, dict):
        return out
    for key in sorted(d.keys()):
        it = d.get(key)
        if not isinstance(it, dict) or not it.get("latitude"):
            continue
        ts = parse_dt(it.get("time"), CST)
        mi = to_float(it.get("intensity"), 0.0) or 0.0
        out.append({
            "uid": "CNC-" + str(it.get("EventID") or key),
            "country": "CN",
            "source": "cenc_cat",
            "event_id": str(it.get("EventID") or key),
            "origin_ts": ts,
            "place": it.get("placeName") or it.get("location") or "未知",
            "province": province_of(it.get("placeName") or it.get("location") or ""),
            "lat": to_float(it.get("latitude"), 0.0),
            "lon": to_float(it.get("longitude"), 0.0),
            "depth": to_float(it.get("depth"), 0.0),
            "mag": to_float(it.get("magnitude"), 0.0),
            "max_intensity": ("%.0f" % mi) if mi else "?",
            "max_intensity_num": mi,
            "reviewed": it.get("type") == "reviewed",
            "kind": "catalog",
        })
    return out


def norm_cwa_eew(d):
    """CWA 地震速报 → 统一事件结构

    Wolfx 中继的字段：ID / ReportNum / OriginTime / HypoCenter /
    Latitude / Longitude / Magnitude / Depth / MaxIntensity / isCancel。
    CWA 用 5-/5+ 的震度写法，跟 JMA 一样，所以直接过 jma_grade。
    """
    if not isinstance(d, dict) or d.get("ID") in (None, "", 0):
        return None
    lat, lon = to_float(d.get("Latitude")), to_float(d.get("Longitude"))
    if lat is None or lon is None:
        return None
    origin = parse_dt(d.get("OriginTime"), 8)
    report = parse_dt(d.get("ReportTime"), 8) or origin
    label = jma_grade(d.get("MaxIntensity"))
    cancel = bool(d.get("isCancel"))
    place = (d.get("HypoCenter") or "台湾附近").strip()
    return {
        "uid": "CWA-" + str(d.get("ID")),
        "country": "TW",
        "source": "cwa_eew",
        "source_name": "台湾中央气象署 CWA",
        "event_id": str(d.get("ID")),
        "serial": int(to_float(d.get("ReportNum"), 1) or 1),
        "origin_ts": origin,
        "report_ts": report,
        # 地名前缀「中国台湾」：这是政治硬约束，任何来源都统一
        "place": ("中国台湾 " + place) if not place.startswith("中国台湾") else place,
        "lat": lat, "lon": lon,
        "depth": to_float(d.get("Depth"), 10.0) or 10.0,
        "mag": to_float(d.get("Magnitude") or d.get("Magunitude"), 0.0) or 0.0,
        "max_intensity": label,
        "max_intensity_num": grade_value(label),
        "warn": not cancel,
        "final": False,
        "cancel": cancel,
        "training": False,
        "is_sea": False,
        "areas": [],
        "pref_intensity": {},
        "title": "地震速報（中国台湾）",
        "raw": d,
    }


NORMALIZERS = {
    "jma_eew": norm_jma_eew,
    "cn_eew": lambda p: norm_cn_eew(p, "cn_eew", "中国地震台网"),
    "sc_eew": lambda p: norm_cn_eew(p, "sc_eew", "四川省地震局"),
    "fj_eew": lambda p: norm_cn_eew(p, "fj_eew", "福建省地震局"),
    "cq_eew": lambda p: norm_cn_eew(p, "cq_eew", "重庆市地震局"),
    "cwa_eew": norm_cwa_eew,
    "jma_obs": norm_jma_obs,
}


# ---------------- 台湾 P-Alert：公开 GraphQL，免 token ----------------
# 官方前端（palert.earth.sinica.edu.tw/realtime）就用这条 query，token 传空串。
# 返回 {测站代号: PGA(gal)}，全台 ~500 站在线，每秒一帧；这里按 10 秒拉一次，
# 够看出来「哪一片在摇」，又不至于把对方服务器当 CDN 刷。
PALERT_Q = ("query ($recordTime: Float, $type: Int, $token: String) { "
            "realtimePGA(recordTime: $recordTime, type: $type, token: $token) "
            "{ dataVals timestamp } }")

_PALERT_CTX = [None]
_PALERT_OPEN = [None]


def _palert_opener():
    """palert.earth.sinica.edu.tw 的证书缺 Subject Key Identifier，
    Python 3.13 的严格校验会直接拒（curl / 浏览器都放行）。只对这一个
    只读公共源放开校验，同时**不走系统代理**——代理对它返回 502。"""
    if _PALERT_OPEN[0] is None:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _PALERT_CTX[0] = ctx
        _PALERT_OPEN[0] = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ctx),
        )
    return _PALERT_OPEN[0]


def palert_realtime(data_type=0, record_time=None):
    """P-Alert 实时 PGA/PGV。data_type: 0=PGA, 1=PGV"""
    body = json.dumps({
        "query": PALERT_Q,
        "variables": {
            "recordTime": float(record_time if record_time is not None else int(time.time())),
            "type": int(data_type),
            "token": "",
        },
    }).encode()
    req = urllib.request.Request(
        "https://palert.earth.sinica.edu.tw/graphql/", data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            # 官方只认自己的实时页当 Referer，不带就被挡
            "Referer": "https://palert.earth.sinica.edu.tw/realtime",
            "User-Agent": UA,
        })
    with _palert_opener().open(req, timeout=12) as r:
        raw = r.read()
    if not raw.strip():
        raise ValueError("空响应")
    d = json.loads(raw.decode("utf-8", "replace"))
    if d.get("errors"):
        raise ValueError("graphql: %s" % str(d["errors"])[:120])
    rt = ((d.get("data") or {}).get("realtimePGA") or {})
    vals = rt.get("dataVals") or {}
    if not isinstance(vals, dict) or not vals:
        raise ValueError("没有测站数据")
    out = {}
    for k, v in vals.items():
        try:
            out[str(k)] = round(float(v), 3)
        except (TypeError, ValueError):
            continue
    return {"vals": out, "ts": rt.get("timestamp") or ""}


TSU_GRADE = {
    "MajorWarning": "大津波警報",
    "Warning": "津波警報",
    "Watch": "津波注意報",
    "Unknown": "津波予報",
}


def norm_tsunami(payload):
    """气象厅津波予報列表 → 最新一条（按发表时刻取最新）

    字段：cancelled / issue.time / areas[].grade|name|immediate|firstHeight|maxHeight
    解除报（cancelled=true）也会保留，前端据此把横幅撤掉。
    """
    items = payload if isinstance(payload, list) else [payload]
    items = [x for x in items if isinstance(x, dict)]
    if not items:
        return None
    def _t(it):
        return parse_dt((it.get("issue") or {}).get("time"), JST) or 0
    it = max(items, key=_t)
    issue = it.get("issue") or {}
    ts = parse_dt(issue.get("time"), JST)
    areas = []
    for a in it.get("areas") or []:
        if not isinstance(a, dict):
            continue
        fh = a.get("firstHeight") or {}
        mh = a.get("maxHeight") or {}
        areas.append({
            "grade": a.get("grade") or "Unknown",
            "label": TSU_GRADE.get(a.get("grade"), "津波予報"),
            "name": a.get("name") or "",
            "immediate": bool(a.get("immediate")),
            "first": {
                "time": fh.get("arrivalTime") or "",
                "cond": fh.get("condition") or "",
            },
            "max": {
                "text": mh.get("description") or "",
                "value": to_float(mh.get("value")) if mh.get("value") is not None else None,
            },
        })
    return {
        "id": it.get("id") or "",
        "issued_ts": ts,
        "cancelled": bool(it.get("cancelled")),
        "areas": areas,
        "source": issue.get("source") or "気象庁",
        "raw_time": issue.get("time") or "",
    }


# ---------------- 气象厅 长周期地震动（LPGM） ----------------
# 列表页 eq_list.php（HTML，UTF-8）给出最近 30 天每次地震的最大阶級；
# 详情数据在 /eew/data/ltpgm/{eventId}/data/level.csv（Shift_JIS），
# 每行一个观测点：最大長周期地震動階級 + 1~7 秒各周期段的阶級。

LPGM_BASE = "https://www.data.jma.go.jp/eew/data/ltpgm"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def _http_bytes(url, timeout=25):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with op.open(req, timeout=timeout) as r:
        return r.read()


def _zen_num(s):
    tbl = str.maketrans("０１２３４５６７８９．", "0123456789.")
    return str(s or "").translate(tbl)


def lpgm_fetch():
    """抓最新一次长周期观测，返回 {event_id, place, mag, max_class, stations...}"""
    import re, csv, io as _io
    html = _http_bytes(LPGM_BASE + "/eq_list.php").decode("utf-8", "replace")
    rows = re.findall(
        r"<tr>\s*<td>(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2})時(\d{1,2})分</td>"
        r"\s*<td>([^<]*)</td>\s*<td>([^<]*)</td>\s*<td>階級(\d)</td>"
        r".*?eventId=(\d+)", html, re.S)
    if not rows:
        return None
    y, mo, d, hh, mm, place, mag, cls, eid = rows[0]
    origin = datetime.datetime(
        int(y), int(mo), int(d), int(hh), int(mm),
        tzinfo=datetime.timezone(datetime.timedelta(hours=9))).timestamp()
    mag_v = to_float(_zen_num(mag), 0.0)
    cls = int(cls)
    stations = []
    if cls >= 1:
        try:
            raw = _http_bytes("%s/%s/data/level.csv" % (LPGM_BASE, eid)).decode("cp932", "replace")
            lines = [ln for ln in raw.splitlines() if not ln.startswith("#")]
            for row in csv.reader(_io.StringIO("\n".join(lines))):
                if len(row) < 15:
                    continue
                try:
                    c = int(float(row[5]))
                    lat = float(row[13]); lon = float(row[14])
                except (ValueError, IndexError):
                    continue
                if c < 1:
                    continue
                stations.append([round(lat, 3), round(lon, 3), c, row[1]])
                if len(stations) >= 1200:
                    break
        except Exception:
            stations = []
    return {
        "event_id": eid,
        "origin_ts": origin,
        "place": place,
        "mag": mag_v,
        "max_class": cls,
        "stations": stations,
        "updated": time.time(),
        "url": "%s/event.php?eventId=%s" % (LPGM_BASE, eid),
    }


# ---------------- 气象厅 震度速報（VXSE51） ----------------
# 列表 list.json → 找 ttl=震度速報 的最新一条 → 详情 JSON 里的
# Body.Intensity.Observation.Pref[].Area[] 就是「震度○以上を観測した地域」。
# 没有震源（Head 里没有 Earthquake），所以前端只展示地域，不画震中。

JMA_QUAKE_BASE = "https://www.jma.go.jp/bosai/quake/data"
_JMA_INT = {"1": 10, "2": 20, "3": 30, "4": 40,
            "5-": 45, "5+": 50, "6-": 55, "6+": 60, "7": 70}


def sokuho_fetch():
    import json as _json
    try:
        lst = _json.loads(_http_bytes(JMA_QUAKE_BASE + "/list.json", timeout=20).decode("utf-8"))
    except Exception:
        return None
    if not isinstance(lst, list):
        return None
    rows = [x for x in lst if isinstance(x, dict) and x.get("ttl") == "震度速報" and x.get("json")]
    if not rows:
        return None
    rows.sort(key=lambda x: x.get("rdt") or "", reverse=True)
    it = rows[0]
    rdt = it.get("rdt") or ""
    try:
        rep = datetime.datetime.fromisoformat(rdt).timestamp()
    except Exception:
        rep = time.time()
    if time.time() - rep > 1800:          # 半小时前的速报不再占卡片
        return None
    try:
        d = _json.loads(_http_bytes("%s/%s" % (JMA_QUAKE_BASE, it["json"]), timeout=20).decode("utf-8"))
    except Exception:
        return None
    head = d.get("Head") or {}
    body = ((d.get("Body") or {}).get("Intensity") or {}).get("Observation") or {}
    max_int = str(body.get("MaxInt") or "?")
    pref = {}
    regions = {}
    for p in body.get("Pref") or []:
        pn = p.get("Name") or ""
        if not pn:
            continue
        pi = _JMA_INT.get(str(p.get("MaxInt") or ""), 0)
        areas = p.get("Area") or []
        if isinstance(areas, dict):
            areas = [areas]
        pref[pn] = {"scale": pi, "label": str(p.get("MaxInt") or "?"), "n": len(areas)}
        for a in areas:
            an = a.get("Name") or ""
            ai = _JMA_INT.get(str(a.get("MaxInt") or ""), 0)
            if not an or ai <= 0:
                continue
            g = regions.get(ai)
            if g is None:
                regions[ai] = {"scale": ai, "label": str(a.get("MaxInt") or "?"), "prefs": [an]}
            else:
                g["prefs"].append(an)
    if not pref:
        return None
    try:
        origin = datetime.datetime.fromisoformat(head.get("TargetDateTime") or rdt).timestamp()
    except Exception:
        origin = rep
    return {
        "kind": "obs",
        "sim": False,
        "source": "jma_sokuho",
        "source_name": "気象庁 震度速報",
        "origin_ts": origin,
        "report_ts": rep,
        "place": "",
        "quake_str": "震源調査中（震度速報）",
        "issue_str": "気象庁 震度速報 · " + (rdt[11:16] if len(rdt) >= 16 else ""),
        "issue_no": 1,
        "max_scale": _JMA_INT.get(max_int, 0),
        "max_label": max_int,
        "point_count": 0,
        "points": [],
        "pref": pref,
        "regions": sorted(regions.values(), key=lambda g: -g["scale"]),
        "scales": dict(SCALE_LABEL),
    }


def apply_of(source_id, payload):
    """非事件型数据源（观测、海啸等）的归一化入口"""
    if source_id == "jma_obs":
        return norm_jma_obs(payload)
    if source_id == "jma_tsunami":
        return norm_tsunami(payload)
    if source_id == "lpgm":
        return payload
    if source_id == "jma_sokuho":
        return payload
    return None


def catalog_of(source_id, payload):
    if source_id == "jma_cat":
        return norm_jma_catalog(payload)
    if source_id == "cenc_cat":
        return norm_cenc_catalog(payload)
    return []


def now_ts():
    return time.time()


def is_active(e, now=None, max_age=300):
    """预警是否仍在有效窗口内（避免把几天前的旧速报当成警报）"""
    if not e:
        return False
    now = now or time.time()
    if e.get("cancel") or e.get("training"):
        return False
    o = e.get("origin_ts")
    if not o:
        return False
    age = now - o
    return -60 <= age <= max_age


# ---------------- Jian Project（WebSocket 预警流） ----------------
# 鉴权三步（文档「连接鉴权」）：
#   1. 登录密钥 lk_ --POST /api/refresh--> 长期 Token rt_（259200 分钟，基本等于长期）
#   2. rt_ --POST /api/access--> 访问令牌 at_（1 小时，最多 3 条并发连接）
#   3. wss 握手时可带，或连上后 15 秒内发一条纯文本 at_…
# 未授权累计被掐断 10 次会封 IP 一小时，所以这里所有失败都退避，绝不猛重连。

JIAN_AUTH = "https://auth.sismotide.top/api/access"
JIAN_HOST = "api.sismotide.top"
JIAN_BACKUP_HOST = "api.jianproject.cloud"


class JianFeed:
    """Jian Project 的 WebSocket 长连接。

    线程里跑 run()，每条业务帧回调 on_event(dict)。断线按 1/2/4/8/16/30 秒退避，
    at_ 过期（1 小时）前 5 分钟自动换新票，避免连上后被踢。
    """

    def __init__(self, rt, path="/jma-eew", on_event=None, log=None, on_alive=None):
        self.rt = str(rt or "").strip()
        self.path = path
        self.on_event = on_event
        self.on_alive = on_alive            # 心跳/每帧都回调一次，用来刷新源状态
        self.log = log or (lambda *a: None)
        self.at = ""
        self.at_ts = 0.0
        self.at_ttl = 3600
        self.alive = True

    # ---- 换票 ----
    def _access(self):
        req = urllib.request.Request(
            JIAN_AUTH, data=b"", method="POST",
            headers={"Authorization": "Bearer " + self.rt,
                     "Accept": "application/json",
                     "User-Agent": UA})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
        tok = str(d.get("token") or "")
        if not d.get("ok") or not tok.startswith("at_"):
            raise ValueError("换票失败: %s" % str(d)[:160])
        self.at = tok
        self.at_ts = time.time()
        self.at_ttl = int(d.get("expires_after_sec") or 3600)
        self.log("jian at_ ok ttl=%ds" % self.at_ttl)
        return tok

    def _token(self):
        if not self.at or time.time() - self.at_ts > max(60, self.at_ttl - 300):
            self._access()
        return self.at

    # ---- WebSocket ----
    def _connect(self):
        for host in (JIAN_HOST, JIAN_BACKUP_HOST):
            try:
                ctx = ssl.create_default_context()
                raw = socket.create_connection((host, 443), timeout=12)
                s = ctx.wrap_socket(raw, server_hostname=host)
                key = base64.b64encode(os.urandom(16)).decode()
                s.sendall((
                    "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n"
                    "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                    "Sec-WebSocket-Version: 13\r\nOrigin: https://%s\r\n"
                    "User-Agent: %s\r\n\r\n" % (self.path, host, key, host, UA)).encode())
                buf = b""
                while b"\r\n\r\n" not in buf:
                    d = s.recv(4096)
                    if not d:
                        raise EOFError("握手被关闭")
                    buf += d
                head = buf.split(b"\r\n\r\n")[0].decode("latin1")
                if "101" not in head.split("\r\n")[0]:
                    raise EOFError(head.split("\r\n")[0])
                return s, host
            except Exception as exc:
                self.log("jian connect %s 失败: %s" % (host, exc))
                continue
        return None, ""

    @staticmethod
    def _send_text(s, text):
        data = text.encode()
        hdr = bytearray([0x81])
        n = len(data)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr += struct.pack(">H", n)
        else:
            hdr.append(0x80 | 127)
            hdr += struct.pack(">Q", n)
        mask = os.urandom(4)
        hdr += mask
        s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    @staticmethod
    def _pong(s, payload):
        hdr = bytearray([0x8A])
        n = len(payload)
        hdr.append(0x80 | n)
        mask = os.urandom(4)
        hdr += mask
        s.sendall(bytes(hdr) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    @staticmethod
    def _read(s, n):
        b = b""
        while len(b) < n:
            d = s.recv(n - len(b))
            if not d:
                raise EOFError("连接结束")
            b += d
        return b

    def _recv(self, s):
        h = self._read(s, 2)
        op = h[0] & 0x0F
        ln = h[1] & 0x7F
        if ln == 126:
            ln = struct.unpack(">H", self._read(s, 2))[0]
        elif ln == 127:
            ln = struct.unpack(">Q", self._read(s, 8))[0]
        return op, self._read(s, ln)

    def run(self):
        backoff = 1
        while self.alive:
            s = None
            try:
                tok = self._token()
                s, host = self._connect()
                if s is None:
                    raise EOFError("主备都连不上")
                s.settimeout(45)
                self._send_text(s, tok)          # 15 秒内必须发，否则被掐
                self.log("jian 已连 %s%s" % (host, self.path))
                if self.on_alive:
                    self.on_alive()
                backoff = 1
                while self.alive:
                    op, payload = self._recv(s)
                    if op == 1:
                        if self.on_alive:
                            self.on_alive()
                        try:
                            msg = json.loads(payload.decode("utf-8", "replace"))
                        except ValueError:
                            continue
                        if self.on_event:
                            try:
                                self.on_event(msg)
                            except Exception as exc:
                                self.log("jian 处理帧出错: %s" % exc)
                    elif op == 9:
                        self._pong(s, payload)
                        if self.on_alive:
                            self.on_alive()
                    elif op == 8:
                        self.log("jian close: %s" % payload[:80])
                        break
            except Exception as exc:
                self.log("jian 断开: %s" % exc)
                time.sleep(backoff)
                backoff = min(30, backoff * 2)
            finally:
                try:
                    if s:
                        s.close()
                except Exception:
                    pass
