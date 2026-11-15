/* 震线 EEW · 前端
 * 地图优先的极简布局：全屏地图 + 悬浮事件卡 + 最近地震 + 历史抽屉
 */
(() => {
  'use strict';

  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const D2R = Math.PI / 180;
  const R_EARTH = 6371.0;
  // ---------------- P/S 走时模型（AK135 官方表） ----------------
  // 以前是对 JMA2001/iasp91 曲线做幂律拟合（tP=0.232·D^0.90）：近场还凑合，
  // 深源和远场都会偏，而且说到底只是拟合。现在直接用 IASPEI/ISC 认可的
  // AK135 走时表（Kennett 2005《Seismological Tables: ak135》，
  // tools/build_tt_table.py 编译成 tt.js）：距离 0–2° 每 0.1°、3–125° 每 1°，
  // 深度 0–700km 共 17 档，双线性插值。表里没有的（>125° 影区）才退回旧式。
  const TT_AP = 0.232, TT_AS = 0.401, TT_EXP = 0.90;   // 只作兜底
  const TT_TAB = window.TT135 || null;         // AK135（全球）
  const TT_JMA = window.TTJMA || null;         // JMA2001（日本国内）
  const KM_PER_DEG = 111.19492664455873;

  // 日本事件用气象厅自己的 JMA2001 表，其他地方一律 AK135
  function ttModelFor(ev) {
    if (ev && TT_JMA && (ev.country === 'JP' || ev.source === 'jma')) return 'jma2001';
    return 'ak135';
  }
  function ttTabOf(model) {
    return model === 'jma2001' ? TT_JMA : TT_TAB;
  }
  // 表可能以「度」或「km」存距离，统一换到表自己的单位
  function ttToX(tab, km) {
    return tab.unit === 'km' ? Math.max(0, km) : Math.max(0, km) / KM_PER_DEG;
  }
  function ttToKm(tab, x) {
    return tab.unit === 'km' ? x : x * KM_PER_DEG;
  }

  function ttBracket(arr, x) {
    const n = arr.length;
    if (n < 2) return [0, 0];
    if (x <= arr[0]) return [0, 0];
    if (x >= arr[n - 1]) return [n - 2, 1];
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) {
      const m = (lo + hi) >> 1;
      if (arr[m] <= x) lo = m; else hi = m;
    }
    const a = arr[lo], b = arr[lo + 1];
    return [lo, b > a ? (x - a) / (b - a) : 0];
  }

  // 走时（秒）：phase='P'/'S'，km 震中距，depth 震源深度
  function ttInterp(model, phase, km, depth) {
    const tab = ttTabOf(model);
    if (!tab) return null;
    const rows = tab[phase], D = tab.dist, H = tab.depth;
    if (!rows) return null;
    const x = ttToX(tab, km);
    if (x > D[D.length - 1]) {
      // JMA2001 只到 2000km，超出就交给 AK135
      return model === 'ak135' ? null : ttInterp('ak135', phase, km, depth);
    }
    const dep = clamp(depth || 0, 0, H[H.length - 1]);
    const bh = ttBracket(H, dep), bd = ttBracket(D, x);
    const c0 = rows[bh[0]], c1 = rows[bh[0] + 1];
    const v00 = c0[bd[0]], v01 = c0[bd[0] + 1];
    const v10 = c1[bd[0]], v11 = c1[bd[0] + 1];
    if (v00 < 0 || v01 < 0 || v10 < 0 || v11 < 0) return null;
    const top = v00 + (v01 - v00) * bd[1];
    const bot = v10 + (v11 - v10) * bd[1];
    return top + (bot - top) * bh[1];
  }

  // 反解：走了 t 秒 → 震中距（km）
  function ttInvert(model, phase, t, depth) {
    const tab = ttTabOf(model);
    if (!tab) return null;
    const rows = tab[phase], D = tab.dist, H = tab.depth;
    if (!rows) return null;
    const dep = clamp(depth || 0, 0, H[H.length - 1]);
    const bh = ttBracket(H, dep);
    const c0 = rows[bh[0]], c1 = rows[bh[0] + 1];
    const at = i => c0[i] + (c1[i] - c0[i]) * bh[1];
    if (at(0) < 0) return null;
    if (t <= at(0)) return 0;
    if (t >= at(D.length - 1)) {
      return model === 'ak135' ? ttToKm(tab, D[D.length - 1])
        : ttInvert('ak135', phase, t, depth);
    }
    let lo = 0, hi = D.length - 1;
    while (hi - lo > 1) {
      const m = (lo + hi) >> 1;
      if (at(m) <= t) lo = m; else hi = m;
    }
    const a = at(lo), b = at(hi);
    const f = b > a ? (t - a) / (b - a) : 0;
    return ttToKm(tab, D[lo] + (D[hi] - D[lo]) * f);
  }

  // 按账号隔离：eew.<用户名>.<键>。登录页会在跳转前写入 eew.user，
  // 这样同一台电脑换账号登录，看不到上一个人的阈值/关注点/已播报记录。
  const LS = {
    _key(k) {
      let u = '';
      try { u = localStorage.getItem('eew.user') || ''; } catch (e) { }
      return 'eew.' + (u ? u + '.' : '') + k;
    },
    get(k, d) { try { const v = localStorage.getItem(LS._key(k)); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(LS._key(k), JSON.stringify(v)); } catch (e) { } },
  };

  // ---------------- 深色 / 浅色 ----------------
  // 挂在 <html> 上而不是 <body>：index.html 的 <head> 里有一段同步脚本，
  // 首帧之前就把 class 打好，否则每次启动都会先闪一下深色再变白。
  const THEME_KEY = 'eew.theme';
  function themeNow() {
    try { return localStorage.getItem(THEME_KEY) === 'light' ? 'light' : 'dark'; } catch (e) { return 'dark'; }
  }
  function applyTheme(t, save) {
    const light = t === 'light';
    document.documentElement.classList.toggle('light', light);
    const b = document.querySelector('#btnTheme');
    if (b) {
      b.classList.toggle('on', light);
      b.title = light ? '切到深色 (T)' : '切到浅色 (T)';
      b.setAttribute('aria-label', b.title);
    }
    if (save !== false) { try { localStorage.setItem(THEME_KEY, light ? 'light' : 'dark'); } catch (e) { } }
    // 设置面板里的分段控件跟着一起亮（从 HUD 按钮或 T 键切换时也要同步）
    const seg = document.querySelector('#themeSeg');
    if (seg) for (const sb of seg.querySelectorAll('button[data-theme]')) {
      sb.classList.toggle('on', (sb.dataset.theme === 'light') === light);
    }
    syncPalette();
  }

  const state = {
    offset: 0,
    sources: [],
    eew: { JP: null, CN: null, TW: null },
    eewList: [],          // 同时进行的所有地震（多事件适配）
    focusUid: null,       // 手动选中的那起（null = 自动跟最新/最强）
    events: [],
    alerts: [],
    sim: null,
    simSpeed: 1,
    primary: {}, palert: null,
    stations: [],
    builtin: 0,
    layers: LS.get('layers3', { stations: 1, field: 1, pref: 1, waves: 1, labels: 1, gqst: 1, palert: 1 }),
    stColor: LS.get('stColor', 'auto'),
    serverAudio: false,
    obs: null,
    tsunami: null, lpgm: null, obsVoiceUid: 0, obsVoiceRisk: -1, obsKey: '', obsAt: 0,
    palertStations: [],
    autoEv: null,      // 本地推算的弱震（JMA 不发时补位）
    focus: LS.get('focus', { name: '北京', lat: 39.904, lon: 116.407, country: 'CN' }),
    // 额外关注点：最多 3 个。事件卡同时列各点预计震度，任一点超标都响铃。
    watches: LS.get('watches', []),
    settings: Object.assign({
      sound: true, speech: false, thJp: 4.5, thCn: 6, gqMag: 3,
      gqAlert: 4.5, notify: true,      // GQ 弹窗门槛 / 桌面通知
      follow: true,                    // 有地震就飞到震中，没地震回中日全景
      quiet: false, quietFrom: 23, quietTo: 7, quietMag: 5.5,   // 夜间免打扰

      waveN: 10, autoZoom: true,
      voice: true, human: true, stVoice: true, stAfterFinal: true, stHuman: false,
      decay: 1.6, decayV: 2,
      chime: 'builtin', pack: '',
    }, LS.get('settings', {})),
    seen: new Set(LS.get('seen', [])),
    lastAlarmKey: '', lastAlarmAt: 0,
    dismissedKey: null,
    hiddenUntil: 0,
    replayAt: 0,
    histReplayAt: 0,
    historyEvent: null,
    simPick: { on: false, lat: null, lon: null, place: '' },
    reactKey: '', reacted: new Set(), reactCount: 0, lastStSound: 0,
    shakeN: 0, shakeSince: 0, shakeFrameAt: 0,
    stUid: '', stKey: '', stMax: -1, stLvAt: {}, stRec: [], tipPin: '',
    kmoni: { ok: false, at: 0, ts: '', lv: {}, n: 0 },
    history: { days: 30, minmag: 4.5, items: [], loading: false },
    connected: false,
  };

  // 老存档迁移：decay 的旧默认值是 1、也有人拖到 0.5（老包络下最快的档）。
  // 新包络整体放慢约一倍，旧值全是按「快档」调出来的：低于新默认 1.6 的直接
  // 抬到 1.6，高于 1.6 的按 1.6× 等比放大；标记 decayV=2 后不再动第二次。
  function bumpDecay(o) {
    if (!o || o.decay === undefined || o.decayV) return;
    const v = o.decay < 1.6 ? 1.6 : o.decay * 1.6;
    o.decay = Math.min(6, Math.max(0.5, Math.round(v * 4) / 4));
    o.decayV = 2;
  }
  bumpDecay(state.settings);
  try { LS.set('settings', state.settings); } catch (e) { /* 存不下就算了 */ }

  const RISK = ['#42536b', '#3b82f6', '#06b6d4', '#22c55e', '#facc15',
    '#fb923c', '#f97316', '#ef4444', '#dc2626', '#a21caf'];
  const RISK_FILL = ['rgba(66,83,107,0)', 'rgba(59,130,246,.30)', 'rgba(6,182,212,.36)',
    'rgba(34,197,94,.44)', 'rgba(250,204,21,.52)', 'rgba(251,146,60,.58)',
    'rgba(249,115,22,.64)', 'rgba(239,68,68,.70)', 'rgba(220,38,38,.74)', 'rgba(162,28,175,.78)'];
  const RISK_RGB = [[66, 83, 107], [59, 130, 246], [6, 182, 212], [34, 197, 94], [250, 204, 21],
    [251, 146, 60], [249, 115, 22], [239, 68, 68], [220, 38, 38], [162, 28, 175]];
  const RISK_ALPHA = [0, .30, .36, .44, .52, .58, .64, .70, .74, .78];
  const GRADE = ['0', '1', '2', '3', '4', '5弱', '5強', '6弱', '6強', '7'];
  const DU_ROMAN = ['', 'Ⅰ', 'Ⅱ', 'Ⅲ', 'Ⅳ', 'Ⅴ', 'Ⅵ', 'Ⅶ', 'Ⅷ', 'Ⅸ', 'Ⅹ', 'Ⅺ', 'Ⅻ'];

  // ---------------- NIED 强震モニタ(kmoni) 实时取色 ----------------
  // kmoni 每秒发布一张 352×400 的实时震度 GIF（jma_s），逐台站取色即可得到
  // 每个观测点的实时计测震度——这也是 NIED+S-Net 类软件的测站数据源。
  // 调色板 -3.0~7.0 每 0.1 一色（KyoshinShindoColorMap, by ingen084）。
  const KMONI_PAL = [[0,0,205],[0,7,209],[0,14,214],[0,21,218],[0,28,223],[0,36,227],[0,43,231],[0,50,236],[0,57,240],[0,64,245],[0,72,250],[0,85,238],[0,99,227],[0,112,216],[0,126,205],[0,140,194],[0,153,183],[0,167,172],[0,180,161],[0,194,150],[0,208,139],[6,212,130],[12,216,121],[18,220,113],[25,224,104],[31,228,96],[37,233,88],[44,237,79],[50,241,71],[56,245,62],[63,250,54],[75,250,49],[88,250,45],[100,251,41],[113,251,37],[125,252,33],[138,252,28],[151,253,24],[163,253,20],[176,254,16],[189,255,12],[195,254,10],[202,254,9],[208,254,8],[215,254,7],[222,255,5],[228,254,4],[235,255,3],[241,254,2],[248,255,1],[255,255,0],[254,251,0],[254,248,0],[254,244,0],[254,241,0],[255,238,0],[254,234,0],[255,231,0],[254,227,0],[255,224,0],[255,221,0],[254,213,0],[254,205,0],[254,197,0],[254,190,0],[255,182,0],[254,174,0],[255,167,0],[254,159,0],[255,151,0],[255,144,0],[254,136,0],[254,128,0],[254,121,0],[254,113,0],[255,106,0],[254,98,0],[255,90,0],[254,83,0],[255,75,0],[255,68,0],[254,61,0],[253,54,0],[252,47,0],[251,40,0],[250,33,0],[249,27,0],[248,20,0],[247,13,0],[246,6,0],[245,0,0],[238,0,0],[230,0,0],[223,0,0],[215,0,0],[208,0,0],[200,0,0],[192,0,0],[185,0,0],[177,0,0],[170,0,0]];
  const KMONI_MAP = new Map();     // RGB → 计测震度
  for (let i = 0; i < KMONI_PAL.length; i++) KMONI_MAP.set(KMONI_PAL[i][0] * 65536 + KMONI_PAL[i][1] * 256 + KMONI_PAL[i][2], i * 0.1 - 3);
  function kmoniValOf(r, g, b) {   // 精确匹配为主，容一点抗锯齿/GIF 抖动边
    let v = KMONI_MAP.get(r * 65536 + g * 256 + b);
    if (v !== undefined) return v;
    if (r + g + b < 30) return null;          // 全黑 = 该点无观测
    let best = null, bd = 40;                 // 距离²阈值，够小才认
    for (let i = 0; i < KMONI_PAL.length; i++) {
      const p = KMONI_PAL[i];
      const d = (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2;
      if (d < bd) { bd = d; best = i * 0.1 - 3; }
    }
    return best;
  }
  function kmoniFresh() { return state.kmoni.ok && nowSec() - state.kmoni.at < 10; }

  // ---- kmoni 图幅投影（实测标定，不是猜的）----
  // 拿 1749 个 NIED 台站的真经纬度去对 jma_s 图上那一堆测站色块做配准，
  // 距离变换精拟合后 96.6% 的台站正好落在像素上、98.3% 落在 2 px 内：
  //   x = 20.25 * lon - 2603.611      y = -24.50 * lat + 1133.952
  // 也就是 352×400 的图覆盖 经度 128.57~145.95、纬度 29.96~46.28
  // （九州—北海道；冲绳/先岛在幅外，会如实显示「图幅外」）。
  const KM_SX = 20.25, KM_BX = -2603.611;
  const KM_SY = -24.5, KM_BY = 1133.952;
  const KM_RING = [[0, 0], [1, 0], [-1, 0], [0, 1], [0, -1], [1, 1], [1, -1], [-1, 1], [-1, -1],
    [2, 0], [-2, 0], [0, 2], [0, -2], [2, 1], [2, -1], [-2, 1], [-2, -1], [1, 2], [-1, 2], [1, -2], [-1, -2]];
  function kmPixel(lon, lat) {
    return [Math.round(KM_SX * lon + KM_BX), Math.round(KM_SY * lat + KM_BY)];
  }
  function kmRiskOf(v) {           // 计测震度 → 我们的标准档位
    return v < 0.5 ? 0 : v < 1.5 ? 1 : v < 2.5 ? 2 : v < 3.5 ? 3 : v < 4.5 ? 4
      : v < 5.0 ? 5 : v < 5.5 ? 6 : v < 6.0 ? 7 : v < 6.5 ? 8 : 9;
  }

  // GQ 全球测站用的色阶：比 kmRiskOf 松一档，跟直觉色序对齐——
  // 1.x 就亮蓝点（有动静就看得见）、2.0 起转绿、3.0 黄、4.0 橙、5.5 以上红、
  // 6.5 顶到紫。原来的档位要 2.5 才绿、3.5 才黄，日本那些小振幅站永远一片蓝灰。
  function gqRiskOf(v) {
    return v < 1.5 ? 1 : v < 2.0 ? 2 : v < 3.0 ? 3 : v < 4.0 ? 4
      : v < 5.0 ? 5 : v < 5.5 ? 6 : v < 6.0 ? 7 : v < 6.5 ? 8 : 9;
  }

  // ---------------- 地球物理 ----------------
  function haversine(lat1, lon1, lat2, lon2) {
    const p1 = lat1 * D2R, p2 = lat2 * D2R;
    const dp = (lat2 - lat1) * D2R, dl = (lon2 - lon1) * D2R;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(a)));
  }
  function hypoDist(ev, lat, lon) {
    return Math.hypot(haversine(ev.lat, ev.lon, lat, lon), ev.depth || 10);
  }
  function pgv(mag, depth, R) {
    const r = Math.max(R, 3);
    // 旧気象庁式给的是 Vs600 基盘上的 PGV600，直接当代入计测震度式会低约一档
    // （熊本 M7.3 实测 7、3.11 宮城海岸实测 6強，原式都只到 5強）。
    // 补场地放大 1.5× 和 M>6.5 的长周期外扩项（M7.3 封顶）。server.py 同款。
    const ml = Math.pow(10, 0.35 * Math.min(Math.max(mag - 6.5, 0), 0.8));
    return 1.5 * ml * Math.pow(10, 0.58 * mag + 0.0038 * depth - 1.29 - Math.log10(r) - 0.002 * r);
  }
  // 斜距：旧幂律式的自变量（只有 AK135 表不可用时才用）
  function slantDist(delta, depth) {
    return Math.hypot(Math.max(0, delta), Math.max(0, depth || 0));
  }
  function ttP(delta, depth, model) {   // delta 是地表水平距离 km → 秒
    const v = ttInterp(model || 'ak135', 'P', delta, depth);
    return v == null ? TT_AP * Math.pow(slantDist(delta, depth), TT_EXP) : v;
  }
  function ttS(delta, depth, model) {
    const v = ttInterp(model || 'ak135', 'S', delta, depth);
    return v == null ? TT_AS * Math.pow(slantDist(delta, depth), TT_EXP) : v;
  }
  // 反演：已知走了 t 秒 → 地表水平半径（km）。表值直接给距离，深源时
  // 前几秒波还在地下，距离为 0，半径自然从 0 开始长，不需要再减 h。
  function distOfT(t, depth, phase, a, model) {
    if (!(t > 0)) return 0;
    const km = ttInvert(model || 'ak135', phase, t, depth);
    if (km != null) return km;
    const D = Math.pow(t / a, 1 / TT_EXP);
    const h = Math.max(0, depth || 0);
    return Math.sqrt(Math.max(0, D * D - h * h));
  }
  function distP(t, depth, model) { return distOfT(t, depth, 'P', TT_AP, model); }
  function distS(t, depth, model) { return distOfT(t, depth, 'S', TT_AS, model); }

  function travelInfo(delta, depth, model) {
    const d = Math.max(0, delta), h = Math.max(0, depth || 0);
    const m = model || 'ak135';
    const tp = ttP(d, h, m), ts = ttS(d, h, m);
    const br = ttTabOf(m) ? m : 'fit';
    return {
      delta: d, depth: h, model: br,
      p: { t: tp, direct: tp, head: NaN, branch: br },
      s: { t: ts, direct: ts, head: NaN, branch: br },
    };
  }

  function shindoVal(p) { return 2.68 + 1.72 * Math.log10(Math.max(p, 0.01)); }

  // 台站「亮起来」的门槛（计测震度）。-2.2 在 kmoni 的 -3.0~7.0 标尺上属于
  // 极轻的微动，比这更弱就直接当平静。改这一个数，est / kmoni / 图例统计三处同步。
  const JP_LIGHT = -2.2;
  // 余晃的最长寿命：过了这个时长（从 S 波到达算）一律归灰。
  // 阈值压到 -2.2 之后，包络虽然还在指数衰减，但要十几分钟才跌穿门槛，
  // 远场那些台站会一直挂着淡蓝点，看着像「震完还到处在摇」。
  const JP_TAIL_MAX = 420;
  function gradeOf(v) {
    if (v < 0.5) return { code: '0', risk: 0, val: 0 };
    if (v < 1.5) return { code: '1', risk: 1, val: 1 };
    if (v < 2.5) return { code: '2', risk: 2, val: 2 };
    if (v < 3.5) return { code: '3', risk: 3, val: 3 };
    if (v < 4.5) return { code: '4', risk: 4, val: 4 };
    if (v < 5.0) return { code: '5弱', risk: 5, val: 4.5 };
    if (v < 5.5) return { code: '5強', risk: 6, val: 5 };
    if (v < 6.0) return { code: '6弱', risk: 7, val: 5.5 };
    if (v < 6.5) return { code: '6強', risk: 8, val: 6 };
    return { code: '7', risk: 9, val: 6.5 };
  }
  function duOf(p) {
    const v = p / 100;
    if (v < 0.005) return 1;
    return clamp(6 + Math.log2(v / 0.06), 1, 12);
  }
  function riskOfDu(du) {
    if (du < 2.5) return 0; if (du < 3.5) return 1; if (du < 4.5) return 2;
    if (du < 5.5) return 3; if (du < 6.5) return 5; if (du < 7.5) return 6;
    if (du < 8.5) return 7; if (du < 9.5) return 8; return 9;
  }
  const usesShindo = ev => !!ev && (ev.country === 'JP' || ev.country === 'TW' || ev.country === 'OT');

  // 震度文字档（'5弱' 等）→ 色带档位
  const LAB_RISK = { '0': 0, '1': 1, '2': 2, '3': 3, '4': 4,
    '5弱': 5, '5強': 6, '6弱': 7, '6強': 8, '7': 9 };
  function riskOfLabel(ev, lab) {
    lab = String(lab == null ? '' : lab).trim();
    if (usesShindo(ev)) return LAB_RISK[lab] === undefined ? 0 : LAB_RISK[lab];
    const du = parseFloat(lab);
    return isFinite(du) ? riskOfDu(du) : 0;
  }

  // ---------------- 「真相源」与「报告值」分离 ----------------
  // 演练里服务端同时给两套东西：
  //   · 估算解（lat/lon/depth/mag）—— 这是系统「报出来」的，卡片和震中标靶用它；
  //   · 真值（sim_true + sim_tt_scale）—— 这是地底下实际发生的，只拿来画
  //     P/S 波前、给台站上色、算震度场。
  // 分开的理由：震源估偏 100km 的时候，波前圈和台站颜色要是也跟着偏，
  // 就会出现「某站明明在晃动、圈还没扫到它」这种自相矛盾的画面。
  // 所以：图上「发生了什么」用真值，「系统以为发生了什么」用估算值。
  let srcKey = '', srcCache = null;
  function srcOf(ev) {
    if (!ev || !ev.isSim || !ev.sim_true) return ev;
    const k = String(ev.uid) + '#' + (ev.serial || 0);
    if (k !== srcKey) {
      srcKey = k;
      srcCache = Object.assign({}, ev, {
        lat: ev.sim_true.lat, lon: ev.sim_true.lon,
        depth: ev.sim_true.depth, mag: ev.sim_true.mag,
      });
    }
    return srcCache;
  }

  function estimate(ev, lat, lon) {
    const depth = ev.depth || 10;
    const epi = haversine(ev.lat, ev.lon, lat, lon);
    const R = Math.hypot(epi, depth);
    const pg = pgv(ev.mag || 0, depth, R);
    const ti = travelInfo(epi, depth, ttModelFor(ev));
    if (usesShindo(ev)) {
      const g = gradeOf(shindoVal(pg));
      return { R, pgv: pg, label: g.code, risk: g.risk, val: g.val, ts: ti.s.t, tp: ti.p.t, ti };
    }
    const du = duOf(pg);
    return {
      R, pgv: pg, du, val: du, label: DU_ROMAN[Math.round(clamp(du, 1, 12))] + '度',
      risk: riskOfDu(du), ts: ti.s.t, tp: ti.p.t, ti
    };
  }

  let estKey = '', estCache = new Map();
  function estOf(ev, st) {
    const k = ev.uid + '#' + (ev.serial || 0);
    if (k !== estKey) { estKey = k; estCache.clear(); }
    let v = estCache.get(st.code);
    // 台站震度按真值算：估算震源只决定「卡片上写哪里、多深、多大」，
    // 不能反过来决定某个站摇成什么样——那是观测，不是预报。
    if (!v) { v = estimate(srcOf(ev), st.lat, st.lon); estCache.set(st.code, v); }
    return v;
  }

  const nowSec = () => Date.now() / 1000 + state.offset;
  function fmtHM(ts, tzMin) {
    if (!ts) return '--:--';
    const d = new Date((ts + (tzMin || 0)) * 1000);
    const p = n => String(n).padStart(2, '0');
    return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
  }
  function fmtShort(ts, tzMin) {
    if (!ts) return '--';
    const d = new Date((ts + (tzMin || 0)) * 1000);
    const p = n => String(n).padStart(2, '0');
    return p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes());
  }
  function fmtClock(ts, tzMin) {
    if (!ts) return '--:--:--';
    const d = new Date((ts + (tzMin || 0)) * 1000);
    const p = n => String(n).padStart(2, '0');
    return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
  }
  const tzOf = ev => ev && ev.country === 'JP' ? 9 * 3600 : 8 * 3600;
  const fmtDur = v => v >= 60
    ? Math.floor(v / 60) + ':' + String(Math.round(v % 60)).padStart(2, '0')
    : Math.round(v) + 's';

  // ---------------- 事件 ----------------
  // 演练是否还在「进行中」：只看服务端给的最后一次更新时刻（report_ts），
  // 不再用本地 simEnd 计时器。本地计时器一旦残留过期值，新的演练就会被判成
  // 「已结束」而不显示（这正是「模拟一次后不能再模拟」的根因）。
  const SIM_LIVE = 240;
  // 演练终报后过了多少「虚拟秒」。演练的点、推定靶标都按它退场——
  // 不然收卡之后彩点还挂满 240 秒，看着就像「地震完了怎么还有黄点在闪」。
  function simFinalAge(ev) {
    if (!ev || !ev.isSim || !ev.final) return 0;
    const sp = ev.speed > 0 ? ev.speed : 1;
    return Math.max(0, (nowSec() - (ev.report_ts || nowSec())) * sp);
  }
  function liveSim() {
    const s = state.sim;
    if (!s) return null;
    // pending = 演练刚点下去、还没有任何台站报到。此时不给主卡、不给镜头，
    // 只在右上「监听中」那行提示「台站检测中」——真实的 EEW 也不是地震一发生
    // 就有解，得先有几个台站摇起来才算得出震源。
    if (s.pending) return null;
    const at = s.report_ts || s.origin_ts || 0;
    if (!at) return s;
    return nowSec() - at < SIM_LIVE ? s : null;
  }
  // 同时进行的所有地震（多事件适配）：服务端 eew_list 优先，兼容旧的 eew.{CN,JP,GQ}
  function activeEvents() {
    const eew = state.eew || {};
    let list = (state.eewList || []).filter(e => e && e.active && e.lat && e.lon);
    if (!list.length) list = [eew.CN, eew.JP, eew.GQ].filter(e => e && e.active);
    const now = nowSec();
    const seen = new Set(), out = [];
    for (const e of list) {
      if (!e.uid || seen.has(e.uid)) continue;
      seen.add(e.uid);
      out.push(e);
    }
    // 「强且新」优先：每过 2 分钟相当于让 1 个震级，避免一堆 M1 抢走主卡
    out.sort((a, b) => prioEvent(b, now) - prioEvent(a, now));
    return out;
  }
  // 本区域加权：这软件的定位是「中日地震预警」，全球台网只是补充。
  // 不加权的话，阿留申 M4.4 会把岩手 M3.2 挤下主卡，并把镜头拽到白令海——
  // 用户盯着日本看，屏幕却每隔几分钟飞到阿拉斯加。
  const PRIO_HOME = 2.5;
  function prioEvent(e, now) {
    const home = (e.auto || e.country === 'JP' || e.country === 'CN' || e.country === 'TW') ? PRIO_HOME : 0;
    return (e.mag || 0) + home - Math.max(0, now - (e.origin_ts || now)) / 120;
  }
  // 自动选中的那起（忽略用户手动切换）——警报/播报只认它，避免切换卡片后漏报新地震
  function primaryEvent() {
    const sim = liveSim();
    if (sim) return Object.assign({}, sim, { active: true, isSim: true });
    const c = activeEvents().filter(headlineWorthy);
    return c.length ? c[0] : null;
  }
  // 一条事件「够不够格」占据主卡、把镜头拽过去。
  // 全球台网一天几十上百个 M1~M2 的自动检测，让它们轮流当头条的后果很具体：
  // 日本真在摇的时候，镜头还停在美国得克萨斯，台站颜色当然一个也看不见。
  // 所以分级：演习/推算/带警报的速报一律优先；中日区域 M3 起；全球台网 M4 起。
  function headlineWorthy(ev) {
    if (!ev) return false;
    if (ev.isSim || ev.auto || ev.warn) return true;
    if (ev.country === 'CN' || ev.country === 'JP' || ev.country === 'TW') return (ev.mag || 0) >= 3.0;
    // 全球台网：门槛改由「全球检测弹窗震级」决定（默认 4.5）。
    // 原来写死 5.0，GQ 推来的 M4~4.9 既不弹卡也不响铃，看起来就是「GQ 从来没预警」。
    return (ev.mag || 0) >= Number(state.settings.gqAlert ?? 4.5);
  }

  function currentEvent() {
    const sim = liveSim();
    if (sim) return Object.assign({}, sim, { active: true, isSim: true });
    const all = activeEvents();
    const cands = all.filter(headlineWorthy);
    // 本地推算参与排序，而不是「实在没东西才轮到它」的备胎：
    // 日本那种 JMA 不发的弱震，全场只有它在报，垫底就等于永远看不见。
    const a = state.autoEv;
    const autoFresh = a && nowSec() - (a.report_ts || 0) < AUTO.holdSec;
    // 但日本有真实速报在报时，推算必须让位——它是补空白的，不能盖过气象厅
    const realJp = all.some(e => e.country === 'JP' &&
      nowSec() - (e.report_ts || e.origin_ts || 0) < 180);
    if (autoFresh && !realJp) cands.push(Object.assign({ active: true }, a));
    // 手动从列表里点的小震也算数（那是人自己选的，不该被「不够格」挡掉）
    if (state.focusUid) {
      const f = all.find(e => e.uid === state.focusUid) ||
        (autoFresh && a.uid === state.focusUid ? a : null);
      if (f) return f;
      state.focusUid = null;          // 手动选中那起已经结束，回到自动
    }
    if (!cands.length) return null;
    const now = nowSec();
    cands.sort((x, y) => prioEvent(y, now) - prioEvent(x, now));
    return cands[0];
  }
  // 终报之后事件卡就该收了——「预警结束 → 震度速報」是先后关系，不是同时。
  // 留 HERO_HOLD 秒是为了让人看清「第 7 报 / 終報」那一行，不然最后一报一闪就没。
  // 注意：收的是卡片，地图上的震度场/海啸继续留着，那才是后面该看的东西。
  const HERO_HOLD = 6;
  function sinceFinal(ev) {
    return ev.isSim
      ? Math.max(0, elapsedOf(ev) - (ev.sim_t || 0))
      : Math.max(0, nowSec() - (ev.report_ts || 0));
  }
  function displayEvent() {
    if (state.historyEvent) return Object.assign({}, state.historyEvent, { isHist: true, active: false });
    const ev = currentEvent();
    if (!ev) return null;
    if (ev.final && sinceFinal(ev) >= HERO_HOLD) return null;
    if (state.dismissedKey === ev.uid + '#' + (ev.serial || 0) && nowSec() < state.hiddenUntil) return null;
    return ev;
  }
  function eventForMap() {
    if (state.historyEvent) return state.historyEvent;
    const ev = currentEvent();
    if (ev) return ev;
    const past = (state.events || []).filter(e => e.lat && e.lon);
    return past.length ? past[0] : null;
  }
  function elapsedOf(ev) {
    if (state.replayAt) return clamp(nowSec() - state.replayAt, 0, 180);
    if (state.histReplayAt && ev && state.historyEvent && ev.uid === state.historyEvent.uid) {
      return clamp(nowSec() - state.histReplayAt, 0, 300);
    }
    // 演练：时间轴是服务端给的「虚拟秒」。每报都带 sim_t 和当时的倍速，
    // 所以这里按 report_ts 往后按倍速外推——3× 时地震波、倒计时、
    // 海啸波前全都真的快 3 倍，而不是只有电文变快、动画还慢吞吞。
    const s = state.sim;
    if (ev && s && s.uid === ev.uid && typeof s.sim_t === 'number') {
      const sp = s.speed > 0 ? s.speed : 1;
      return Math.max(0, s.sim_t + (nowSec() - (s.report_ts || nowSec())) * sp);
    }
    return Math.max(0, nowSec() - (ev.origin_ts || nowSec()));
  }

  // ---------------- 地图 ----------------
  const canvas = $('#map');
  const ctx = canvas.getContext('2d');
  const baseCanvas = document.createElement('canvas');
  const fieldCanvas = document.createElement('canvas');
  const view = { w: 0, h: 0, s: 1, tx: 0, ty: 0, fit: 1, dpr: 1 };
  state.gqSt = [];          // GQ 全球测站 [index, lat, lon, net, code, loc]
  state.gqIdx = new Map();  // index → 测站（GQ 的编号是稀疏的，不能拿数组下标顶）
  state.gqAct = [];         // 活跃强度 [index, 值]
  const WORLD = { lon0: -180, lon1: 180, lat0: -62, lat1: 74 };
  const HOME = { lon0: 70, lon1: 151, lat0: 11, lat1: 55 };
  const merc = lat => Math.log(Math.tan(Math.PI / 4 + clamp(lat, -85, 85) * D2R / 2));
  const WORLD_W = (WORLD.lon1 - WORLD.lon0) * D2R;
  const WORLD_H = merc(WORLD.lat1) - merc(WORLD.lat0);
  const HOME_W = (HOME.lon1 - HOME.lon0) * D2R;
  const HOME_H = merc(HOME.lat1) - merc(HOME.lat0);
  const HOME_MID = { lon: (HOME.lon0 + HOME.lon1) / 2, lat: (HOME.lat0 + HOME.lat1) / 2 };

  let baseDirty = true, fieldDirty = true, fieldKey = '';

  const px = lon => (lon * D2R - WORLD.lon0 * D2R) * view.s + view.tx;
  const py = lat => (merc(WORLD.lat1) - merc(lat)) * view.s + view.ty;
  const lonAt = x => ((x - view.tx) / view.s + WORLD.lon0 * D2R) / D2R;
  const latAt = y => (Math.PI / 2 - 2 * Math.atan(Math.exp((y - view.ty) / view.s - merc(WORLD.lat1)))) / D2R;
  const kmPx = lat => view.s / (R_EARTH * Math.cos(clamp(lat, -80, 80) * D2R));
  // 一个「世界」的像素宽度：地图左右无缝循环，相邻副本正好差这么宽
  const worldPx = () => WORLD_W * view.s;
  // 经度归一到 [-180, 180)：显示坐标/点选坐标用（内部 px 允许任意经度）
  const normLon = lon => { let x = lon % 360; if (x > 180) x -= 360; if (x < -180) x += 360; return x; };
  const lonLabel = lon => Math.abs(lon) + '°' + (lon < 0 ? 'W' : 'E');
  const latLabel = lat => Math.abs(lat) + '°' + (lat < 0 ? 'S' : 'N');

  function clampView() {
    const wp = worldPx(), wH = WORLD_H * view.s;
    // 垂直照旧夹住；水平方向不夹——左右是同一张地图的接续，tx 只归一到「一个世界宽」内
    view.ty = wH <= view.h ? (view.h - wH) / 2 : clamp(view.ty, view.h - wH, 0);
    if (wp > 0 && isFinite(wp)) view.tx = ((view.tx % wp) + wp) % wp - wp;   // → [-wp, 0)
  }
  // 需要画几份才能铺满屏幕（最少 1 份；最小缩放时 2~3 份）
  function copiesNeeded() {
    const wp = worldPx();
    if (!(wp > 0)) return 1;
    return Math.max(1, Math.min(4, Math.ceil((view.w - view.tx) / wp)));
  }
  // 环的包围盒缓存（大地图几十万点，靠它跳过屏幕外的环，拖动/缩放才够丝滑）
  function ringBB(r) {
    let b = r._bb;
    if (!b) {
      let x0 = 1e9, x1 = -1e9, y0 = 1e9, y1 = -1e9;
      for (let i = 0; i < r.length; i++) {
        const p = r[i];
        if (p[0] < x0) x0 = p[0];
        if (p[0] > x1) x1 = p[0];
        if (p[1] < y0) y0 = p[1];
        if (p[1] > y1) y1 = p[1];
      }
      b = r._bb = [x0, x1, y0, y1];
    }
    return b;
  }
  // 缩放基准改为「全球铺满」：否则全球视野的比例尺超出下限，滚轮一缩就弹回东亚
  function fitScale() { return Math.min(view.w / WORLD_W, view.h / WORLD_H) * 0.99; }
  function homeScale() { return Math.min(view.w / HOME_W, view.h / HOME_H) * 0.99; }
  function centerOn(lat, lon) {
    view.tx = view.w / 2 - (lon - WORLD.lon0) * D2R * view.s;
    view.ty = view.h / 2 - (merc(WORLD.lat1) - merc(lat)) * view.s;
    clampView();
  }
  function fitView() {
    view.fit = fitScale();
    view.s = homeScale();
    centerOn(HOME_MID.lat, HOME_MID.lon);
    baseDirty = fieldDirty = true;
  }
  // 全球视野：整个世界铺满画布
  function fitWorld() {
    view.s = view.fit;
    centerOn(20, 0);
    baseDirty = fieldDirty = true;
  }
  function resize() {
    const rect = canvas.parentElement.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    const had = view.w > 0 && view.fit > 0;
    const cLat = had ? latAt(view.h / 2) : 0;
    const cLon = had ? lonAt(view.w / 2) : 0;
    const ratio = had ? view.s / view.fit : 1;
    view.w = Math.max(320, Math.round(rect.width));
    view.h = Math.max(240, Math.round(rect.height));
    view.dpr = dpr;
    canvas.width = Math.round(view.w * dpr);
    canvas.height = Math.round(view.h * dpr);
    canvas.style.width = view.w + 'px';
    canvas.style.height = view.h + 'px';
    for (const c of [baseCanvas, fieldCanvas]) { c.width = canvas.width; c.height = canvas.height; }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    view.fit = fitScale();
    if (!had) fitView();
    else {
      view.s = clamp(view.fit * ratio, view.fit * 0.9, view.fit * 80);
      centerOn(cLat, cLon);
      baseDirty = fieldDirty = true;
    }
  }

  // 底图配色：深色 / 浅色两套。浅色模式下海是浅灰蓝、陆是近白，
  // 经纬网、海岸线、国界都换成深一点的灰蓝，免得整张图糊成一片白。
  const MPAL = {
    dark: {
      sea: ['#081320', '#060d16', '#04090f'],
      grid: 'rgba(120,180,225,.075)', gridTxt: 'rgba(120,150,190,.28)',
      land: '#152230', land2: '#18293a',
      coast: 'rgba(96,142,188,.55)', coast2: 'rgba(104,152,200,.6)',
      border: 'rgba(158,198,236,.62)', scs: 'rgba(140,178,214,.55)',
      pref: 'rgba(158,203,240,', gq: 'rgba(122,168,210,.30)',
      chip: 'rgba(6,10,16,.82)', knock: 'rgba(4,8,13,.45)',
      tagBg: 'rgba(8,13,20,.86)', tagFg: '#ffd7cf',
    },
    light: {
      sea: ['#eef4fa', '#e6eef7', '#dfe9f3'],
      grid: 'rgba(70,110,155,.13)', gridTxt: 'rgba(60,90,125,.55)',
      land: '#fcfdfe', land2: '#f8fafc',
      coast: 'rgba(120,150,180,.62)', coast2: 'rgba(110,142,175,.66)',
      border: 'rgba(60,100,150,.60)', scs: 'rgba(90,120,160,.55)',
      pref: 'rgba(90,125,165,', gq: 'rgba(90,120,155,.32)',
      chip: 'rgba(255,255,255,.90)', knock: 'rgba(255,255,255,.55)',
      tagBg: 'rgba(255,255,255,.94)', tagFg: '#8a2f22',
    },
  };
  let PAL = MPAL.dark;
  function syncPalette() {
    PAL = document.documentElement.classList.contains('light') ? MPAL.light : MPAL.dark;
    baseDirty = fieldDirty = true;
  }

  function drawBase() {
    const g = baseCanvas.getContext('2d');
    g.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
    g.clearRect(0, 0, view.w, view.h);

    const grad = g.createLinearGradient(0, 0, 0, view.h);
    grad.addColorStop(0, PAL.sea[0]);
    grad.addColorStop(.6, PAL.sea[1]);
    grad.addColorStop(1, PAL.sea[2]);
    g.fillStyle = grad;
    g.fillRect(0, 0, view.w, view.h);

    // 网格：经度按可视范围动态生成（地图左右无缝，网格要跟着绕圈）
    g.strokeStyle = PAL.grid;
    g.lineWidth = 1;
    g.beginPath();
    const lonA = lonAt(0), lonB = lonAt(view.w);
    for (let lon = Math.ceil(lonA / 20) * 20; lon <= lonB; lon += 20) {
      const x = Math.round(px(lon)) + .5;
      g.moveTo(x, 0); g.lineTo(x, view.h);
    }
    for (let lat = -60; lat <= 70; lat += 20) { const y = Math.round(py(lat)) + .5; g.moveTo(0, y); g.lineTo(view.w, y); }
    g.stroke();

    g.font = '9px ui-monospace,monospace';
    g.fillStyle = PAL.gridTxt;
    for (let lon = Math.ceil(lonA / 20) * 20; lon <= lonB; lon += 20) {
      const x = px(lon) + 3;
      if (x < 40 || x > view.w - 60) continue;
      g.fillText(lonLabel(normLon(lon)), x, 12);
    }
    for (let lat = -60; lat <= 70; lat += 20) {
      const y = py(lat) - 3;
      if (y < 60 || y > view.h - 30) continue;
      g.fillText(latLabel(lat), 4, y);
    }

    // 地图主体：左右无缝循环——同一套数据按「一个世界宽」平移画多份，
    // 拖过东西边缘时邻份正好接上（地球是圆的，不该有硬边）。
    // 每份内部再按环包围盒做屏幕剔除（剔除把份偏移算进去），几十万点也不卡。
    const paintMap = (dx) => {
      const x0 = -48, x1 = view.w + 48;      // 屏幕外留一点余量给描边

      // 陆地层：物理陆地（NE 50m 全球打底 + NE 10m 东亚精细）。逐环独立填充，
      // 避免 evenodd 把相邻/重叠环抵消成"海"
      const zRel = view.s / (view.fit || 1);
      // 缩小后点间距远小于 1px，隔点取样即可（懒缓存），几十万点也拖得动
      const step = zRel < 1.1 ? 3 : zRel < 2.2 ? 2 : 1;
      const ringAt = (ring, s) => {
        if (s <= 1) return ring;
        const k = '_d' + s;
        let r = ring[k];
        if (!r) {
          r = [];
          for (let i = 0; i < ring.length; i += s) r.push(ring[i]);
          if (r[r.length - 1] !== ring[ring.length - 1]) r.push(ring[ring.length - 1]);
          ring[k] = r;
        }
        return r;
      };
      const drawLand = (rings, fill, stroke, lw) => {
        if (!rings) return;
        for (const ring0 of rings) {
          const bb = ringBB(ring0);
          const sx0 = px(bb[0]) + dx, sx1 = px(bb[1]) + dx;
          if (sx1 < x0 || sx0 > x1) continue;
          const sy0 = py(bb[3]), sy1 = py(bb[2]);
          if (sy1 < -60 || sy0 > view.h + 60) continue;
          const ring = ringAt(ring0, step);
          g.beginPath();
          for (let i = 0; i < ring.length; i++) {
            const x = px(ring[i][0]) + dx, y = py(ring[i][1]);
            i ? g.lineTo(x, y) : g.moveTo(x, y);
          }
          g.closePath();
          // 先用填充色描一圈粗边堵住环间细缝，再描海岸线，最后填充
          g.strokeStyle = fill;
          g.lineWidth = lw * 3;
          g.lineJoin = 'round';
          g.stroke();
          if (stroke) {
            g.strokeStyle = stroke;
            g.lineWidth = lw;
            g.stroke();
          }
          g.fillStyle = fill;
          g.fill('nonzero');
        }
      };
      if (window.GEOW) drawLand(window.GEOW.land, PAL.land, PAL.coast, 0.7);
      // 东亚精细层：全球视野下和 50m 层几乎重合，缩得太小就省掉（省一半绘制量）
      if (window.GEO && window.GEO.land && zRel > 1.4)
        drawLand(window.GEO.land, PAL.land2, PAL.coast2, 0.75);
      // DataV 中国岛礁（海南/台湾/港澳/南海诸岛/钓鱼岛等，物理陆地缺的小环）
      if (window.GEO && window.GEO.cnisl && window.GEO.cnisl.length)
        drawLand(window.GEO.cnisl, PAL.land2, PAL.coast2, 0.75);

      // 中国国界线（DataV 标准数据：官方藏南边界，只描线不填充）
      if (window.GEO && window.GEO.cn) {
        const cn = window.GEO.cn, bb = ringBB(cn);
        if (!(px(bb[1]) + dx < x0 || px(bb[0]) + dx > x1)) {
          g.strokeStyle = PAL.border;
          g.lineWidth = 1;
          g.lineJoin = 'round';
          g.beginPath();
          for (let i = 0; i < cn.length; i++) {
            const x = px(cn[i][0]) + dx, y = py(cn[i][1]);
            i ? g.lineTo(x, y) : g.moveTo(x, y);
          }
          g.stroke();
        }
      }

      // 南海九段线（DataV 100000_JD 标准要素）
      if (window.GEO && window.GEO.scs && window.GEO.scs.length) {
        g.strokeStyle = PAL.scs;
        g.lineWidth = 0.9;
        g.setLineDash([5, 4]);
        g.beginPath();
        for (const seg of window.GEO.scs) {
          const bb = ringBB(seg);
          if (px(bb[1]) + dx < x0 || px(bb[0]) + dx > x1) continue;
          for (let i = 0; i < seg.length; i++) {
            const x = px(seg[i][0]) + dx, y = py(seg[i][1]);
            i ? g.lineTo(x, y) : g.moveTo(x, y);
          }
        }
        g.stroke();
        g.setLineDash([]);
      }

      // 日本都道府县界线：放大后逐渐显形
      if (window.GEOJP) {
        const z = view.s / (homeScale() || 1);
        const a = clamp(0.10 + Math.max(0, z - 1) * 0.16, 0.10, 0.46);
        g.strokeStyle = PAL.pref + a.toFixed(3) + ')';
        g.lineWidth = z > 2.4 ? 1 : 0.7;
        g.beginPath();
        for (const p of window.GEOJP.prefs) tracePref(g, p, dx);
        g.stroke();
      }

      // GlobalQuake 全球测站：静态灰点画进底层（拖动/缩放时才重绘，几万个点也不卡）
      if (state.layers.gqst && state.gqSt.length) {
        g.fillStyle = PAL.gq;
        const r = view.s / homeScale() > 2.5 ? 2 : 1.4;
        for (const s of state.gqSt) {
          const x = px(s[2]) + dx, y = py(s[1]);
          if (x < -4 || x > view.w + 4 || y < -4 || y > view.h + 4) continue;
          g.fillRect(x - r / 2, y - r / 2, r, r);
        }
      }
    };

    const n = copiesNeeded();
    const wp = worldPx();
    for (let k = 0; k < n; k++) paintMap(k * wp);
    baseDirty = false;
  }

  function drawField(ev) {
    const g = fieldCanvas.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.clearRect(0, 0, fieldCanvas.width, fieldCanvas.height);
    if (!state.layers.field) { fieldDirty = false; return; }
    // 速報到得晚（終報后约 2 分钟），那时事件多半已经收场——
    // 只要还有新鲜的震度速報（15 分钟内），县块就继续铺着
    if (!ev && !obsBlocks()) { fieldDirty = false; return; }
    g.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);

    // 气象厅给了予想震度地域，就直接照它的区块画，不再自己算等值面。
    // 没有预警但有震度速報时，用速报的都道府县震度画块——速报本身就是
    // 「地域」级的东西，落到地图上就应该是块。
    const ob = state.layers.pref === 0 ? null : obsBlocks();
    let blocks = state.layers.pref === 0 ? null : prefBlocks(ev);
    // 震度速報 是「摇完实测」的地域级原文，比 EEW 的予想、更比本机推算准：
    // 同一场地震时压过那些块；推算出来的块（__est）也一律让位。
    if (ob && (!ev || !blocks || blocks.__est || sameQuake(ev))) blocks = ob;
    if (blocks) {
      const idx = prefIndex();
      for (const name of Object.keys(blocks)) {
        const pref = idx[name];
        if (!pref) continue;
        const b = blocks[name];
        const risk = b.risk != null ? b.risk : gradeOf(b.value).risk;
        if (risk < 1) continue;
        g.beginPath();
        tracePref(g, pref);
        g.fillStyle = PREF_FILL[risk];
        g.fill('evenodd');
      }
      fieldDirty = false;
      return;
    }

    // 色块范围跟着震级走，不再一刀切 900 km——大震的有感范围远超这个数，
    // 之前中国地震的色块就是被这圈硬边「截」住的。边缘再按震度值淡出：
    // 淡出带必须对齐「risk≥1」的显示门槛（日本 震度1 / 中国 Ⅱ度），
    // 否则门槛直接 continue 掉，淡出根本轮不到。
    const jp = usesShindo(ev);
    // 演练：热力场按真值画。这层颜色是「哪里真的摇到几度」，
    // 跟着估算震源走的话，估算偏 50km 整片颜色就跟着挪，和台站对不上。
    const src = srcOf(ev);
    const dMax = Math.min(2600, 240 + (src.mag || 5) * 300);
    const thr = jp ? 1.5 : 2.5;      // 显示门槛（震度值 / 烈度值）
    const full = jp ? 2.5 : 3.6;     // 达到这个值就不再加透明度
    const step = 6;
    for (let y = 0; y < view.h; y += step) {
      const lat = latAt(y + step / 2);
      if (lat < WORLD.lat0 || lat > WORLD.lat1) continue;
      for (let x = 0; x < view.w; x += step) {
        const lon = lonAt(x + step / 2);
        const d = hypoDist(src, lat, lon);
        if (d > dMax) continue;
        const p = pgv(src.mag || 0, src.depth || 10, d);
        let v, risk;
        if (jp) {
          v = shindoVal(p);
          if (v < thr) continue;
          risk = gradeOf(v).risk;
        } else {
          v = duOf(p);
          if (v < thr) continue;
          risk = riskOfDu(v);
        }
        if (risk < 1) continue;
        const fade = clamp((v - thr) / (full - thr), 0, 1);
        const a = RISK_ALPHA[risk] * (0.22 + 0.78 * fade);
        const c = RISK_RGB[risk];
        g.fillStyle = 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a.toFixed(3) + ')';
        g.fillRect(x, y, step + 1, step + 1);
      }
    }
    fieldDirty = false;
  }

  function ring(ev, km, color, width, dash) {
    if (!(km > 0) || !isFinite(km)) return;
    const cx = px(ev.lon), cy = py(ev.lat);
    const r = km * kmPx(ev.lat);
    if (r < 2) return;
    ctx.beginPath();
    ctx.setLineDash(dash || []);
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(4,8,13,.45)';
    ctx.lineWidth = (width || 1.2) + 1.8;
    ctx.stroke();
    ctx.beginPath();
    ctx.setLineDash(dash || []);
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.strokeStyle = color;
    ctx.lineWidth = width || 1.2;
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function ringLabel(ev, km, text, color) {
    const r = km * kmPx(ev.lat);
    if (r < 46) return;
    const x = px(ev.lon) - r, y = py(ev.lat);
    if (x < 46 || y < 70 || y > view.h - 40) return;
    ctx.font = '700 9.5px ui-monospace,monospace';
    const w = ctx.measureText(text).width;
    ctx.fillStyle = 'rgba(5,9,14,.78)';
    roundRect(ctx, x - w / 2 - 5, y - 8, w + 10, 15, 4);
    ctx.fill();
    ctx.fillStyle = color;
    ctx.fillText(text, x - w / 2, y + 3);
  }

  // ---------------- 视野自适应（起手取景 + P 波撞边大步拉远） ----------------
  // 旧做法是每帧缩 0.4%~1.8%，波前贴着边缘一路慢慢蹭，看着很墨迹。
  // 现在改两条：①新事件一出现就先把画面放远一点，留够波前扩散的余量；
  //            ②P 波真撞到屏幕边时，一次性拉远一大截（补间 0.4s 走完），不逐帧微调。
  let lastUserZoomAt = 0;
  let vTween = null;        // { t0, dur, s0, s1, ax, ay, tx0, ty0 }
  let framedUid = '';       // 已经做过「起手取景」的事件

  function stepViewTween() {
    if (!vTween) return;
    const p = clamp((performance.now() - vTween.t0) / vTween.dur, 0, 1);
    const e = 1 - Math.pow(1 - p, 3);          // easeOutCubic：起步快、落点稳，不会拖泥带水
    const s = vTween.s0 + (vTween.s1 - vTween.s0) * e;
    view.s = s;
    if (vTween.mode === 'fly') {
      view.tx = vTween.tx0 + (vTween.tx1 - vTween.tx0) * e;
      view.ty = vTween.ty0 + (vTween.ty1 - vTween.ty0) * e;
    } else {
      const real = s / vTween.s0;
      view.tx = vTween.ax - (vTween.ax - vTween.tx0) * real;   // 锚定震中，画面不跳
      view.ty = vTween.ay - (vTween.ay - vTween.ty0) * real;
    }
    clampView();
    baseDirty = fieldDirty = true;
    if (p >= 1) vTween = null;
  }
  function startViewTween(sTo, ax, ay, dur) {
    if (!(sTo > 0) || !isFinite(sTo)) return;
    vTween = { t0: performance.now(), dur: dur || 420, s0: view.s, s1: sTo,
      ax: ax, ay: ay, tx0: view.tx, ty0: view.ty };
  }
  // 平滑飞到某个经纬度：既缩放又把目标移到屏幕中心（点击地震、绿框放大都走这里）
  function flyTo(lat, lon, sTo, dur) {
    if (!(sTo > 0) || !isFinite(sTo) || lat == null || lon == null) return;
    const tx1 = view.w / 2 - (lon - WORLD.lon0) * D2R * sTo;
    const ty1 = view.h / 2 - (merc(WORLD.lat1) - merc(lat)) * sTo;
    vTween = { t0: performance.now(), dur: dur || 620, mode: 'fly',
      s0: view.s, s1: sTo, tx0: view.tx, ty0: view.ty, tx1: tx1, ty1: ty1 };
  }
  function cancelViewTween() { vTween = null; }
  function canAutoView() {
    return state.settings.autoZoom && !drag && !state.historyEvent &&
      performance.now() - lastUserZoomAt > 3000;
  }
  // P 波 sec 秒后的地表水平半径（km），与波前模型同一套走时公式
  function predictP(ev, sec) {
    return distP(sec, Math.max(0, ev.depth || 10), ttModelFor(ev));
  }
  // 起手取景：新事件刚进来就把视野放到「P 波跑 90 秒也装得下」的尺度（只缩不放）
  function initialFrame(ev) {
    if (!ev || !ev.uid) return;
    if (framedUid === ev.uid) return;
    if (elapsedOf(ev) > 30) { framedUid = ev.uid; return; }    // 早就在响的事件，不折腾
    framedUid = ev.uid;
    if (!canAutoView()) return;
    const cx = px(ev.lon), cy = py(ev.lat);
    if (cx < 40 || cx > view.w - 40 || cy < 40 || cy > view.h - 40) return;  // 震中不在视野里，别乱动
    const edge = Math.min(cx, view.w - cx, cy, view.h - cy);
    const dP = predictP(ev, 90);
    if (!(dP > 1)) return;
    // 让 90 秒后的 P 波半径落在边缘的 42% 左右 → 起手先远一档，之后少折腾
    const sWant = (edge * 0.42) * R_EARTH * Math.cos(clamp(ev.lat, -80, 80) * D2R) / dP;
    const ns = Math.max(view.fit * 0.9, Math.min(view.s, sWant));
    if (ns < view.s * 0.97) startViewTween(ns, cx, cy, 620);
  }
  // P 波撞边：一口气拉远一大截，把波前压回边缘 55%；不做每帧微调
  function autoFrame(rP, cx, cy) {
    if (vTween || !canAutoView()) return;
    if (!(rP > 0) || !isFinite(rP)) return;
    if (cx < 0 || cx > view.w || cy < 0 || cy > view.h) return;       // 震中不在视野里
    const edge = Math.min(cx, view.w - cx, cy, view.h - cy);
    if (edge < 24) return;
    if (rP < edge * 0.88) return;                                     // 还没贴边，什么都不用做
    const minS = view.fit * 0.9;
    if (view.s <= minS * 1.002) return;                               // 已到最小视野
    const ns = Math.max(minS, view.s * (edge * 0.55) / rP);
    if (ns > view.s * 0.96) return;                                   // 缩不动了就别抖
    startViewTween(ns, cx, cy, 420);
  }

  // ---------------- 长周期地震动（气象厅观测） ----------------
  // 高层建筑才有感的慢摇，和震度是两套指标。观测点按階級 1~4 画紫色圈，
  // 只显示最近 6 小时内发布的一次观测，避免旧事件一直挂在图上。
  const LPGM_COLOR = ['', '#c4b5fd', '#a78bfa', '#8b5cf6', '#6d28d9'];
  function lpgmLive() {
    const d = state.lpgm;
    if (!d || !(d.max_class >= 1) || !(d.stations || []).length) return null;
    if (nowSec() - (d.updated || 0) > 6 * 3600) return null;
    return d;
  }
  function drawLpgm() {
    const d = lpgmLive();
    if (!d) return;
    ctx.save();
    for (const s of d.stations) {
      const x = px(s[1]), y = py(s[0]);
      if (x < -8 || y < -8 || x > view.w + 8 || y > view.h + 8) continue;
      const c = Math.max(1, Math.min(4, s[2] | 0));
      const r = 8 + c * 2.2;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.strokeStyle = LPGM_COLOR[c];
      ctx.lineWidth = 1.7;
      ctx.stroke();
      ctx.fillStyle = LPGM_COLOR[c] + '22';
      ctx.fill();
    }
    ctx.restore();
  }
  // 长周期并进「震度速報」卡片：只有和卡上这场地震同一场（发震时刻 30 分钟内）才显示
  function renderObsLpgm() {
    const el = $('#obsLpgm');
    if (!el) return;
    const o = state.obs, d = lpgmLive();
    const same = o && d && Math.abs((o.origin_ts || 0) - (d.origin_ts || 0)) < 1800;
    if (!same) { el.hidden = true; return; }
    el.hidden = false;
    $('#obsLpgmVal').textContent = '階級' + d.max_class;
    $('#obsLpgmPlace').textContent = (d.place || '') + ' · ' + d.stations.length + ' 点';
    el.title = '气象厅 长周期地震动观测 · 最大階級 ' + d.max_class +
      ' · ' + (d.place || '') + ' · ' + d.stations.length + ' 个观测点';
  }

  // ---------------- 气象厅海啸警报（官方报文） ----------------
  // 与下面「按震级推算的传播」区分：这里只显示气象厅真的发出来的警报/注意报。
  const TSU_G = {
    MajorWarning: { label: '大津波警报', cls: 'g-majorwarning' },
    Warning: { label: '津波警报', cls: '' },
    Watch: { label: '津波注意报', cls: 'g-watch' },
  };
  function renderTsuAlert() {
    const el = $('#tsuAlert');
    if (!el) return;
    const t = state.tsunami;
    const live = t && !t.cancelled && (t.areas || []).length && t.issued_ts &&
      nowSec() - t.issued_ts < 6 * 3600;
    if (!live) { el.hidden = true; return; }
    const order = { MajorWarning: 0, Warning: 1, Watch: 2 };
    const areas = t.areas.slice().sort((a, b) =>
      (order[a.grade] == null ? 9 : order[a.grade]) - (order[b.grade] == null ? 9 : order[b.grade]));
    const top = areas[0];
    const g = TSU_G[top.grade] || { label: '津波预报', cls: 'g-watch' };
    el.className = 'tsualert' + (g.cls ? ' ' + g.cls : '');
    $('#tsuAlertTitle').textContent = g.label;
    const one = a => {
      const h = a.max && (a.max.text || (a.max.value != null ? a.max.value + 'm' : ''));
      return a.name + (h ? ' ' + h : '');
    };
    $('#tsuAlertBody').textContent = areas.slice(0, 3).map(one).join(' · ') +
      (areas.length > 3 ? ' 等 ' + areas.length + ' 区' : '');
    $('#tsuAlertTime').textContent = '气象厅 ' + fmtHM(t.issued_ts, 9 * 3600);
    el.hidden = false;
  }

  // ---------------- 海啸 ----------------
  // 触发条件（演练与真实事件同一套）：
  //   ① 震中在海里（打不中任何陆地多边形）  ② 震源浅（≤70km）  ③ 震级够大（M6.5 起）
  // 深海长波速度 v=√(gH)，平均水深 4km → ≈200 m/s ≈ 720 km/h；越靠岸越浅越慢，
  // 这里按平均速度匀速推进，只做传播示意，不查海底测深。
  const TSU = { minMag: 6.5, maxDepth: 70, v: 0.198 };

  // 沿海城市（到达时间/波高的参照点）。海啸预警实际就是按这些点发倒计时的。
  const TSU_CITIES = [
    ['札幌', 43.06, 141.35], ['青森', 40.82, 140.75], ['仙台', 38.27, 140.87],
    ['东京', 35.68, 139.77], ['横滨', 35.44, 139.64], ['名古屋', 35.18, 136.91],
    ['大阪', 34.69, 135.50], ['广岛', 34.39, 132.46], ['福冈', 33.59, 130.40],
    ['高知', 33.56, 133.53], ['那霸', 26.21, 127.68], ['石垣', 24.34, 124.16],
    ['台北', 25.03, 121.57], ['上海', 31.23, 121.47], ['宁波', 29.87, 121.55],
    ['青岛', 36.07, 120.38], ['大连', 38.91, 121.61], ['厦门', 24.48, 118.09],
    ['汕头', 23.35, 116.68], ['香港', 22.32, 114.17], ['海口', 20.04, 110.32],
    ['釜山', 35.18, 129.08], ['马尼拉', 14.60, 120.98], ['海参崴', 43.12, 131.89],
    ['火奴鲁鲁', 21.31, -157.86], ['旧金山', 37.77, -122.42],
  ];

  // 射线法 + 包围盒预筛。全套陆地十几万点，靠 _bb 缓存逐个环跳过，
  // 结果再按 0.02° 网格记一份，同一震中只认真算一次（拖地图不会反复算）。
  function ringHit(ring, x, y) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const a = ring[i], b = ring[j];
      if ((a[1] > y) !== (b[1] > y) &&
        x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
    }
    return inside;
  }
  const LAND_CACHE = new Map();
  function landHit(lon, lat) {
    const k = lon.toFixed(2) + ',' + lat.toFixed(2);
    if (LAND_CACHE.has(k)) return LAND_CACHE.get(k);
    const sets = [];
    if (window.GEOW && window.GEOW.land) sets.push(window.GEOW.land);
    if (window.GEO && window.GEO.land) sets.push(window.GEO.land);
    if (window.GEO && window.GEO.cnisl) sets.push(window.GEO.cnisl);
    let hit = false;
    outer:
    for (const set of sets) {
      for (const ring of set) {
        if (ring.length < 3) continue;
        const bb = ringBB(ring);
        if (lon < bb[0] || lon > bb[1] || lat < bb[2] || lat > bb[3]) continue;
        if (ringHit(ring, lon, lat)) { hit = true; break outer; }
      }
    }
    if (LAND_CACHE.size > 600) LAND_CACHE.clear();
    LAND_CACHE.set(k, hit);
    return hit;
  }

  // 震源附近的最大波高（米）。经验式：M7.5/10km 约 2m、M8.5 约 7m、M9 约 14m，
  // 越浅越大（破裂面更容易把海底整体抬起来）。
  function tsuAmpl(mag, depth) {
    const m = Math.pow(10, (mag - 7.5) * 0.62);
    const shallow = Math.min(1.35, Math.pow(35 / Math.max(8, depth), 0.30));
    return 1.7 * m * shallow;
  }
  // 沿传播方向的衰减：近场近似不衰，200km 之后转 1/R^0.85。
  // 旧版 1/√R 掉得太慢，M6.5 的弱海啸在 5000km 外还有发布线，整张地图都被涂满。
  function tsuAmpAt(h0, R) {
    return h0 / Math.pow(1 + Math.max(0, R) / 240, 0.85);
  }

  // ---- 陆地遮挡 ----
  // 海啸只能沿着水面传。从震中每 10° 打一条射线，记下「这个方位上最先撞到的
  // 大片陆地」有多远（连续 90km 以上才算大陆/大岛，小岛礁不算）。被陆地挡住的
  // 方向不会有浪——这才是「8.1 级一炸，全日本都发海啸」的真正原因：以前只按直线
  // 距离衰减，太平洋侧的地震会越过本州岛给日本海侧也预报海啸。
  const TSU_AZ = 36;
  let shdCache = { key: '\u0000', b: null };
  function shadowOf(T) {
    if (shdCache.key === T.uid) return shdCache.b;
    const b = new Float32Array(TSU_AZ).fill(3000);
    const c = Math.max(0.15, Math.cos(T.lat * D2R));
    for (let i = 0; i < TSU_AZ; i++) {
      const az = i * 2 * Math.PI / TSU_AZ;
      const dy = Math.cos(az), dx = Math.sin(az);
      let run = 0;
      for (let d = 25; d <= 3000; d += 30) {
        const lat = T.lat + dy * d / 111.0;
        const lon = T.lon + dx * d / (111.0 * c);
        if (landHit(lon, lat)) {
          if (++run >= 3) { b[i] = Math.max(0, d - 60); break; }
        } else run = 0;
      }
    }
    shdCache = { key: T.uid, b };
    return b;
  }
  function tsuShdIdx(T, lat, lon) {
    const dy = (lat - T.lat) * 111.0;
    const dx = (lon - T.lon) * 111.0 * Math.max(0.15, Math.cos(T.lat * D2R));
    let az = Math.atan2(dx, dy);
    if (az < 0) az += Math.PI * 2;
    return Math.round(az / (Math.PI * 2) * TSU_AZ) % TSU_AZ;
  }
  // 某点的预计波高：先按距离衰减，若该点比「该方位的遮挡距离」更远，
  // 说明浪得绕过来（绕射），从 0.32 起按 320km 尺度指数衰减——近处还能有
  // 低档预报，隔一个岛的方位很快就掉到发布线以下。
  function tsuH(T, R, lat, lon) {
    const h = tsuAmpAt(T.h0, R);
    if (!T.shd) return h;
    const lim = T.shd[tsuShdIdx(T, lat, lon)];
    if (R <= lim) return h;
    return h * Math.max(0.03, 0.32 * Math.exp(-(R - lim) / 320));
  }

  // ---- 地名级判定 ----
  // 617 个沿海市町村（tools/gen_coast_places.py 生成）各算一遍：距离、遮挡、波高、
  // 到达时间，再按县聚合。面板显示的就是「哪个县、哪一处海岸、多少米、多久到」。
  let COAST = null;
  function loadCoast() {
    fetch('/api/coast').then(r => r.json()).then(d => {
      COAST = (d && d.prefs) || {};
      tsuCache = { key: '\u0000', val: null };
    }).catch(() => { COAST = {}; });
  }

  // 台湾 P-Alert 的 786 个测站（站号 / 地名 / 县市 / 经纬度）。实时值在 state.palert 里，
  // 两边按站号对起来。表本身很少变，取一次就够。
  function loadPalertStations() {
    fetch('/api/palert_stations', { cache: 'no-store' })
      .then(r => r.json())
      .then(d => {
        state.palertStations = (d && d.stations) || [];
        baseDirty = true;
      })
      .catch(() => { state.palertStations = []; });
  }
  function tsuRegions(T) {
    const out = [];
    if (!COAST) return out;
    const keys = Object.keys(COAST);
    if (!keys.length) {
      // 沿海表没取到（旧服务端）→ 退回 26 个参照城市，至少还能显示
      for (const c of TSU_CITIES) {
        const R = haversine(T.lat, T.lon, c[1], c[2]);
        if (R > TSU_MAX_R) continue;
        const h = tsuH(T, R, c[1], c[2]);
        if (h < TSU_MIN_H) continue;
        out.push({ pref: '', name: c[0], h, eta: R / TSU.v, cnt: 1, lon: c[2], lat: c[1] });
      }
      out.sort((a, b) => b.h - a.h);
      return out;
    }
    for (const pref of keys) {
      const arr = COAST[pref];
      let best = null, cnt = 0;
      for (let i = 0; i < arr.length; i++) {
        const p = arr[i];
        const slon = p[3], slat = p[4];
        const R = haversine(T.lat, T.lon, slat, slon);
        if (R > TSU_MAX_R) continue;
        const h = tsuH(T, R, slat, slon);
        if (h < TSU_MIN_H) continue;
        cnt++;
        if (!best || h > best.h) best = { name: p[0], h, eta: R / TSU.v, lon: slon, lat: slat };
      }
      if (best) out.push({ pref, name: best.name, h: best.h, eta: best.eta, cnt,
        lon: best.lon, lat: best.lat });
    }
    out.sort((a, b) => b.h - a.h);
    return out;
  }

  let tsuCache = { key: '\u0000', val: null };

  // 海啸的发布前提：地震得先「结束」。EEW 还在续报、震级还在往上修的时候
  // 就把岸线铺满地图，等于拿一个没定下来的震级提前公布海啸——真实链路不会
  // 这样：先终报，几分钟后气象厅才发津波予報/注意報。所以这里等终报落定。
  const TSU_SETTLE = 6;                 // 终报之后再等 6 秒（虚拟秒）
  function quakeSettled(ev) {
    if (!ev) return false;
    if (ev.isHist || state.replayAt || state.histReplayAt) return true;
    if (ev.cancel) return true;
    if (!ev.final) return false;
    return sinceFinal(ev) >= TSU_SETTLE;
  }

  function tsunamiOf(ev) {
    if (!ev || ev.cancel) return null;
    const key = [ev.uid, ev.mag, ev.depth, ev.lat, ev.lon].join('|');
    if (tsuCache.key === key) return tsuCache.val;
    let val = null;
    const mag = ev.mag || 0, dep = Math.max(0, ev.depth || 0);
    if (mag >= TSU.minMag && dep <= TSU.maxDepth && !landHit(ev.lon, ev.lat)) {
      const T = { uid: ev.uid, lat: ev.lat, lon: ev.lon, mag, depth: dep, h0: tsuAmpl(mag, dep) };
      T.shd = shadowOf(T);
      T.regions = tsuRegions(T);
      // 一处够发布线的海岸都没有 → 这起地震在预报口径上就是「无海啸」，
      // 不弹面板、不画线（和 JMA 只发「若干の海面変動」时一致）。
      if (T.regions.length) val = T;
    }
    tsuCache = { key, val };
    return val;
  }

  function fmtTsuDur(sec) {
    if (!(sec > 0)) return '—';
    if (sec < 90) return Math.ceil(sec) + ' 秒';
    if (sec < 5400) return Math.round(sec / 60) + ' 分';
    return (sec / 3600).toFixed(1) + ' 小时';
  }

  // 海啸波前：青色的「浪带」+ 流体虚线流动，比地震波慢得多（720 vs 2 万 km/h），
  // 所以单独一层、画在地震波下面，两者不会互相盖住。
  // 海啸沿岸分级：按「该岸段的预计波高」上色。七档，对应 JMA 的预报 / 注意报 /
  // 警报 / 大津波警报（大津波再分 5m 和 10m 两级），颜色沿 蓝→黄→橙→红→紫 递进：
  //   0.2~0.5 / 0.5~1 蓝（预报）· 1~2 / 2~3 黄橙（注意报）· 3~5 红（警报）
  //   5~10 / 10+ 紫（大津波警报，加粗 + 辉光）
  // 档位多一档，色带就能看出「涨了一点」还是「翻了一倍」，不再只有四级跳。
  const TSU_CLASS = [
    { h: 0.2, color: 'rgba(56,189,248,', w: 3.0, name: '海啸预报', note: '0.2~0.5m' },
    { h: 0.5, color: 'rgba(59,130,246,', w: 3.6, name: '海啸预报', note: '0.5~1m' },
    { h: 1.0, color: 'rgba(250,204,21,', w: 4.2, name: '海啸注意报', note: '1~2m' },
    { h: 2.0, color: 'rgba(245,158,11,', w: 5.0, name: '海啸注意报', note: '2~3m' },
    { h: 3.0, color: 'rgba(239,68,68,', w: 6.0, name: '海啸警报', note: '3~5m' },
    { h: 5.0, color: 'rgba(168,85,247,', w: 8.2, name: '大海啸警报', note: '5~10m' },
    { h: 10.0, color: 'rgba(126,58,242,', w: 10.6, name: '大海啸警报', note: '10m 以上' },
  ];
  const tsuClassOf = h => {
    for (let i = TSU_CLASS.length - 1; i >= 0; i--) if (h >= TSU_CLASS[i].h) return i;
    return 0;
  };
  const TSU_BIG = TSU_CLASS.findIndex(c => c.h >= 5);   // 大津波警报起始档（5m）
  // 发布线：预计波高不到 20cm 的岸段 JMA 根本不发预报，也就不该在地图上画线。
  // 只铺「够格的岸段」，地图上剩下的就是真正被点名的那几段海岸。
  const TSU_MIN_H = 0.2;
  const TSU_MAX_R = 2600;                    // 影响半径上限（km）

  // 海岸线采样：物理陆地的每个环，边界就是海岸线。沿环每 ~13km 取一个点，
  // 算「波到达时间 eta」和「该处预计波高 h」。按事件缓存，不重复遍历全球十几万点。
  let coastCache = { key: '', rings: null };
  function tsuCoast(T) {
    if (coastCache.key === T.uid) return coastCache.rings;
    const rings = [];
    const sets = [];
    if (window.GEOW && window.GEOW.land) sets.push(window.GEOW.land);
    if (window.GEO && window.GEO.land) sets.push(window.GEO.land);
    const MAXR = TSU_MAX_R;                   // 更远的岸段波高已低于发布线，不评估
    for (const set of sets) {
      for (const ring of set) {
        if (ring.length < 8) continue;
        const bb = ringBB(ring);
        const dLat = Math.max(0, Math.max(bb[2] - T.lat, T.lat - bb[3]));
        const dLon = Math.max(0, Math.max(bb[0] - T.lon, T.lon - bb[1]));
        if (Math.hypot(dLat, dLon) * 111 > MAXR) continue;
        const out = [];
        let pl = null, pa = null;
        for (let i = 0; i < ring.length; i++) {
          const p = ring[i];
          const R = haversine(T.lat, T.lon, p[1], p[0]);
          if (R > MAXR) continue;
          if (pl !== null && haversine(pa, pl, p[1], p[0]) < 13) continue;
          pl = p[0]; pa = p[1];
          out.push([p[0], p[1], R / TSU.v, tsuH(T, R, p[1], p[0])]);
        }
        if (out.length > 1) rings.push(out);
      }
    }
    coastCache = { key: T.uid, rings };
    return rings;
  }

  // 把沿岸点编成 Path2D：pred = 全岸段「预计」（淡色），hit = 波已到达（实色）。
  // 关键：只有预计波高 ≥ TSU_MIN_H 的连续岸段才进路径。整条大陆轮廓一律不画，
  // 否则地图上每个半岛、每个海湾都会被涂满，既看不出重点，也不是 JMA 的画法。
  // 一秒重建一次（签名里带秒和视图），平时只做 8 次 stroke。
  let coastPath = { sig: '', pred: null, hit: null, n: 0 };
  function tsuCoastPaths(T, el) {
    const sig = [T.uid, Math.floor(el), view.s.toFixed(3), Math.round(view.tx),
      Math.round(view.ty), view.w, view.h].join('|');
    if (coastPath.sig === sig) return coastPath;
    const rings = tsuCoast(T);
    const NC = TSU_CLASS.length;
    const pred = Array.from({ length: NC }, () => new Path2D());
    const hit = Array.from({ length: NC }, () => new Path2D());
    let n = 0;
    // 一段连续达标的岸段才落笔；中间掉回发布线以下的点把线断开，
    // 于是「只有被预报的那几段海岸有线」，不会连成一条贯穿全图的假轮廓。
    const put = (a, b) => {
      const [x1, y1, p] = a, [x2, y2] = b;
      if ((x1 < -60 && x2 < -60) || (x1 > view.w + 60 && x2 > view.w + 60)) return;
      if ((y1 < -60 && y2 < -60) || (y1 > view.h + 60 && y2 > view.h + 60)) return;
      const k = tsuClassOf(p[3]);
      pred[k].moveTo(x1, y1); pred[k].lineTo(x2, y2);
      if (p[2] <= el) { hit[k].moveTo(x1, y1); hit[k].lineTo(x2, y2); }
      n++;
    };
    for (const ring of rings) {
      let run = [], lx = null, ly = null;
      const flush = () => {
        if (run.length >= 2) for (let i = 0; i + 1 < run.length; i++) put(run[i], run[i + 1]);
        run = [];
      };
      for (let i = 0; i < ring.length; i++) {
        const p = ring[i];
        if (p[3] < TSU_MIN_H) { flush(); lx = ly = null; continue; }
        const x = px(p[0]), y = py(p[1]);
        // 屏幕距离不到 1.4px 的点不落笔：小比例尺下否则全是碎齿，看不出是海岸分级线
        if (lx !== null && Math.abs(x - lx) < 1.4 && Math.abs(y - ly) < 1.4) continue;
        lx = x; ly = y;
        run.push([x, y, p]);
      }
      flush();
    }
    coastPath = { sig, pred, hit, n };
    return coastPath;
  }

  function drawTsunami(ev, el) {
    const T = tsunamiOf(ev);
    if (!T || !state.layers.waves) { state.tsu = null; syncTsuBox(null); return; }
    // 线宽随缩放走：全球视图下细，放大到县一级时加粗，避免「越看越像头发丝」。
    const zf = clamp(Math.pow(view.s / (view.fit || 1), 0.34), 0.9, 2.3);
    // ① 沿岸分级线：只铺达到发布标准的岸段，再把「波已经到了」的岸段用实色压上去。
    //    面板的地名判定独立于岸线采样，所以这里没有线也不隐藏面板。
    const P = tsuCoastPaths(T, el);
    state.tsu = T;
    const R = TSU.v * Math.max(0, el);                 // 已传播公里数
    syncTsuBox(T, R, el);

    if (P.n) {
      ctx.save();
      ctx.lineCap = 'round';
      ctx.lineJoin = 'round';
      for (let k = 0; k < TSU_CLASS.length; k++) {
        ctx.strokeStyle = TSU_CLASS[k].color + '0.40)';
        ctx.lineWidth = TSU_CLASS[k].w * zf;
        ctx.stroke(P.pred[k]);
      }
      for (let k = 0; k < TSU_CLASS.length; k++) {
        if (k >= TSU_BIG) {                                 // 5m 以上：大津波警报，垫一圈辉光
          ctx.strokeStyle = 'rgba(192,132,252,.20)';
          ctx.lineWidth = TSU_CLASS[k].w * zf + 12;
          ctx.stroke(P.hit[k]);
        }
        ctx.strokeStyle = TSU_CLASS[k].color + '0.96)';
        ctx.lineWidth = TSU_CLASS[k].w * zf;
        ctx.stroke(P.hit[k]);
      }
      ctx.restore();
    }

    // ② 波前位置：一圈极淡的点线，只用来读「浪走到哪了」，不做主视觉
    const rp = R * kmPx(ev.lat);
    if (rp > 12) {
      ctx.save();
      ctx.setLineDash([2, 9]);
      ctx.beginPath(); ctx.arc(px(ev.lon), py(ev.lat), rp, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(125,240,255,.18)';
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.restore();
    }
  }

  // ---- 海啸信息面板 ----
  let tsuBoxKey = '';
  function syncTsuBox(T, R, el) {
    const box = $('#tsun');
    if (!box) return;
    if (!T) {
      if (!box.hidden) { box.hidden = true; tsuBoxKey = ''; }
      return;
    }
    const k = T.uid + '|' + Math.round((R || 0) / 5);
    if (box.hidden) box.hidden = false;
    if (k === tsuBoxKey) return;
    tsuBoxKey = k;
    const now = R || 0;
    const cur = tsuAmpAt(T.h0, now);
    box.classList.toggle('big', T.h0 >= 5);
    // 徽章直接显示「当前波前所处级别」，颜色跟着级别走——一眼看出是预报、注意报还是警报
    const lv = TSU_CLASS[tsuClassOf(cur)];
    const bd = box.querySelector('.tsun-badge');
    if (bd) {
      bd.textContent = lv.name;
      bd.style.background = lv.color + '1)';
      bd.style.color = lv.h >= 3 ? '#fff' : '#04121a';
    }
    $('#tsunH').textContent = cur < 0.1 ? '<0.1' : cur.toFixed(1);
    $('#tsunSub').textContent = 'M' + (T.mag || 0).toFixed(1) + ' · 深 ' + Math.round(T.depth) +
      'km · 震源波高 ' + T.h0.toFixed(1) + 'm';
    $('#tsunNow').textContent = '波前 ' + Math.round(now) + ' km · 已走 ' + fmtTsuDur(el || 0);
    const sc = $('#tsunScale');
    if (sc && !sc.childElementCount) {
      sc.innerHTML = TSU_CLASS.map(c =>
        '<span><i style="background:' + c.color + '.95)"></i>' + c.name +
        '<em>' + c.note + '</em></span>').join('');
    }
    const list = $('#tsunList');
    const rows = T.regions || [];
    list.innerHTML = rows.slice(0, 16).map(r => {
      const left = r.eta - (el || 0);
      const cl = TSU_CLASS[tsuClassOf(r.h)];
      return '<div class="tsun-row' + (left <= 0 ? ' reach' : '') + '">' +
        '<span>' + (r.pref || '境外') + '</span>' +
        '<i>' + r.name + (r.cnt > 1 ? ' 等' + r.cnt + '处' : '') + '</i>' +
        '<em style="color:' + cl.color + '1)">' + r.h.toFixed(1) + '</em>' +
        '<b>' + (left <= 0 ? '已到达' : fmtTsuDur(left)) + '</b></div>';
    }).join('');
  }

  // 单起地震的 P/S 波前。strong=true 走主事件醒目样式（含标签/渐变），
  // 其它同时进行的地震用细淡圈——一眼分清主次，但都看得见。
  function drawWavesOf(e, strong, elapsed) {
    // 波前半径 = 波已经走到的「地表水平半径」，波还在地下时（v·t < h）没有圈：
    //   R(t) = √(D² − h²)，D = (t/a)^(1/0.9) 为斜距
    // 深 800km 与深 10km 不再长得一样；浅源也换掉了原来那套均匀速度，
    // 远距离不再比真实走时慢三四成（见文件顶部走时模型说明）。
    // 演练：圆心和深度取真值（正确震源），波速也用服务端那一套等效速度，
    // 这样「圈扫到某个台站的时刻」= 「那个台站刚好报到的时刻」，两边严丝合缝。
    // 卡片和震中标靶仍然用估算值——真想藏起来的东西是那一个。
    const src = srcOf(e);
    const dep = Math.max(0, src.depth || 10);
    // 走时统一走 AK135 表；演练若把走时整体压缩过（震中在远海、附近没台站），
    // 服务端会给 sim_tt_scale，这里按同一比例缩放虚拟时间，圈和台站仍然同步。
    const ttScale = (e.isSim && e.sim_tt_scale > 0) ? e.sim_tt_scale : 1;
    const te = elapsed / ttScale;      // 压缩过就把虚拟时间还原成真实走时
    const ttModel = ttModelFor(e);
    let dP = distP(te, dep, ttModel);
    let dS = distS(te, dep, ttModel);
    // 演练：波前扫到有感边界就停（服务端给的 sim_wave_km）。不封顶的话圈会一直
    // 往外涨，看上去「这地震永远没完」，震度速報当然怎么发都像是提前发。
    let fadeEnd = 1;
    if (e.isSim && e.sim_wave_km > 0) {
      const cap = e.sim_wave_km;
      fadeEnd = clamp((cap - dS) / 55, 0, 1);
      dS = Math.min(dS, cap);
      dP = Math.min(dP, cap * 1.18);
    }
    const rS = dS * kmPx(src.lat), rP = dP * kmPx(src.lat);
    // 起始阶段半径只有几像素，套在震中标记上会被看成「小圈」，
    // 所以从 10px 开始淡入、44px 转满，圆圈是连续长大的而不是突然冒出来卡住
    const fadeS = clamp((rS - 10) / 34, 0, 1);
    const fadeP = clamp((rP - 10) / 34, 0, 1);
    if (strong) {
      window.__waveDbg = {
        t: +elapsed.toFixed(1), depth: dep, dP: Math.round(dP), dS: Math.round(dS),
        rP: Math.round(rP), rS: Math.round(rS), fadeS: +fadeS.toFixed(2)
      };
    }
    if (fadeS > 0.02) {
      ctx.save();
      ctx.globalAlpha = (strong ? fadeS : fadeS * 0.4) * fadeEnd;
      ring(e, dS, strong ? 'rgba(255,190,60,.95)' : 'rgba(255,198,112,.8)', strong ? 2.0 : 1);
      if (strong) ring(e, dS, 'rgba(255,176,32,.18)', 8);
      ctx.restore();
    }
    if (fadeP > 0.02) {
      ctx.save();
      ctx.globalAlpha = (strong ? 0.9 : 0.45) * fadeP * fadeEnd;
      ring(e, dP, strong ? 'rgba(150,225,255,.7)' : 'rgba(150,225,255,.6)',
        strong ? 1.3 : 0.9, [6, 6]);
      ctx.restore();
    }
    const cx = px(src.lon), cy = py(src.lat);
    if (strong) {
      if (fadeS > 0.6 && fadeEnd > 0.05) ringLabel(src, dS, 'S 波', '#ffc04d');
      if (fadeP > 0.6 && fadeEnd > 0.05) ringLabel(src, dP, 'P 波', '#9fd8ff');
      if (rS > 60 && fadeS > 0.4) {
        const rg = ctx.createRadialGradient(cx, cy, Math.max(0, rP), cx, cy, rS);
        rg.addColorStop(0, 'rgba(255,176,32,.02)');
        rg.addColorStop(.78, 'rgba(255,140,60,.06)');
        rg.addColorStop(1, 'rgba(255,90,60,.02)');
        ctx.fillStyle = rg;
        ctx.beginPath(); ctx.arc(cx, cy, rS, 0, Math.PI * 2); ctx.fill();
      }
    }
    return { rP: rP, cx: cx, cy: cy };
  }

  // 地图动态层（地震/波前/测站/标签）。左右无缝：按世界宽度平移画多份，
  // 事情挪到地图另一侧时也能接着显示。
  function paintOverlay(ev, live, histPlaying, elapsed, firstCopy) {
    if (ev && state.layers.waves && (live || histPlaying) && elapsed > 0.6) {
      // 多事件波前：同屏最多 waveN 起（1~10，默认 10），主事件最亮
      if (live && !state.historyEvent) {
        const N = clamp(Math.round(state.settings.waveN || 10), 1, 10);
        const others = activeEvents().filter(e => e.uid !== ev.uid).slice(0, Math.max(0, N - 1));
        for (const o of others) {
          const t = elapsedOf(o);
          if (t > 0.6) drawWavesOf(o, false, t);
        }
      }
      const w = drawWavesOf(ev, true, elapsed);
      if (firstCopy) {
        initialFrame(ev);
        autoFrame(w.rP, w.cx, w.cy);
      }
    }

    // 海啸：比地震波慢两个数量级，画在波前下层，先铺海面再叠地震圈。
    // 但要等地震结束（终报之后）才铺——见 quakeSettled。
    if (ev && (live || histPlaying) && quakeSettled(ev)) drawTsunami(ev, elapsed);
    else { state.tsu = null; syncTsuBox(null); }

    if (state.layers.stations) drawStations(ev, live || !!state.historyEvent);
    drawLpgm();                                   // 长周期观测点（紫色圈）
    if (ev && ev.isSim) drawSimDetected(ev);   // 演练：报到的台站一个个亮起来
    if (state.layers.palert !== 0) drawPalert();   // 旧配置里没有这个键，默认开
    if (state.layers.gqst) drawGQActive();
    // 县震度标签跟着热力场一起退：演练终报 15 虚拟秒后停画，
    // 不然速報都出了地图上还飘着一圈黄橙色的「5強/4」圆点
    if (ev && state.layers.field && state.layers.pref !== 0 &&
        !(ev.isSim && simFinalAge(ev) >= 15)) drawPrefLabels(ev);
    if (ev && simFinalAge(ev) < 12) drawEpicenter(ev, live);
    if (live && !state.historyEvent) drawOtherEvents(ev);
    // 绿框放最后画：震中标靶正好压在台站簇中心，先画就被它整个盖住了
    drawShakeHints(ev);
    drawFocus(ev);
    if (state.simPick.on) drawSimPick();
    if (state.layers.labels) drawLabels();
  }

  function draw() {
    stepViewTween();            // 起手取景/撞边拉远：补间每帧推进一步
    const ev = eventForMap();
    // 钉住的台站卡片每秒刷新一次，数据一到就能看到观测状态变化
    if (state.tipPin && nowSec() - tipAt > 1) {
      tipAt = nowSec();
      const pinSt = state.stations.find(s => s.code === state.tipPin);
      const tip = $('#tip');
      if (pinSt) tip.innerHTML = stationInfoHtml(pinSt);
      else { state.tipPin = ''; tip.classList.remove('show', 'pin'); }
    }
    const live0 = !!currentEvent();
    // 演练终报后热力场不急着收：速報要終報后约 2 分钟才来，收早了中间空窗。
    // 速報到了就让它接管（县块同源），再亮 10 分钟后一起收——底色亮太久也是干扰。
    const simDone = ev && ev.isSim && ev.final && (() => {
      const o = state.obs;
      if (o && sameQuake(ev)) return nowSec() - (o.report_ts || 0) > 600;
      return simFinalAge(ev) >= 120;
    })();
    // 真实速報在事件收场后也照样铺：15 分钟内的地域级原文都算数
    const obFresh = state.obs && state.obs.pref &&
      nowSec() - (state.obs.report_ts || state.obs.origin_ts || 0) < 900;
    const showField = obFresh ||
      (!!ev && (live0 || !!state.historyEvent) && !simDone);
    if (baseDirty) drawBase();
    const key = (ev ? ev.uid + '|' + (ev.serial || 0) : 'none') + (showField ? '' : '#off') +
      '|' + obsKeyOf();
    if (fieldDirty || key !== fieldKey) { drawField(showField ? ev : null); fieldKey = key; }

    ctx.setTransform(view.dpr, 0, 0, view.dpr, 0, 0);
    ctx.clearRect(0, 0, view.w, view.h);
    ctx.drawImage(baseCanvas, 0, 0, view.w, view.h);

    const live = !!currentEvent();
    const histPlaying = !!(state.histReplayAt && state.historyEvent);
    const elapsed = ev ? elapsedOf(ev) : 0;

    OCC = [];
    const wp = worldPx();
    const n = copiesNeeded();
    for (let k = 0; k < n; k++) {
      ctx.save();
      if (k) ctx.translate(k * wp, 0);
      if (ev && state.layers.field) {
        ctx.globalAlpha = 0.9;
        ctx.drawImage(fieldCanvas, 0, 0, view.w, view.h);
        ctx.globalAlpha = 1;
      }
      paintOverlay(ev, live, histPlaying, elapsed, k === 0);
      ctx.restore();
    }
    drawScaleBar();
  }

  const SCALE_RISK = { 10: 1, 20: 2, 30: 3, 40: 4, 45: 5, 50: 6, 55: 7, 60: 8, 70: 9 };

  // GlobalQuake 全球测站实时强度：StationsIntensityPacket 用 index 索引进测站表。
  // 值是「相对噪声底的振幅比」。GQ 官方的 pct=(ratio-1)^0.25/50000^0.25 曲线太"严"——
  // ratio 要四五百才够到震度 2，日本/关东那些中小振幅台站常年一片灰蓝，看着像没反应。
  // 这里先给 0.9 的基线、再把斜率放大到 6.2：ratio≈1.2 就有震度 1 的蓝点，
  // ratio≈50 落到震度 2.0 转绿，之后按 gqRiskOf 逐档递进，微动也能看出来。
  function drawGQActive() {
    const act = state.gqAct;
    if (!act || !act.length || !state.gqSt.length) return;
    const K = Math.pow(50000, 0.25);          // = 14.953…
    // 按档位分桶批量画：一盏一盏画的话，几千个站每帧上万条 arc，拖一下就掉帧。
    // 分桶之后同色只剩一次 fill，站数翻几倍也不怕。
    const buckets = [];
    for (let i = 0; i < act.length; i++) {
      const a = act[i];
      const st = state.gqIdx.get(a[0]);
      if (!st) continue;
      const ratio = a[1];
      const pct = Math.pow(Math.max(0, ratio - 1), 0.25) / K;
      const s = 0.9 + 6.2 * pct;
      const risk = gqRiskOf(s);
      if (risk < 1) continue;
      const x = px(st[2]), y = py(st[1]);
      if (x < -8 || x > view.w + 8 || y < -8 || y > view.h + 8) continue;
      const r = risk <= 1 ? 1.6 : 1.8 + Math.min(3.6, risk * 0.55);
      (buckets[risk] || (buckets[risk] = [])).push(x, y, r);
    }
    for (let risk = 1; risk < buckets.length; risk++) {
      const b = buckets[risk];
      if (!b || !b.length) continue;
      const c = RISK_RGB[risk] || RISK_RGB[1];
      // 光晕：震度2 以上才给，弱站不加，免得整片糊成雾
      if (risk >= 3) {
        ctx.beginPath();
        for (let k = 0; k < b.length; k += 3) {
          const x = b[k], y = b[k + 1], r = b[k + 2];
          ctx.moveTo(x + r + 2.5, y);
          ctx.arc(x, y, r + 2.5, 0, Math.PI * 2);
        }
        ctx.fillStyle = 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',.20)';
        ctx.fill();
      }
      ctx.beginPath();
      for (let k = 0; k < b.length; k += 3) {
        const x = b[k], y = b[k + 1], r = b[k + 2];
        ctx.moveTo(x + r, y);
        ctx.arc(x, y, r, 0, Math.PI * 2);
      }
      ctx.fillStyle = 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
      ctx.fill();
    }
  }

  // 地图上的测站 = 气象厅电文里实际报到的观测点（addr/震度），
  // 收到几个就画几个，不再用内置代表点凑数。震度 0（没摇）不画。
  function realStations(ev) {
    if (state.stColor === 'est') return null;        // 手动切到「按预估着色」时用代表点
    const o = state.obs;
    if (!o || !o.points || !o.points.length) return null;
    // 演练的速报是推算，不是观测点——绝不能在图上铺一片「实测」。
    // 演练只出县块（予想震度）+ 电文卡，真实电文（sim 不带）才铺点。
    if (o.sim) return null;
    if (ev && o.origin_ts && ev.origin_ts &&
      Math.abs(o.origin_ts - ev.origin_ts) > 300) return null;   // 别的场次，不盖当前预警
    const list = [];
    for (const p of o.points) {
      if (typeof p.lat !== 'number' || typeof p.lon !== 'number') continue;
      if (!(SCALE_RISK[p.scale] > 0)) continue;   // 震度0（没摇）不画
      list.push(p);
    }
    return list.length >= 2 ? list : null;
  }

  function obsKeyOf() {
    const o = state.obs;
    return o ? String(o.origin_ts || 0) + ':' + (o.points ? o.points.length : 0) : '';
  }

  // 图标：外发光 + 实心核 + 暗描边，越强越大越亮；强震动再套一圈细光环。
  // 十档震度各缓存一个精灵，几百个测站同屏也不掉帧。
  const SPR = new Map();
  function stationSprite(risk) {
    risk = Math.max(0, Math.min(9, risk | 0));
    let c = SPR.get(risk);
    if (c) return c;
    const s = 34;
    c = document.createElement('canvas');
    c.width = c.height = s * 2;
    const g = c.getContext('2d');
    const r = 2.2 + risk * 0.38;
    const rgb = vibeRGB(risk);
    const col = a => 'rgba(' + Math.round(rgb[0]) + ',' + Math.round(rgb[1]) +
      ',' + Math.round(rgb[2]) + ',' + a + ')';
    const grd = g.createRadialGradient(s, s, 0, s, s, r * 2.8);
    grd.addColorStop(0, col(0.50));
    grd.addColorStop(0.42, col(0.20));
    grd.addColorStop(1, col(0));
    g.fillStyle = grd;
    g.beginPath(); g.arc(s, s, r * 2.8, 0, Math.PI * 2); g.fill();
    g.beginPath(); g.arc(s, s, r, 0, Math.PI * 2);
    g.fillStyle = col(0.96); g.fill();
    g.lineWidth = 1; g.strokeStyle = 'rgba(4,8,14,.72)'; g.stroke();
    if (risk >= 5) {
      g.beginPath(); g.arc(s, s, r + 2.4, 0, Math.PI * 2);
      g.lineWidth = 1.1; g.strokeStyle = col(0.42); g.stroke();
    }
    SPR.set(risk, c);
    return c;
  }

  function drawRealStations(list, ev) {
    const now = nowSec();
    const key = obsKeyOf();
    if (state.obsKey !== key) { state.obsKey = key; state.obsAt = now; }
    // 底层：整个真实台网用极淡的小点垫着（有实测点时也不至于只剩孤零零几个点）
    ctx.beginPath();
    let nBase = 0;
    for (const st of state.stations) {
      const x = px(st.lon), y = py(st.lat);
      if (x < -12 || y < -12 || x > view.w + 12 || y > view.h + 12) continue;
      ctx.moveTo(x + 1.4, y);
      ctx.arc(x, y, 1.4, 0, Math.PI * 2);
      nBase++;
    }
    if (nBase) {
      ctx.fillStyle = 'rgba(126,156,196,.16)';
      ctx.fill();
    }
    // 一个一个放上去：按震度从大到小依次登场，最多 1.1 秒铺满
    const step = Math.min(0.013, 1.1 / Math.max(1, list.length));
    const zoomIn = view.s > (view.fit || 1) * 2.6;
    const boxes = [];
    for (let i = 0; i < list.length; i++) {
      const p = list[i];
      const x = px(p.lon), y = py(p.lat);
      if (x < -24 || y < -24 || x > view.w + 24 || y > view.h + 24) continue;
      const t = now - state.obsAt - i * step;
      const a = t >= 0.3 ? 1 : Math.max(0, t / 0.3);
      if (a <= 0.01) continue;
      const risk = SCALE_RISK[p.scale] || 0;
      ctx.globalAlpha = a;
      ctx.drawImage(stationSprite(risk), x - 34, y - 34);
      ctx.globalAlpha = 1;

      if (!zoomIn || p.scale < 40 || boxes.length >= 34) continue;
      const text = (p.addr || '') + ' ' + (p.label || '');
      ctx.font = '600 9px ui-monospace,monospace';
      const w = ctx.measureText(text).width;
      const bx = x + 7, by = y - 6;
      if (boxes.some(b => bx < b[0] + b[2] && bx + w + 6 > b[0] &&
        by < b[1] + b[3] && by + 12 > b[1])) continue;
      boxes.push([bx, by, w + 6, 12]);
      ctx.fillStyle = 'rgba(6,10,16,.72)';
      roundRect(ctx, bx, by, w + 5, 12, 3);
      ctx.fill();
      ctx.fillStyle = 'rgba(236,244,252,.94)';
      ctx.fillText(text, bx + 2.5, by + 9);
    }
  }

  // 都道府县区块（气象厅予想震度用）——比等值面淡一些，避免整块糊住底图
  const PREF_FILL = ['rgba(66,83,107,0)', 'rgba(59,130,246,.20)', 'rgba(6,182,212,.24)',
    'rgba(34,197,94,.28)', 'rgba(250,204,21,.34)', 'rgba(251,146,60,.40)',
    'rgba(249,115,22,.46)', 'rgba(239,68,68,.52)', 'rgba(220,38,38,.56)', 'rgba(162,28,175,.60)'];

  let PREFIDX = null;
  function prefIndex() {
    if (PREFIDX !== null) return PREFIDX;
    PREFIDX = {};
    const g = window.GEOJP;
    if (g && g.prefs) for (const p of g.prefs) PREFIDX[p.n] = p;
    return PREFIDX;
  }

  function tracePref(g, pref, dx) {
    dx = dx || 0;
    for (const ring of pref.p) {
      for (let i = 0; i < ring.length; i++) {
        const x = px(ring[i][0]) + dx, y = py(ring[i][1]);
        i ? g.lineTo(x, y) : g.moveTo(x, y);
      }
      g.closePath();
    }
  }

  // 地图上日本那一层用「都道府县填色块」还是渐变热力场，全看这里：
  //   · 演练 → 按真值算县块（和台站同源）；
  //   · 真实速报 → 报文带了地域别予想震度就原样用，没带就按报文给的震源算县块。
  // 总之只要气象厅发了报，就是填色块，不再画那层自己外推的等值面。
  function prefBlocks(ev) {
    if (!ev || ev.country !== 'JP') return null;
    if (ev.isSim || ev.training) {
      const c = simPrefIntensity(ev);
      if (c) return c;
    }
    const pi = ev.pref_intensity;
    if (pi && Object.keys(pi).length) return pi;
    if (!isJmaReport(ev)) return null;    // 别的源的报不铺块
    return estPrefIntensity(ev);
  }

  let pbKey = '', pbCache = null;
  // 按震源算每个县的震度：取县多边形上离震源最近的顶点，套同一条衰减式。
  // 直接用县中心会偏——三陸沖这种海上震源，岩手/宮城中心离得远，会低一整档。
  function prefFromSrc(ev, tag) {
    const g = window.GEOJP;
    if (!g || !g.prefs) return null;
    const src = srcOf(ev);
    const k = tag + ev.uid + '#' + (ev.serial || 0) + '#' + (src.mag || 0) + '#' +
      (src.depth || 0) + '#' + Math.round((src.lat || 0) * 100) + ',' +
      Math.round((src.lon || 0) * 100);
    if (pbKey === k && pbCache) return pbCache;
    const mag = src.mag || 0, depth = src.depth || 10;
    const out = {}; let best = -9;
    for (const p of g.prefs) {
      let dmin = Infinity;
      for (const ring of p.p) for (const v of ring) {
        const d = haversine(src.lat, src.lon, v[1], v[0]);
        if (d < dmin) dmin = d;
      }
      if (!isFinite(dmin)) continue;
      const R = Math.hypot(dmin, depth);
      const val = shindoVal(pgv(mag, depth, R));
      out[p.n] = { value: Math.round(val * 100) / 100, label: gradeOf(val).code };
      if (val > best) best = val;
    }
    if (best < -8) return null;
    out.__max = best;
    out.__est = tag === 'est:';      // 推算出来的块（不是气象厅原文）
    pbKey = k; pbCache = out;
    return out;
  }
  function simPrefIntensity(ev) { return prefFromSrc(ev, 'sim:'); }
  function estPrefIntensity(ev) { return prefFromSrc(ev, 'est:'); }
  // 测试钩子：喂一条假报，看县块是怎么选出来的（截图/自检脚本用）
  window.__blkTest = ev => {
    const b = prefBlocks(ev);
    if (!b) return null;
    const names = Object.keys(b).filter(k => !k.startsWith('__'));
    let top = '';
    for (const n of names) if (!top || b[n].value > b[top].value) top = n;
    return { n: names.length, est: !!b.__est, max: b.__max, top: top,
             topLabel: top ? (b[top].label || '') : '' };
  };

  // 震度速報 和当前这场是不是同一起（同一场地震，两个源的发震时刻差几秒）
  function sameQuake(ev) {
    const o = state.obs;
    if (!ev || !o || !o.origin_ts || !ev.origin_ts) return false;
    return Math.abs(o.origin_ts - ev.origin_ts) < 300;
  }

  // 震度速報 → 都道府县块。演练与「真实速报但没带观测点」两种情况都走这里：
  // 速报是地域级的，落到地图上就是块，不是一片台站点。
  let obKey = '', obCache = null;
  function obsBlocks() {
    const o = state.obs;
    if (!o || !o.pref) return null;
    // 真实电文自带观测点时照旧铺点，不再叠块，免得两套口径打架
    if (!o.sim && o.points && o.points.length) return null;
    const k = String(o.origin_ts || 0) + '|' + String(o.report_ts || 0);
    if (obKey === k) return obCache;
    const out = {};
    for (const name of Object.keys(o.pref)) {
      const c = o.pref[name] || {};
      // 震度速報只报「震度3以上」的地域。以前把震度1/2 的县也一起上色，
      // 于是一起 M7 就从北海道一路亮到高知——图上看着像全国都报了，电文里
      // 其实只有二十来个县。以电文为准：3 未满不上图。
      if ((c.scale || 0) < 30) continue;
      const risk = SCALE_RISK[c.scale] || 0;
      if (risk < 1) continue;
      out[name] = { risk: risk, label: c.label || GRADE[risk] };
    }
    obKey = k;
    obCache = Object.keys(out).length ? out : null;
    return obCache;
  }

  function obsOf(st) {
    const o = state.obs;
    if (!o || !o.pref || st.country !== 'JP') return null;
    return o.pref[st.region] || null;
  }

  // ---------------- 台站「有没有在摇」----------------
  // 平时没有震度速报时台网只是待机（暗点），但每个台站其实都能给出状态：
  // 这次电文有没有报到它、报到的震度是多少、离震中多远、预计几度。
  // 悬停/点击任一台站都能看到，不用再去数颜色。
  function nearestObsPoint(st, maxKm) {
    const o = state.obs;
    if (!o || !o.points || !o.points.length) return null;
    let best = null, bd = maxKm || 8;
    for (const p of o.points) {
      if (typeof p.lat !== 'number' || typeof p.lon !== 'number') continue;
      const d = haversine(st.lat, st.lon, p.lat, p.lon);
      if (d < bd) { bd = d; best = p; }
    }
    return best ? { p: best, km: bd } : null;
  }

  // ---------------- 台站震动包络（点开台站看的那条线） ----------------
  // kmoni 每秒给一个计测震度，攒最近两分钟就是一条真实的震动包络；
  // 没有实测（演练/推算/海底站）时，用同一套衰减式按时间采样一条推算包络。
  const HIST = new Map();          // code → [[秒, 計測震度], ...]
  function histPush(code, v, t) {
    let a = HIST.get(code);
    if (!a) { a = []; HIST.set(code, a); }
    const last = a[a.length - 1];
    if (last && t - last[0] < 0.7) { a[a.length - 1] = [t, v]; return; }  // 同一帧内不重复
    a.push([t, v]);
    while (a.length > 160) a.shift();
    while (a.length && t - a[0][0] > 150) a.shift();
  }
  function histOf(code) { return HIST.get(code) || null; }

  // 纵轴范围跟着数据走：kmoni 原始值可能是 -3.0 到 2.x，按震度档画就成了一根压底的直线，
  // 看不出「有没有在动」。lo/hi 传进来就按它画，不传就按数据自适应。
  function sparkSvg(pts, note, lo, hi) {
    if (!pts || pts.length < 4) return '';
    const W = 214, H = 42, P = 3;
    let mn = Infinity, mx = -Infinity;
    for (const p of pts) { mn = Math.min(mn, p[1]); mx = Math.max(mx, p[1]); }
    if (lo === undefined) lo = Math.min(0, Math.floor(mn));
    if (hi === undefined) hi = Math.max(2, Math.ceil(mx));
    if (hi - lo < 1) hi = lo + 1;
    const X = i => P + (W - P * 2) * i / Math.max(1, pts.length - 1);
    const Y = r => H - P - (H - P * 2) * (Math.min(hi, Math.max(lo, r)) - lo) / (hi - lo);
    let d = '';
    for (let i = 0; i < pts.length; i++) {
      d += (i ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(pts[i][1]).toFixed(1);
    }
    const step = (hi - lo) > 5 ? 2 : 1;
    let grid = '';
    for (let r = Math.ceil(lo); r <= hi; r += step) {
      if (r === lo) continue;
      grid += '<line x1="' + P + '" y1="' + Y(r).toFixed(1) + '" x2="' + (W - P) +
        '" y2="' + Y(r).toFixed(1) + '"/>';
    }
    return '<div class="wave"><svg viewBox="0 0 ' + W + ' ' + H + '" preserveAspectRatio="none">' +
      '<g class="wv-grid">' + grid + '</g>' +
      '<path class="wv-line" d="' + d + '"/></svg>' +
      '<i>' + note + '</i></div>';
  }

  // 推算包络：按时间采样一遍 shakeLevel，横轴是发震后秒数
  function estWavePts(ev, st) {
    const est = estOf(ev, st);
    if (!est) return null;
    const mag = srcOf(ev).mag || 5, jp = usesShindo(ev);
    // 窗口要盖住这个站自己的 S 波到达（远站 90 秒才摇），否则整条线是平的
    const span = Math.max(30, Math.min(260, (est.ts || 0) + 45, Math.max(60, elapsedOf(ev) + 45)));
    const pts = [];
    for (let i = 0; i <= 45; i++) {
      const t = span * i / 45;
      const lv = shakeLevel(est, t, mag, jp);
      pts.push([t, !lv ? 0 : (lv.dim ? 0.35 : Math.min(9, lv.risk))]);
    }
    return pts;   // 横轴 0~span 秒（发震后）
  }

  function waveBlock(st, ev) {
    // 演练优先看「这一场会怎么摇」，别看现实世界的平静直线
    if (ev && ev.isSim) {
      const p0 = estWavePts(ev, st);
      if (p0) return sparkSvg(p0, '演练 · 震度包络（真值推算）');
    }
    if (st.country === 'JP' && !st.sea) {
      const a = histOf(st.code);
      if (a && a.length >= 6) {
        const t1 = a[a.length - 1][0];
        const pts = a.filter(p => t1 - p[0] <= 120);
        const hold = pts.length && (nowSec() - t1 > 8);   // 断流了就别假装还在更新
        if (pts.length >= 6 && !hold) {
          let mn = Infinity, mx = -Infinity;
          for (const p of pts) { mn = Math.min(mn, p[1]); mx = Math.max(mx, p[1]); }
          const lo = Math.min(-1, Math.floor(mn)), hi = Math.max(1.5, Math.ceil(mx * 2) / 2);
          return sparkSvg(pts, '实时 · 计测震度包络（近 2 分钟）', lo, hi);
        }
      }
    }
    if (ev && !state.historyEvent) {
      const pts = estWavePts(ev, st);
      if (pts) {
        return sparkSvg(pts, (ev.isSim ? '演练' : '推算') + ' · 震度包络（' +
          (ev.isSim ? '真值推算' : '衰减式') + '）');
      }
    }
    return '';
  }

  function stationInfoHtml(st) {
    const ev = eventForMap();
    const o = state.obs;
    const jp = st.country === 'JP';
    const obs = nearestObsPoint(st, 8);
    const age = o && o.origin_ts ? Math.max(0, nowSec() - o.origin_ts) : 1e9;
    const fresh = age < 1800;                 // 半小时内的电文才算「这一次」
    let h = '<b>' + st.name + '</b> <span class="dimx">' + (st.region || st.area || '') + '</span>';
    h += '<br><span class="dimx">' + (st.net || '代表点') + ' · ' + st.code + ' · ' +
      st.lat.toFixed(2) + '°N ' + st.lon.toFixed(2) + '°E' +
      (st.sea ? ' · 海底' + (st.depth_m ? ' ' + st.depth_m + ' m' : '') : '') + '</span>';
    if (st.sea) {
      h += '<br><span class="dimx">NIED 海底地震津波観測網 · 位置来自官方名册；' +
        'kmoni 的震度图不含海底符号，这里给的是推算震动</span>';
    }
    // ⓪ kmoni 实时计测震度（逐秒，NIED K-NET/KiK-net 才有）
    if (jp && kmoniFresh()) {
      const v = state.kmoni.lv[st.code];
      const [kx, ky] = kmPixel(st.lon, st.lat);
      const inFrame = kx >= 0 && ky >= 0 && kx < KMONI_IMG_W && ky < KMONI_IMG_H;
      if (v !== undefined) {
        const g = kmRiskOf(v);
        h += (v < 0.5)
          ? '<br><i class="dotv" style="background:#7f8b9e"></i>实时 <b>震度0</b>' +
            '<span class="dimx"> · kmoni 计测 ' + v.toFixed(1) + '</span>'
          : '<br><i class="dotv" style="background:' + vibeColor(g, 1) + '"></i>实时 <b>震度' +
            GRADE[g] + '</b><span class="dimx"> · kmoni 计测 ' + v.toFixed(1) + '</span>';
      } else if (!inFrame) {
        h += '<br><i class="dotv" style="background:#7f8b9e"></i><b>不在 kmoni 图幅内</b>' +
          '<span class="dimx"> · 图幅只到 30°N（冲绳/先岛无实时取色）</span>';
      }
    }
    // ① 观测状态：电文报到它 → 报的就是实测震度；没报到 → 震度 0（未达 1）；
    //    中国侧没有公开台站电文，只能标明「无实时数据」，不能假装成「没摇」。
    if (obs) {
      const risk = SCALE_RISK[obs.p.scale] || 0;
      h += '<br><i class="dotv" style="background:' + vibeColor(risk, 1) + '"></i>' +
        (fresh ? '实测 ' : '上次实测 ') + '<b>震度' + (obs.p.label || '?') + '</b>' +
        (obs.km > 0.8 ? '<span class="dimx"> · 观测点 ' + obs.km.toFixed(1) + ' km</span>' : '');
    } else if (jp) {
      h += fresh
        ? '<br><i class="dotv" style="background:#7f8b9e"></i>安静 <b>震度0</b>' +
          '<span class="dimx"> · 本次电文未报到（未达 1）</span>'
        : '<br><i class="dotv" style="background:#39d98a"></i>安静 <b>震度0</b>' +
          '<span class="dimx"> · 未观测到震动</span>';
    } else {
      h += '<br><i class="dotv" style="background:#7f8b9e"></i><b>无实时数据</b>' +
        '<span class="dimx"> · 中国暂无公开台站电文</span>';
    }
    if (o && o.origin_ts && jp) {
      const when = age < 90 ? Math.round(age) + ' 秒前'
        : age < 5400 ? Math.round(age / 60) + ' 分钟前' : (age / 3600).toFixed(1) + ' 小时前';
      h += '<br><span class="dimx">' + (fresh ? '电文 ' : '最近一次电文 ') +
        fmtHM(o.origin_ts, 9 * 3600) + '（' + when + '）· 最大 ' + (o.max_label || '—') + '</span>';
    } else if (jp) {
      h += '<br><span class="dimx">当前无震度速报 · 台网待机</span>';
    }
    // ② 有事件时补上震中距 / 预计震度 / 此刻是否被波扫到
    if (ev) {
      const est = estimate(ev, st.lat, st.lon);
      const lv = shakeLevel(est, elapsedOf(ev), ev.mag || 5, usesShindo(ev));
      if (est.risk >= 1 || lv) {
        h += '<br><span class="dimx">震中距</span> ' + Math.round(est.R) + ' km · ' +
          '<span class="dimx">预计</span> ' + (usesShindo(ev) ? '震度' : '') + est.label;
        if (lv) {
          h += '<i class="dotv" style="background:' + vibeColor(lv.risk, 1) +
            ';margin-left:7px"></i>正在摇晃';
        }
      }
    }
    h += waveBlock(st, ev);
    return h;
  }

  // auto：有进行中的预警看「预计」，空闲时看「观测」
  function stationMode(ev) {
    const p = state.stColor || 'auto';
    // 演练的震度速报一发，台站就不再按单站点亮（那是假的）
    if (state.obs && state.obs.sim) return 'idle';
    // 演练终报 15 虚拟秒后同样归灰：真实速报偶尔会把演练速報顶掉，
    // 不能因为顶掉了就让演练的台站彩点一直挂到过期
    if (ev && ev.isSim && simFinalAge(ev) >= 15) return 'idle';
    if (p === 'est') return ev ? 'est' : 'none';
    // kmoni 实时取色有效时优先用（真实观测，逐秒更新）；演练不放真地震，仍用推算
    if (kmoniFresh() && !(ev && ev.isSim)) return 'kmoni';
    if (p === 'obs') return 'obs';
    return ev ? 'est' : 'obs';
  }

  function stationModeText(mode) {
    if (mode === 'kmoni') {
      return '台站着色：NIED 实时震度（kmoni 逐秒）';
    }
    if (mode === 'idle') return '台站着色：震度速報（地域级，按都道府县上色）';
    if (mode === 'est') return '台站着色：预计震度（衰减式推算）';
    if (mode === 'obs') {
      const o = state.obs;
      if (!o) return '台站着色：观测（暂未收到震度速报）';
      const age = Math.max(0, nowSec() - (o.origin_ts || nowSec()));
      const when = age < 3600 ? Math.round(age / 60) + ' 分钟前' : (age / 3600).toFixed(1) + ' 小时前';
      return '台站着色：实测震度 · ' + o.place + ' M' + (o.mag || 0).toFixed(1) + ' · ' + when;
    }
    return '台站着色：无事件（灰点）';
  }

  // ---------------- 台站震度色阶 ----------------
  // 一档一色：颜色就是图例那排官方震度色（0 灰蓝 → 7 紫），越强越暖。
  // 以前是连续插值，0~1 两档挤在同一个绿里，所以「到了震度 1 的阈值也看不出
  // 颜色变过」。现在台站直接用图例那排官方震度十色（RISK_RGB），
  // 一档一个颜色：跨过哪一档的阈值，点就变成图例里那一档的颜色，一眼能对上。
  function gradeRGB(risk) {
    return RISK_RGB[Math.max(0, Math.min(9, risk | 0))];
  }
  function vibeRGB(risk) {
    const c = gradeRGB(risk);
    return [c[0], c[1], c[2]];
  }
  function vibeColor(risk, alpha) {
    const c = gradeRGB(risk);
    const a = alpha === undefined ? 0.62 + 0.04 * Math.max(0, Math.min(9, risk | 0)) : alpha;
    return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a.toFixed(2) + ')';
  }

  // ---- 微动（还没到震度 1）的连续色带：黑 → 蓝 → 绿 ----
  // 计测震度 JP_LIGHT(-2.2) 起从近黑的暗蓝慢慢透出来，-0.7 左右是中等蓝，
  // 0 附近转青绿，到震度 1 的门槛(0.5)是亮绿 —— 绿就是「这一带开始有动静」。
  // 震度 1 以上照旧接图例那排官方十色，配色一个都不改，只是多补了这段过渡。
  const MICRO_STOPS = [
    [-2.2, [12, 26, 56]],
    [-1.5, [22, 62, 155]],
    [-0.7, [37, 99, 235]],
    [0.0, [16, 185, 129]],
    [0.5, [52, 211, 90]],
  ];
  function microColor(v) {
    const t = clamp(v, JP_LIGHT, 0.5);
    let i = 0;
    while (i < MICRO_STOPS.length - 2 && t > MICRO_STOPS[i + 1][0]) i++;
    const a0 = MICRO_STOPS[i], a1 = MICRO_STOPS[i + 1];
    const k = clamp((t - a0[0]) / ((a1[0] - a0[0]) || 1), 0, 1);
    const c0 = a0[1], c1 = a1[1];
    const ch = j => Math.round(c0[j] + (c1[j] - c0[j]) * k);
    const u = clamp((t - JP_LIGHT) / (0.5 - JP_LIGHT), 0, 1);
    const al = 0.18 + 0.74 * Math.pow(u, 0.60);
    return {
      color: 'rgba(' + ch(0) + ',' + ch(1) + ',' + ch(2) + ',' + al.toFixed(2) + ')',
      r: 1.6 + 0.85 * u,
    };
  }

  // 某一刻台站的「瞬时震度档」：null = 还没摇 / 已经停了。
  // 档位是离散的：P 波扫到先亮「震度 0」（临界），S 波到达后从 0 一档一档
  // 升到预估峰值、再一档一档掉回去——所以能看见 震度0 → 1 → 2 → … 的过程。
  function shakeLevel(est, el, mag, jp) {
    if (el < est.tp) return null;
    if (el < est.ts) {
      // P 波初动：刚够震度 0 的临界值，先亮个空心的 0
      return jp ? gradeOf(0.3) : { risk: 1 };
    }
    const age = el - est.ts;
    if (age > JP_TAIL_MAX) return null;      // 尾波封顶：超过 ~7 分钟一律归灰
    // 摇晃包络 = 起振 → 平台 → 强震段衰减 → 尾段（coda）慢慢收。
    // 单段指数衰减时，M7 的强摇三四秒掉一半、半分钟就全灭，看着「一震就没」。
    // 真实强震：峰值能顶十几秒，之后还有很长的低频尾巴（震度1~2 能拖上一两分钟）。
    // v1.9.5 放慢过一档，v1.9.22 再放慢约一倍：
    //   ① 平台段拉长（hold 4+4·(M-5) → 5+4.5·(M-5)）；
    //   ② 主衰减时间常数 14+5.5·(M-4) → 18+6.5·(M-4)；
    //   ③ 距离项从 250km 封顶 2.0 提到 220km 就 2.0、最远 2.2（远场低频拖得更久）；
    //   ④ 尾波段（coda）时间常数 3τ → 4.5τ，切尾点 0.10 → 0.13（更早进慢尾）。
    // 合起来 M6.8 在 100km 处：强摇 ~13s 平台，之后 50% 衰减从 ~19s 拖到 ~44s，
    // 震度 1 级别的余晃能挂两三分钟，不再「一波过去图就黑」。
    const rise = clamp(0.8 + (mag - 5) * 0.2, 0.6, 2.2);
    const hold = clamp(5.0 + (mag - 5) * 4.5, 3.5, 32);
    const far = 1 + Math.min(1.2, (est.R || 0) / 220);   // 100km→1.45，220km→2.0，最远 2.2
    const tau1 = clamp(18 + (mag - 4) * 6.5, 12, 90) * far * (state.settings.decay || 1.6);
    const CODA = 0.13;                       // 切到慢尾段时的包络值
    let env;
    if (age < rise) env = 0.04 + 0.96 * Math.pow(age / rise, 1.4);
    else if (age < rise + hold) env = 1 - 0.05 * (age - rise) / hold;
    else {
      const t = age - rise - hold;
      const e1 = 0.95 * Math.exp(-t / tau1);
      const tCoda = tau1 * Math.log(0.95 / CODA);
      env = e1 > CODA ? e1 : CODA * Math.exp(-(t - tCoda) / (tau1 * 4.5));
    }
    const pg = (est.pgv || 0) * env;
    if (jp) {
      const v = shindoVal(pg);
      // 点亮门槛压到计测震度 -1.5（kmoni 那套 -3.0~7.0 的标尺里 -1.5 已经是
      // 很轻的微动）。-1.5 一过就点一盏极淡的蓝，一路加到 0.5 转正蓝的震度 1，
      // 再往后 1→2→3… 一档一档加强。这样「刚有一点点动静」也能在图上看出来。
      if (v < JP_LIGHT) return null;
      if (v < 0.5) return { risk: 0, dim: true, low: v };
      return gradeOf(Math.max(v, 0.5));
    }
    const du = duOf(pg);
    if (du < 0.9) return null;
    if (du < 1.5) return { risk: 0, dim: true };
    const risk = riskOfDu(du);
    if (risk < 1) return { risk: 0, dim: true };
    return { risk };
  }

  // ---- 台站徽章：圆底 + 震度数字 ----
  // 一帧可能有上千个台站要标数字，直接 fillText 会明显掉帧；这里按
  // 「档位 × 半径 × dpr」预渲染成离线小图，绘制时只做一次 drawImage。
  // 颜色一律沿用图例那套官方震度十色，数字只是叠在色块上，不改变配色。
  const BADGE = new Map();
  const BADGE_TXT = ['0', '1', '2', '3', '4', '5-', '5+', '6-', '6+', '7'];
  function badgeOf(g, r) {
    const dpr = view.dpr || 1;
    const key = g + ':' + r + ':' + dpr;
    const hit = BADGE.get(key);
    if (hit) return hit;
    const pad = 1.8;
    const W = Math.ceil((r + pad) * 2);
    const cv = document.createElement('canvas');
    cv.width = Math.max(2, Math.ceil(W * dpr));
    cv.height = Math.max(2, Math.ceil(W * dpr));
    const c2 = cv.getContext('2d');
    c2.setTransform(dpr, 0, 0, dpr, 0, 0);
    const cx = W / 2, cy = W / 2;
    const col = RISK_RGB[Math.max(0, Math.min(9, g | 0))] || RISK_RGB[1];
    // 深色描边打底，浅色档（黄/绿）在亮陆地上也分得清边界
    c2.beginPath(); c2.arc(cx, cy, r, 0, Math.PI * 2);
    c2.fillStyle = 'rgb(' + col[0] + ',' + col[1] + ',' + col[2] + ')';
    c2.fill();
    c2.lineWidth = 1.2;
    c2.strokeStyle = 'rgba(6,10,18,.72)';
    c2.stroke();
    const txt = BADGE_TXT[g] || String(g);
    const wide = txt.length > 1;
    // 字体用系统里一定有的几款（以前写 ui-sans-serif，canvas 会退化到默认衬线，
    // 数字看着又细又歪）；数字用 800 粗体、双字符档缩小一点并收紧字距。
    c2.font = '800 ' + (wide ? r * 0.98 : r * 1.30).toFixed(1) +
      'px "Segoe UI","Microsoft YaHei",Arial,sans-serif';
    if ('letterSpacing' in c2) c2.letterSpacing = wide ? '-0.4px' : '0px';
    c2.textAlign = 'center';
    c2.textBaseline = 'middle';
    c2.lineWidth = Math.max(1.3, r * 0.26);
    c2.strokeStyle = 'rgba(6,10,18,.50)';
    c2.strokeText(txt, cx, cy + r * 0.04);
    c2.fillStyle = '#fff';
    c2.fillText(txt, cx, cy + r * 0.04);
    const b = { cv, w: W };
    BADGE.set(key, b);
    if (BADGE.size > 160) BADGE.delete(BADGE.keys().next().value);
    return b;
  }

  // ---------------- 台湾 P-Alert 测站 ----------------
  // 786 个测站，10 秒一帧的实时 PGA（gal）。日本那边是 kmoni 的每秒 GIF，
  // 台湾这边走这个源——两边都是「测站级实测」，视觉上统一用震度十色。
  // PGA → 计测震度用 JMA 的经验近似，够看档位，不追求小数点后两位。
  function gaLv(ga) {
    if (!(ga > 0.2)) return 0;
    return clamp(2.0 * Math.log10(ga) + 0.7, 0, 7);
  }
  // 台湾同一时刻只画一个测站源：P-Alert 与 TREM 覆盖的是同一片地方，
  // 两套点叠在一起既看不清也会互相盖色。谁延迟低谁上，另一个让位（每 10 秒评一次）。
  function obsPrimaryId(country) {
    const o = (state.primary || {}).obs || {};
    return ((o[country] || {}).id) || '';
  }

  function drawPalert() {
    const p = state.palert;
    const list = state.palertStations;
    if (!p || !list || !list.length) return;
    const cur = obsPrimaryId('TW');
    if (cur && cur !== 'palert') return;     // 这一轮 TREM 更快 → P-Alert 让位
    const vals = p.vals || {};
    const stale = nowSec() - (p.at || 0);
    if (stale > 120) return;                 // 断流两分钟就别拿旧值冒充实时
    ctx.save();
    ctx.globalAlpha = stale > 30 ? 0.55 : 1;
    for (let i = 0; i < list.length; i++) {
      const s = list[i];
      const x = px(s.x), y = py(s.y);
      if (x < -14 || y < -14 || x > view.w + 14 || y > view.h + 14) continue;
      const ga = vals[s.c];
      if (ga === undefined) {                // 这一帧没上报：留个待机暗点，看得出台网在哪
        ctx.fillStyle = 'rgba(136,168,204,.16)';
        ctx.beginPath(); ctx.arc(x, y, 1.35, 0, Math.PI * 2); ctx.fill();
        continue;
      }
      const lv = gaLv(ga);
      if (lv < 0.5) {
        ctx.fillStyle = 'rgba(136,168,204,.26)';
        ctx.beginPath(); ctx.arc(x, y, 1.5, 0, Math.PI * 2); ctx.fill();
        continue;
      }
      const risk = clamp(gradeOf(lv).risk, 0, 9);
      const r = 2.1 + Math.max(0, lv - 1) * 0.62;
      if (lv >= 4.5) {                       // 震度5弱以上给个光晕，扫一眼就知道哪里在摇
        ctx.beginPath(); ctx.arc(x, y, r * 2.4, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(' + RISK_RGB[risk].join(',') + ',.16)';
        ctx.fill();
      }
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = RISK[risk];
      ctx.fill();
      ctx.lineWidth = 0.9;
      ctx.strokeStyle = 'rgba(6,10,16,.55)';
      ctx.stroke();
    }
    ctx.restore();
  }

  // ---------------- 双地实时最大震度（右上角） ----------------
  // 日本那栏取 kmoni 的逐站计测震度，台湾那栏取 P-Alert 的 PGA 换算成震度。
  // 平时灰着，真在摇才着色——扫一眼就知道两边哪边在动、动了多大。
  const TW_AREA = {
    NewTaipei: '新北', Tainan: '台南', Nantou: '南投', Chiayi: '嘉義', Hsinchu: '新竹',
    Kaohsiung: '高雄', Taitung: '台東', Taichung: '台中', Hualien: '花蓮',
    Pingtung: '屏東', Ilan: '宜蘭', Miaoli: '苗栗', Taipei: '台北', Yunlin: '雲林',
    Changhua: '彰化', Taoyuan: '桃園', Keelung: '基隆',
  };
  const lmKey = { k: '' };

  function paintNet(jv, jp) {
    const box = document.getElementById('lmNet');
    const val = document.getElementById('lmNetVal');
    const em = document.getElementById('lmNetPlace');
    if (!box || !val || !em) return;
    const lv = state.kmoni.lv || {};
    const n = kmoniFresh() ? Object.keys(lv).length : 0;
    let hits = 0;
    if (n) for (const k in lv) if (lv[k] >= SHAKE.lv) hits++;
    const a = state.autoEv;
    const autoFresh = a && nowSec() - (a.report_ts || 0) < AUTO.holdSec;
    box.classList.toggle('live', n > 0);
    box.classList.remove('hot');

    if (!n) {
      val.textContent = '—';
      val.style.color = '#7f8b9e';
      em.textContent = '台站数据中断';
      return;
    }
    val.textContent = (jv > -9 ? jv.toFixed(1) : '—');
    val.style.color = jv >= SHAKE.lv ? '#4ade80' : '#7f8b9e';
    if (autoFresh) {
      // 推算比「绿框几个」重要，优先显示
      em.textContent = '推算 M' + (a.mag || '?') + ' · ' + (a.stations || 0) + '站';
    } else if (hits >= SHAKE.minSt) {
      em.textContent = '绿框 ' + hits + ' · 待发展';
    } else {
      em.textContent = n + ' 站 · 静稳';
    }
    box.title = 'kmoni 取色 ' + n + ' 站（台湾另计）· 峰值 ' +
      (jv > -9 ? jv.toFixed(1) : '—') + ' · 过 ' + SHAKE.lv + ' 的 ' + hits + ' 站' +
      (jp ? ' · 最强在 ' + jp : '');
  }

  // 一行实时震度：label 为 null 表示当前没有可显示的值（静稳/断流）
  function paintLmRow(key, label, risk, sub, tip) {
    const box = document.getElementById('lm' + key);
    const val = document.getElementById('lm' + key + 'Val');
    const em = document.getElementById('lm' + key + 'Place');
    if (!box || !val || !em) return;
    box.title = tip || '';
    if (label == null) {
      box.classList.remove('hot', 'live');
      val.textContent = '—';
      val.style.color = '#7f8b9e';
      em.textContent = sub || '静稳';
      return;
    }
    val.textContent = label;
    val.style.color = (RISK && RISK[risk]) || '#eaf4ff';
    em.textContent = sub || '';
    box.classList.toggle('hot', risk >= 5);
    box.classList.toggle('live', risk >= 1);
  }

  function updateLiveMax() {
    if (!document.getElementById('liveMax')) return;
    const o = state.obs;
    const tw = state.eew && state.eew.TW;
    const kk = [state.kmoni.ts || '', (state.palert && state.palert.at) || '',
      (o && o.report_ts) || '', (tw && tw.report_ts) || ''].join('|');
    if (kk === lmKey.k) return;
    lmKey.k = kk;

    // ---- 日本：官方震度速報/震度观测优先，kmoni 实时只做补充 ----
    let jv = -99, jp = '';
    if (kmoniFresh()) {
      const lv = state.kmoni.lv || {};
      for (let i = 0; i < state.stations.length; i++) {
        const st = state.stations[i];
        if (st.country !== 'JP') continue;
        const v = lv[st.code];
        if (v === undefined || v <= jv) continue;
        jv = v;
        jp = (st.region || st.name || '');
      }
    }
    const obsFresh = o && o.max_scale >= 10 &&
      nowSec() - (o.report_ts || o.origin_ts || 0) < 1800;
    let jLabel = null, jRisk = 0, jSub = '', jTip = '';
    if (obsFresh) {
      jRisk = SCALE_RISK[o.max_scale] || 0;
      jLabel = o.max_label || (GRADE[jRisk] && GRADE[jRisk].label) || '?';
      const src = o.source === 'jma_sokuho' ? '速報' : '实测';
      jSub = src + ' · ' + (o.place || '震源調査中');
      jTip = '气象厅 ' + (o.source === 'jma_sokuho' ? '震度速報' : '震度观测') +
        ' · ' + (o.place || '震源調査中') + ' · 最大震度 ' + jLabel +
        (jp ? ' · kmoni 实时峰值 ' + (jv > -9 ? jv.toFixed(1) : '—') + '（' + jp + '）' : '');
    }
    if (kmoniFresh() && jv > -9) {
      const g = gradeOf(jv);
      // 官方还没更新、但现场已经摇得更强时，先按实时显示（差一档以上才抢）
      if (!obsFresh || g.risk > jRisk + 1) {
        jLabel = g.code; jRisk = g.risk;
        jSub = (jp || '—') + ' · 实时';
        jTip = 'kmoni 实时峰值 ' + jv.toFixed(1) + ' · ' + (jp || '') +
          (obsFresh ? '（官方报 ' + o.max_label + '）' : '');
      }
    }
    paintLmRow('Jp', jLabel, jRisk, jSub || '静稳', jTip);

    // 台网自检行：站数 / 峰值 / 绿框数 / 推算状态
    paintNet(jv, jp);

    // ---- 台湾：CWA 速报优先，P-Alert 实时补充 ----
    let tv = -99, tp = '';
    const p = state.palert;
    if (p && nowSec() - (p.at || 0) < 120) {
      const vals = p.vals || {};
      for (let i = 0; i < state.palertStations.length; i++) {
        const st = state.palertStations[i];
        const ga = vals[st.c];
        if (ga === undefined) continue;
        const v = gaLv(ga);
        if (v <= tv) continue;
        tv = v;
        tp = TW_AREA[st.a] || st.a || st.n || '';
      }
    }
    const twFresh = tw && tw.max_intensity && !tw.cancel && tw.active !== false &&
      nowSec() - (tw.report_ts || tw.origin_ts || 0) < 900;
    let tLabel = null, tRisk = 0, tSub = '', tTip = '';
    if (twFresh) {
      tLabel = tw.max_intensity;
      tRisk = LAB_RISK[String(tLabel).trim()];
      if (tRisk === undefined) tRisk = 0;
      tSub = 'CWA · ' + (tw.place || '');
      tTip = '台湾中央气象署 CWA 地震速报 · ' + (tw.place || '') + ' · 最大震度 ' + tLabel;
    }
    if (tv > -9) {
      const g = gradeOf(tv);
      if (!twFresh || g.risk > tRisk + 1) {
        tLabel = g.code; tRisk = g.risk;
        tSub = (tp || '—') + ' · P-Alert';
        tTip = 'P-Alert 实时 PGA 换算震度 ' + g.code + ' · ' + (tp || '');
      }
    }
    paintLmRow('Tw', tLabel, tRisk, tSub || '静稳', tTip);
  }

  // ---------------- 摇晃提示框（不用计算的那种） ----------------
  // 两个台站到计测震度 0.7（震度1 的下沿）就先亮绿框，告诉人「这边在动」。
  // 这一步**不做任何反演**——只是视觉提示，够不上「7 站聚拢 + 两站 1.5」的推算门槛。
  // 真发展起来（站更多、两站过 1.5），autoHypo 那套才会接管去找震源。
  const SHAKE = { lv: 0.7, minSt: 2, hz: 4.6, max: 14 };

  let hitKey = '', hitAt = 0, hitCache = [];
  function shakeHits(ev) {
    const hits = [];
    if (kmoniFresh()) {
      const lv = state.kmoni.lv || {};
      for (let i = 0; i < state.stations.length; i++) {
        const st = state.stations[i];
        if (st.country !== 'JP') continue;
        const v = lv[st.code];
        if (v === undefined || v < SHAKE.lv) continue;
        hits.push({ lat: st.lat, lon: st.lon, v: v });
      }
    }
    const p = state.palert;
    if (p && nowSec() - (p.at || 0) < 120) {
      const vals = p.vals || {};
      for (let i = 0; i < state.palertStations.length; i++) {
        const s = state.palertStations[i];
        const ga = vals[s.c];
        if (ga === undefined) continue;
        const v = gaLv(ga);
        if (v < SHAKE.lv) continue;
        hits.push({ lat: s.y, lon: s.x, v: v });
      }
    }
    if (hits.length) return hits;
    // 没有实测数据（kmoni 没帧、P-Alert 没值）时，推算/演练也照给绿框：
    // 每 0.5 秒算一次，别每帧把 2000 多个站重算一遍。
    if (!ev || state.historyEvent) return hits;
    const key = ev.uid + '#' + (ev.serial || 0);
    const now = nowSec();
    if (key === hitKey && now - hitAt < 0.5) return hitCache;
    const el = elapsedOf(ev), mag = srcOf(ev).mag || 5, jp = usesShindo(ev);
    const out = [];
    for (const st of state.stations) {
      const lv = shakeLevel(estOf(ev, st), el, mag, jp);
      if (!lv || lv.risk < 1) continue;
      out.push({ lat: st.lat, lon: st.lon, v: SHAKE.lv + lv.risk });
    }
    hitKey = key; hitAt = now; hitCache = out;
    return out;
  }

  // 演练：把「已经报到摇了」的台站一个个点亮（服务端每报下发 sim_det）。
  // 这就是「先有台站反应、才有震源估算」的可视化——第一报之前图上什么都没有，
  // 台站陆续亮起来之后，震中标靶才跟着解算结果跳出来。
  function drawSimDetected(ev) {
    const det = ev && ev.sim_det;
    if (!det || !det.length) return;
    // 演练速報一出，地图交给县色块，这些点立刻退场；
    // 没到速報的话，终报后也按虚拟秒淡出（4s 起淡、12s 退完），
    // 否则这堆彩点会一直挂到演练过期，看起来就是「完事了还有黄点」。
    if (state.obs && state.obs.sim) return;
    const age = simFinalAge(ev);
    if (age >= 12) return;
    const fade = age > 4 ? clamp(1 - (age - 4) / 8, 0, 1) : 1;
    const buckets = [];
    for (let i = 0; i < det.length; i++) {
      const p = det[i];
      const x = px(p[1]), y = py(p[0]);
      if (x < -10 || y < -10 || x > view.w + 10 || y > view.h + 10) continue;
      const g = gradeOf(p[2]);
      if (!g.risk) continue;
      const r = 1.5 + Math.min(2.8, g.risk * 0.42);
      (buckets[g.risk] || (buckets[g.risk] = [])).push(x, y, r);
    }
    ctx.save();
    ctx.globalAlpha = 0.92 * fade;
    for (let risk = 1; risk < buckets.length; risk++) {
      const b = buckets[risk];
      if (!b || !b.length) continue;
      const c = RISK_RGB[risk] || RISK_RGB[1];
      ctx.beginPath();
      for (let k = 0; k < b.length; k += 3) {
        ctx.moveTo(b[k] + b[k + 2], b[k + 1]);
        ctx.arc(b[k], b[k + 1], b[k + 2], 0, Math.PI * 2);
      }
      ctx.fillStyle = 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
      ctx.fill();
    }
    ctx.restore();
  }

  // 绿框一出现就把镜头推过去：等它连续存在 1.2 秒（排除瞬时噪声），
  // 6 秒内不重复推，且已经看得清就不再动——避免镜头自己抽搐。
  function autoFrameShake(hits) {
    const now = nowSec();
    if (!hits.length || hits.length < SHAKE.minSt) { state.shakeSince = 0; return; }
    if (!state.shakeSince) state.shakeSince = now;
    if (now - state.shakeSince < 1.2) return;
    if (!canAutoView() || vTween) return;
    let sx = 0, sy = 0;
    for (const h of hits) { sx += h.lon; sy += h.lat; }
    const cLat = sy / hits.length, cLon = sx / hits.length;
    let rad = 0;
    for (const h of hits) rad = Math.max(rad, haversine(cLat, cLon, h.lat, h.lon));
    // 范围太大（一整片都在微动）或站太多时不追——那种情况缩进去反而看不见全局
    if (hits.length > 60 || rad > 220) { state.shakeSince = 0; return; }
    rad = Math.max(25, rad * 1.15);                 // 留一圈边
    const deg = rad / KM_PER_DEG;
    const needS = 0.22 * Math.min(view.w, view.h) / Math.max(1e-6, deg * D2R);
    const cap = homeScale() * 10;                   // 最多推到「县」一级，别再往里钻
    const target = clamp(needS, view.fit * 0.9, Math.min(cap, view.fit * 80));
    const cx = px(cLon), cy = py(cLat);
    const inside = cx > view.w * 0.12 && cx < view.w * 0.88 &&
      cy > view.h * 0.12 && cy < view.h * 0.88;
    const coverPx = deg * D2R * view.s;             // 当前画面上这撮站的半径
    if (inside && coverPx > Math.min(view.w, view.h) * 0.18) return;
    if (now - (state.shakeFrameAt || 0) < 6) return;
    state.shakeFrameAt = now;
    flyTo(cLat, cLon, Math.max(view.s, target), 650);
  }

  function drawShakeHints(ev) {
    if (!state.layers.stations && state.layers.palert === 0) return;
    const hits = shakeHits(ev);
    state.shakeN = hits.length;
    if (!ev) { try { autoFrameShake(hits); } catch (e) { } }
    if (hits.length < SHAKE.minSt) return;   // 单站跳一下不算，至少两个站同时
    // 东亚视野下日本只有几十像素宽，几十个框会糊成一团绿。按强度取前 N 个，
    // 且彼此屏幕距离不足 9px 的只留最强那个——放大后照样一个站一个框。
    hits.sort((a, b) => b.v - a.v);
    const drawn = [];
    const pick = [];
    for (let i = 0; i < hits.length && pick.length < SHAKE.max; i++) {
      const h = hits[i];
      const hx = px(h.lon), hy = py(h.lat);
      let dup = false;
      for (let j = 0; j < drawn.length; j++) {
        if (Math.abs(drawn[j][0] - hx) < 9 && Math.abs(drawn[j][1] - hy) < 9) { dup = true; break; }
      }
      if (dup) continue;
      drawn.push([hx, hy]);
      pick.push(h);
    }
    // 闪烁：|sin| 让框明暗跳，比单纯透明度渐变更像「报警」
    const blink = 0.35 + 0.65 * Math.abs(Math.sin(nowSec() * SHAKE.hz));
    ctx.save();
    ctx.globalAlpha = blink;
    ctx.strokeStyle = '#4ade80';
    ctx.lineWidth = 1.9;
    ctx.lineJoin = 'round';
    for (let i = 0; i < pick.length; i++) {
      const h = pick[i];
      const x = px(h.lon), y = py(h.lat);
      if (x < -20 || y < -20 || x > view.w + 20 || y > view.h + 20) continue;
      const r = 7 + Math.min(5, (h.v - SHAKE.lv) * 1.8);
      // 圆角方框：四角小圆弧，比直角更像「框住那个站」
      const w = r * 2, k = 2.4;
      ctx.beginPath();
      ctx.moveTo(x - r + k, y - r);
      ctx.lineTo(x + r - k, y - r); ctx.quadraticCurveTo(x + r, y - r, x + r, y - r + k);
      ctx.lineTo(x + r, y + r - k); ctx.quadraticCurveTo(x + r, y + r, x + r - k, y + r);
      ctx.lineTo(x - r + k, y + r); ctx.quadraticCurveTo(x - r, y + r, x - r, y + r - k);
      ctx.lineTo(x - r, y - r + k); ctx.quadraticCurveTo(x - r, y - r, x - r + k, y - r);
      ctx.closePath();
      ctx.stroke();
    }
    ctx.restore();
  }

  function drawStations(ev, colorBy) {
    const mode0 = stationMode(colorBy ? ev : null);
    // 有实测电文就画实测测站（电文报到哪几个就画哪几个）
    const real = realStations(colorBy ? ev : null);
    if (real && mode0 !== 'kmoni') { drawRealStations(real, ev); return; }
    const mode = mode0;
    const el = ev ? elapsedOf(ev) : 0;
    const mag = ev ? (srcOf(ev).mag || 5) : 5;   // 摇晃包络按真值震级，不跟着估算走
    const jp = ev ? usesShindo(ev) : true;
    // 台网有 1900+ 个点：同款式的暗点合并成一条路径，一次画完，不然掉帧
    const batch = new Map();
    const flush = () => {
      for (const [style, pts] of batch) {
        const i1 = style.indexOf('|'), i2 = style.indexOf('|', i1 + 1);
        const col = style.slice(0, i1), r = +style.slice(i1 + 1, i2), hollow = style[i2 + 1] === '1';
        ctx.beginPath();
        for (let k = 0; k < pts.length; k += 2) {
          const x = pts[k], y = pts[k + 1];
          ctx.moveTo(x + r, y);
          ctx.arc(x, y, r, 0, Math.PI * 2);
        }
        if (hollow) { ctx.strokeStyle = col; ctx.lineWidth = 1.1; ctx.stroke(); }
        else { ctx.fillStyle = col; ctx.fill(); }
      }
      batch.clear();
    };
    const push = (x, y, color, r, hollow) => {
      const key = color + '|' + r + '|' + (hollow ? 1 : 0);
      let a = batch.get(key);
      if (!a) batch.set(key, a = []);
      a.push(x, y);
    };
    const sea = [];                            // 海底站：单独画方点
    const lvCnt = new Array(10).fill(0);      // 各档台站数，供调试观察衰减
    window.__lvCount = lvCnt;
    for (const st of state.stations) {
      const x = px(st.lon), y = py(st.lat);
      if (x < -20 || y < -20 || x > view.w + 20 || y > view.h + 20) continue;

      let color = 'rgba(150,182,220,.30)', r = 2.2, hollow = false, hot = false, numG = -1;

      if (mode === 'idle') {
        // 震度速报已是「地域」级：台站只当底网点缀，不冒充每个点都测到了
        const col = st.country === 'JP' ? 'rgba(136,168,204,.26)' : 'rgba(132,164,200,.20)';
        if (st.sea) { sea.push([x, y, col, 1.9, 0, -1]); continue; }
        push(x, y, col, 1.9, st.country !== 'JP');
        continue;
      }

      if (mode === 'kmoni') {
        // NIED 实时观测：每个台站每秒都有计测震度。有数据就按档位上色，
        // 平静(-3~0.5)画暗点待机——这正是「平时也能看出测站有没有震动」。
        const v = state.kmoni.lv[st.code];
        if (st.country !== 'JP' || v === undefined) {
          if (st.country === 'JP') { color = 'rgba(136,168,204,.34)'; r = 2.0; }
          else { hollow = true; color = 'rgba(132,164,200,.42)'; r = 2.0; }
        } else if (v < JP_LIGHT) {
          color = 'rgba(136,168,204,.40)';   // 真·平静：暗点待机
          r = 2.0;
        } else if (v < 0.5) {
          // 微动：JP_LIGHT 以上先点亮，黑→蓝→绿连续变亮变大
          const m = microColor(v);
          color = m.color; r = m.r;
        } else {
          const g = kmRiskOf(v);
          color = vibeColor(g);
          r = Math.round((2.0 + g * 0.32) * 2) / 2;
          hot = g >= 5;
          numG = g;
        }
      } else if (mode === 'est' && ev) {
        const est = estOf(ev, st);
        const lv = shakeLevel(est, el, mag, jp);
        if (!lv) {
          // 还没被波扫到 / 已经退干净 —— 画成待机灰点，不要直接 continue。
          // 以前这里一 continue，整场演练地图上只剩震中周围一圈彩色，
          // 日本其余地方一片空白，看着既像「没测站」也像「衰减得飞快」。
          color = st.country === 'JP' ? 'rgba(136,168,204,.30)' : 'rgba(132,164,200,.24)';
          r = 2.0;
          hollow = st.country !== 'JP';
        } else {
        // 颜色直接跟震度档走：震度0 = 绿（临界），每升一档换一个更亮的颜色，
        // 档位是离散的，颜色也离散——跨过哪档阈值就换成图例里那档的颜色。
        const g = Math.max(0, Math.min(9, lv.risk | 0));
        lvCnt[g]++;
        if (el < est.ts) {
          hollow = true;                 // 初动：空心圈 = 震度 0 临界
          color = vibeColor(g, 0.85);
          r = 2.4;
        } else if (lv.dim) {
          // 微摇（计测震度 JP_LIGHT~0.5，还没到震度 1）：黑→蓝→绿连续过渡，
          // 亮度和点径都跟着数值长——「亮了 → 逐步加强」这一段能看出来。
          const m = microColor(lv.low === undefined ? JP_LIGHT : lv.low);
          color = m.color; r = m.r;
        } else {
          color = vibeColor(g);
          r = Math.round((2.0 + g * 0.32) * 2) / 2;
          hot = g >= 5;
          numG = g;
        }
        }
      } else if (mode === 'obs') {
        const o = obsOf(st);
        if (o) {
          const risk = SCALE_RISK[o.scale] || 0;
          color = vibeColor(risk);
          r = Math.round((2.2 + risk * 0.3) * 2) / 2;
          hot = risk >= 5;
          numG = risk;
        } else if (st.country === 'JP') {
          color = 'rgba(136,168,204,.34)';
          r = 2.0;
        } else {
          hollow = true;
          color = 'rgba(132,164,200,.42)';
          r = 2.0;
        }
      }

      // 海底站：颜色/强度照上面算，但不画圆点——它们是埋在海床上的，
      // 和水面站点不是一类东西，留到最后统一画成方块。
      if (st.sea) { sea.push([x, y, color, r, hot ? 1 : 0, numG]); continue; }

      // 震度 1 以上：画「圆底 + 数字」的徽章，半径放大到能塞下字
      if (numG >= 1) {
        flush();
        const rr = 5.4 + numG * 0.74;          // 震度1 → 6.1px，震度7 → 10.6px
        if (hot) {
          ctx.beginPath();
          ctx.arc(x, y, rr + 3.2, 0, Math.PI * 2);
          ctx.globalAlpha = .20;
          ctx.fillStyle = color;
          ctx.fill();
          ctx.globalAlpha = 1;
        }
        const b = badgeOf(numG, rr);
        ctx.drawImage(b.cv, x - b.w / 2, y - b.w / 2, b.w, b.w);
        continue;
      }

      if (!hot) { push(x, y, color, r, hollow); continue; }

      flush();
      ctx.beginPath();
      ctx.arc(x, y, r + 2.6, 0, Math.PI * 2);
      ctx.globalAlpha = .22;
      ctx.fillStyle = color;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,.55)';
      ctx.lineWidth = .9;
      ctx.stroke();
    }
    flush();

    // 海底台站（S-net / DONET / N-net / 相模湾，243 站）：方点。
    // 平时是个半透明小方框，摇起来按同一套震度色填充、放大，强震描白边。
    if (sea.length) {
      for (const q of sea) {
        const s2 = Math.max(2.1, q[3] * 1.02);
        ctx.beginPath();
        ctx.rect(q[0] - s2, q[1] - s2, s2 * 2, s2 * 2);
        ctx.fillStyle = q[2];
        ctx.fill();
        // 方点常驻一道细边：太小的时候圆点/方点看着都一样，有了边才认得出是「海里的站」
        ctx.strokeStyle = q[4] ? 'rgba(255,255,255,.75)' : 'rgba(150,182,214,.55)';
        ctx.lineWidth = q[4] ? 1 : .7;
        ctx.stroke();
      }
    }

    // kmoni 模式下，气象厅电文观测点仍然叠在最上层（带地名标签，更醒目）
    if (real && mode === 'kmoni') drawRealStations(real, ev);
  }

  let OCC = [];
  function drawPrefLabels(ev) {
    const blocks = prefBlocks(ev);
    if (!blocks) return;
    const idx = prefIndex();
    const z = view.s / (view.fit || 1);
    if (z < 1.2) return;
    const ex = px(ev.lon), ey = py(ev.lat);
    ctx.font = '700 9.5px ui-monospace,monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const list = Object.keys(blocks).map(n => {
      const p = idx[n];
      if (!p) return null;
      const b = blocks[n];
      return { p, b, risk: b.risk != null ? b.risk : gradeOf(b.value).risk };
    }).filter(Boolean).sort((a, b) => b.risk - a.risk);

    for (const it of list) {
      if (it.risk < 1) continue;
      const x = px(it.p.c[0]), y = py(it.p.c[1]);
      if (x < 26 || y < 62 || x > view.w - 26 || y > view.h - 46) continue;
      if (Math.hypot(x - ex, y - ey) < 42) continue;
      const r = 8;
      if (OCC.some(b => !(x + r < b[0] || x - r > b[0] + b[2] || y + r < b[1] || y - r > b[1] + b[3]))) continue;
      OCC.push([x - r, y - r, r * 2, r * 2]);
      ctx.beginPath();
      ctx.arc(x, y, r, 0, Math.PI * 2);
      ctx.fillStyle = PAL.chip;
      ctx.fill();
      ctx.strokeStyle = RISK[it.risk];
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.fillStyle = RISK[it.risk];
      ctx.fillText(it.b.label, x, y + .5);
    }
    ctx.textAlign = 'left';
    ctx.textBaseline = 'alphabetic';
  }

  function drawEpicenter(ev, active) {
    const x = px(ev.lon), y = py(ev.lat);
    const pulse = active ? (Math.sin(performance.now() / 320) + 1) / 2 : 0;
    ctx.save();
    ctx.strokeStyle = active ? 'rgba(255,80,60,.9)' : 'rgba(255,140,110,.5)';
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(x, y, 8 + pulse * 8, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.arc(x, y, 4.2, 0, Math.PI * 2);
    ctx.fillStyle = active ? '#ff4a35' : '#c9503c'; ctx.fill();
    ctx.beginPath(); ctx.arc(x, y, 1.5, 0, Math.PI * 2);
    ctx.fillStyle = '#fff'; ctx.fill();
    ctx.strokeStyle = 'rgba(255,90,70,.45)';
    ctx.lineWidth = .9;
    ctx.beginPath();
    ctx.moveTo(x - 17, y); ctx.lineTo(x - 7, y);
    ctx.moveTo(x + 7, y); ctx.lineTo(x + 17, y);
    ctx.moveTo(x, y - 17); ctx.lineTo(x, y - 7);
    ctx.moveTo(x, y + 7); ctx.lineTo(x, y + 17);
    ctx.stroke();
    ctx.restore();

    const el0 = active ? elapsedOf(ev) : 0;
    const dep = Math.max(0, ev.depth || 0);
    // 深源地震：波还在地下爬，地表暂时没有圈。把「还要等多久」直接写在震中标上，
    // 不然看着就是「深 800km 和深 10km 一模一样」。
    let deep = '';
    if (dep >= 100) {
      // 演练的波前按服务端等效速度走，深源提示也用同一个速度，别一个用分层模型
      const waitP = travelInfo(0, dep, ttModelFor(ev)).p.t - el0;
      deep = waitP > 1
        ? ' · 深' + Math.round(dep) + 'km（P 波 ' + Math.ceil(waitP) + 's 到地表）'
        : ' · 深' + Math.round(dep) + 'km';
    }
    // 演练：靶标画在「反演出来的震中」上，不是真震中——真震源是要藏起来的。
    const label = (ev.isSim ? '演练 · 推定震源 · ' : ev.isHist ? '历史 · ' : (ev.active === false ? '最近 · ' : '')) +
      String(ev.place || '').replace(/（演练）/g, '').replace(/\(演练\)/g, '') + deep;
    ctx.font = '600 11px system-ui,"PingFang SC","Hiragino Sans",sans-serif';
    const w = ctx.measureText(label).width;
    const bx = x + 13, by = y - 26;
    ctx.fillStyle = PAL.tagBg;
    ctx.strokeStyle = 'rgba(255,90,70,.22)';
    ctx.lineWidth = 1;
    roundRect(ctx, bx, by, w + 14, 19, 6);
    ctx.fill(); ctx.stroke();
    ctx.fillStyle = ev.isSim ? '#ffc46b' : PAL.tagFg;
    ctx.fillText(label, bx + 7, by + 13);
  }

  // 多事件适配：其它同时进行的地震用小号震中标出（主事件仍在中心带波前/色块）
  function drawOtherEvents(focused) {
    const list = activeEvents();
    if (list.length < 2) return;
    const fu = focused ? focused.uid : '';
    let n = 0;
    for (const e of list) {
      if (e.uid === fu) continue;
      if (++n > 5) break;
      const x = px(e.lon), y = py(e.lat);
      if (x < -30 || x > view.w + 30 || y < -30 || y > view.h + 30) continue;
      ctx.save();
      ctx.strokeStyle = 'rgba(255,146,112,.8)';
      ctx.lineWidth = 1.1;
      ctx.beginPath(); ctx.arc(x, y, 5.6, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.arc(x, y, 2.4, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,116,84,.92)'; ctx.fill();
      const txt = 'M' + (e.mag || 0).toFixed(1) + ' ' + String(e.place || '').slice(0, 9);
      ctx.font = '600 9.5px system-ui,"PingFang SC",sans-serif';
      const w = ctx.measureText(txt).width;
      ctx.fillStyle = 'rgba(8,13,20,.82)';
      roundRect(ctx, x + 9, y - 8, w + 10, 15, 4);
      ctx.fill();
      ctx.fillStyle = '#ffcabc';
      ctx.fillText(txt, x + 14, y + 2.5);
      ctx.restore();
    }
  }

  function drawSimPick() {    const lat = state.simPick.lat, lon = state.simPick.lon;
    if (lat == null) return;
    const x = px(lon), y = py(lat);
    ctx.save();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = 'rgba(255,196,107,.92)';
    ctx.lineWidth = 1.4;
    ctx.beginPath(); ctx.arc(x, y, 14, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(x - 24, y); ctx.lineTo(x - 8, y);
    ctx.moveTo(x + 8, y); ctx.lineTo(x + 24, y);
    ctx.moveTo(x, y - 24); ctx.lineTo(x, y - 8);
    ctx.moveTo(x, y + 8); ctx.lineTo(x, y + 24);
    ctx.stroke();
    ctx.setLineDash([]);
    ctx.font = '700 9.5px ui-monospace,monospace';
    const txt = '演练震中';
    const w = ctx.measureText(txt).width;
    ctx.fillStyle = 'rgba(6,10,16,.8)';
    roundRect(ctx, x + 16, y - 24, w + 10, 16, 4);
    ctx.fill();
    ctx.fillStyle = '#ffc46b';
    ctx.fillText(txt, x + 21, y - 13);
    ctx.restore();
  }

  function drawFocus(ev) {
    const f = state.focus, x = px(f.lon), y = py(f.lat);
    ctx.save();
    ctx.translate(x, y);
    ctx.beginPath();
    for (let i = 0; i < 8; i++) {
      const ang = i * Math.PI / 4, r1 = 7, r2 = 2.6;
      ctx.lineTo(Math.cos(ang) * r1, Math.sin(ang) * r1);
      ctx.lineTo(Math.cos(ang + Math.PI / 8) * r2, Math.sin(ang + Math.PI / 8) * r2);
    }
    ctx.closePath();
    ctx.fillStyle = '#6ea8fe';
    ctx.fill();
    ctx.strokeStyle = 'rgba(110,168,254,.3)';
    ctx.lineWidth = 1;
    ctx.beginPath(); ctx.arc(0, 0, 11, 0, Math.PI * 2); ctx.stroke();
    ctx.restore();

    ctx.font = '10px system-ui,"PingFang SC",sans-serif';
    ctx.fillStyle = 'rgba(160,205,255,.9)';
    ctx.fillText(f.name, x + 13, y + 4);

    if (ev) {
      ctx.setLineDash([3, 6]);
      ctx.strokeStyle = 'rgba(110,168,254,.22)';
      ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px(ev.lon), py(ev.lat)); ctx.lineTo(x, y); ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  const CITY_LABELS = [
    [39.90, 116.40, '北京'], [31.23, 121.47, '上海'], [23.13, 113.26, '广州'],
    [30.57, 104.07, '成都'], [29.56, 106.55, '重庆'], [34.34, 108.94, '西安'],
    [25.04, 102.72, '昆明'], [30.59, 114.31, '武汉'], [45.80, 126.54, '哈尔滨'],
    [43.83, 87.62, '乌鲁木齐'], [29.65, 91.17, '拉萨'], [22.32, 114.17, '香港'],
    [25.03, 121.57, '台北'],
    [35.69, 139.69, '東京'], [34.69, 135.52, '大阪'], [35.18, 136.91, '名古屋'],
    [43.06, 141.35, '札幌'], [38.27, 140.87, '仙台'], [33.59, 130.40, '福岡'],
    [26.21, 127.68, '那覇'],
  ];

  // 国家名：只在洲际尺度铺一层，放大到国内就收起来（那时该看城市名）。
  // 中国按正式国名写「中华人民共和国」；台湾/港澳不在这张表里——它们不是国家，
  // 由城市名（台北/香港）和地名翻译负责表述。锚点挑在人口稀疏处，少压城市。
  // 缩放倍率是相对「全球视野」的：1 = 整个世界铺满，东亚默认视野约 3.6，
  // 聚焦到日本时 8~12。6.5 的意思是「还能看见好几个国家的时候才标国名」。
  const COUNTRY_ZOOM = 6.5;
  const COUNTRY_LABELS = [
    [35.0, 103.5, '中华人民共和国'], [36.6, 137.6, '日本'], [36.0, 127.8, '韩国'],
    [40.0, 127.2, '朝鲜'], [46.5, 103.0, '蒙古'], [60.0, 95.0, '俄罗斯'],
    [48.0, 67.0, '哈萨克斯坦'], [41.5, 63.5, '乌兹别克斯坦'], [21.5, 78.5, '印度'],
    [29.5, 69.0, '巴基斯坦'], [33.8, 66.0, '阿富汗'], [32.0, 53.5, '伊朗'],
    [32.5, 43.5, '伊拉克'], [39.0, 35.0, '土耳其'], [24.0, 45.0, '沙特阿拉伯'],
    [26.0, 30.0, '埃及'], [39.5, 22.0, '希腊'], [42.5, 12.5, '意大利'],
    [46.5, 2.5, '法国'], [51.0, 10.0, '德国'], [54.0, -2.5, '英国'],
    [40.0, -4.0, '西班牙'], [61.5, 9.0, '挪威'], [62.5, 15.0, '瑞典'],
    [64.0, 26.0, '芬兰'], [52.0, 19.5, '波兰'], [49.0, 32.0, '乌克兰'],
    [45.8, 25.0, '罗马尼亚'], [64.8, -18.5, '冰岛'], [31.8, -7.0, '摩洛哥'],
    [28.0, 2.5, '阿尔及利亚'], [27.0, 17.0, '利比亚'], [15.0, 30.0, '苏丹'],
    [8.5, 39.5, '埃塞俄比亚'], [0.5, 37.5, '肯尼亚'], [-6.5, 35.0, '坦桑尼亚'],
    [-30.0, 24.0, '南非'], [9.5, 8.0, '尼日利亚'], [7.5, -1.0, '加纳'],
    [-3.0, 23.0, '刚果（金）'], [-12.5, 17.5, '安哥拉'], [-13.5, 27.5, '赞比亚'],
    [-18.0, 35.0, '莫桑比克'], [-19.5, 29.5, '津巴布韦'], [-19.0, 46.5, '马达加斯加'],
    [-22.5, 17.0, '纳米比亚'], [-22.0, 24.0, '博茨瓦纳'], [-25.0, 134.0, '澳大利亚'],
    [-42.0, 172.5, '新西兰'], [-6.0, 144.0, '巴布亚新几内亚'], [-2.0, 117.0, '印度尼西亚'],
    [12.5, 122.5, '菲律宾'], [16.0, 106.5, '越南'], [15.5, 101.0, '泰国'],
    [21.0, 96.0, '缅甸'], [3.5, 102.0, '马来西亚'], [23.7, 90.3, '孟加拉国'],
    [28.3, 84.0, '尼泊尔'], [39.5, -98.5, '美国'], [56.0, -106.0, '加拿大'],
    [23.5, -102.5, '墨西哥'], [21.5, -79.5, '古巴'], [15.5, -90.5, '危地马拉'],
    [4.0, -73.0, '哥伦比亚'], [7.0, -66.0, '委内瑞拉'], [-1.5, -78.5, '厄瓜多尔'],
    [-9.5, -75.0, '秘鲁'], [-16.5, -64.5, '玻利维亚'], [-10.0, -52.0, '巴西'],
    [-33.0, -71.0, '智利'], [-35.0, -64.0, '阿根廷'], [-33.0, -56.0, '乌拉圭'],
    [-23.5, -58.5, '巴拉圭'], [-18.0, 178.0, '斐济'], [-21.0, -175.0, '汤加'],
    [-16.0, 168.0, '瓦努阿图'], [-9.0, 160.0, '所罗门群岛'],
  ];

  function drawLabels() {
    const zoom = view.s / view.fit;
    const fx = px(state.focus.lon), fy = py(state.focus.lat);
    const boxes = [];
    const clash = (x, y, w) => boxes.some(b =>
      !(x + w < b[0] || x > b[0] + b[2] || y + 13 < b[1] || y > b[1] + b[3]));
    // ① 国家名（底噪级，只在洲际视野出现）
    if (zoom <= COUNTRY_ZOOM) {
      ctx.font = '11.5px system-ui,"PingFang SC","Hiragino Sans",sans-serif';
      for (const [lat, lon, name] of COUNTRY_LABELS) {
        const x = px(lon), y = py(lat);
        if (x < -40 || y < 0 || x > view.w + 40 || y > view.h) continue;
        const w = ctx.measureText(name).width;
        if (clash(x - w / 2, y - 6, w)) continue;
        boxes.push([x - w / 2, y - 6, w, 13]);
        ctx.textAlign = 'center';
        ctx.fillStyle = 'rgba(10,16,24,.55)';
        ctx.fillText(name, x + 1, y + 5);
        ctx.fillStyle = 'rgba(158,182,210,.55)';
        ctx.fillText(name, x + 1, y + 4);
        ctx.textAlign = 'left';
      }
    }
    // ② 城市名（亮一档，压在国家名之上）
    ctx.font = '10.5px system-ui,"PingFang SC","Hiragino Sans",sans-serif';
    for (const [lat, lon, name] of CITY_LABELS) {
      if (zoom < 1.1 && (name === '名古屋' || name === '那覇')) continue;
      const x = px(lon), y = py(lat);
      if (x < 0 || y < 0 || x > view.w || y > view.h) continue;
      if (Math.hypot(x - fx, y - fy) < 22) continue;
      const w = ctx.measureText(name).width;
      if (clash(x + 3, y - 11, w)) continue;
      if (OCC.some(b => !(x + 3 + w < b[0] || x + 3 > b[0] + b[2] || y + 2 < b[1] || y - 11 > b[1] + b[3]))) continue;
      boxes.push([x + 3, y - 11, w, 13]);
      ctx.fillStyle = 'rgba(0,0,0,.6)';
      ctx.fillText(name, x + 5, y + 4);
      ctx.fillStyle = 'rgba(178,198,222,.8)';
      ctx.fillText(name, x + 4, y + 3);
    }
  }

  function drawScaleBar() {
    const latC = 33, want = 92;
    const kmRaw = want / kmPx(latC);
    const nice = [50, 100, 200, 300, 500, 1000, 2000];
    let km = nice[0];
    for (const n of nice) if (Math.abs(n - kmRaw) < Math.abs(km - kmRaw)) km = n;
    const len = km * kmPx(latC);
    const x = view.w - len - 24, y = view.h - 104;
    ctx.save();
    ctx.strokeStyle = 'rgba(190,210,232,.45)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(x, y); ctx.lineTo(x + len, y);
    ctx.moveTo(x, y - 3); ctx.lineTo(x, y + 3);
    ctx.moveTo(x + len, y - 3); ctx.lineTo(x + len, y + 3);
    ctx.stroke();
    ctx.fillStyle = 'rgba(190,210,232,.55)';
    ctx.font = '9.5px ui-monospace,monospace';
    ctx.fillText(km >= 1000 ? (km / 1000) + ',000 km' : km + ' km', x + 1, y - 6);
    ctx.restore();
  }

  function roundRect(c, x, y, w, h, r) {
    c.beginPath();
    c.moveTo(x + r, y);
    c.arcTo(x + w, y, x + w, y + h, r);
    c.arcTo(x + w, y + h, x, y + h, r);
    c.arcTo(x, y + h, x, y, r);
    c.arcTo(x, y, x + w, y, r);
    c.closePath();
  }

  // ---------------- 交互 ----------------
  let drag = null, downAt = null, tipAt = 0;
  canvas.addEventListener('mousedown', e => {
    cancelViewTween();          // 用户一碰画面就停掉自动拉远，不跟用户抢
    noteUserView();             // 也别在这时候把镜头飞回全景
    drag = { x: e.clientX, y: e.clientY, tx: view.tx, ty: view.ty };
    downAt = { x: e.clientX, y: e.clientY };
    canvas.classList.add('drag');
  });
  window.addEventListener('mouseup', () => { drag = null; canvas.classList.remove('drag'); });
  canvas.addEventListener('mousemove', e => {
    const rect = canvas.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    if (drag) {
      view.tx = drag.tx + (e.clientX - drag.x);
      view.ty = drag.ty + (e.clientY - drag.y);
      clampView();
      baseDirty = fieldDirty = true;
      return;
    }
    const lat = latAt(my), lon = normLon(lonAt(mx));
    $('#cursorPos').textContent = latLabel(lat) + '  ' + lonLabel(lon);
    let hit = null, best = 11;
    for (const st of state.stations) {
      const d = Math.hypot(px(st.lon) - mx, py(st.lat) - my);
      if (d < best) { best = d; hit = st; }
    }
    const tip = $('#tip');
    if (state.tipPin) return;          // 卡片钉住时不再跟着鼠标跑
    if (hit) {
      tip.innerHTML = stationInfoHtml(hit);
      tip.style.left = e.clientX + 'px';
      tip.style.top = e.clientY + 'px';
      tip.classList.add('show');
    } else tip.classList.remove('show');
  });
  canvas.addEventListener('mouseleave', () => {
    if (!state.tipPin) $('#tip').classList.remove('show');
    $('#cursorPos').textContent = '—';
  });
  canvas.addEventListener('wheel', e => {
    e.preventDefault();
    noteUserView();
    const rect = canvas.getBoundingClientRect();
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, Math.exp(-e.deltaY * 0.0016));
  }, { passive: false });

  function zoomAt(mx, my, f) {
    cancelViewTween();
    const ns = clamp(view.s * f, view.fit * 0.9, view.fit * 80);
    const real = ns / view.s;
    view.s = ns;
    view.tx = mx - (mx - view.tx) * real;
    view.ty = my - (my - view.ty) * real;
    clampView();
    lastUserZoomAt = performance.now();   // 手动缩放后 3 秒内不自动跟随波前
    baseDirty = fieldDirty = true;
  }
  function zoomBy(f) { zoomAt(view.w / 2, view.h / 2, f); }

  // 演练选点 → 按坐标反查地名。以前前端只传经纬度、后端从预设里随机挑个地名，
  // 于是「点在北海道近海、标题却写熊本県」——地名就是这么乱掉的。
  async function placeAt(lat, lon) {
    try {
      const cc = $('#simCountry') ? $('#simCountry').value : 'JP';
      const r = await fetch('/api/place?lat=' + lat + '&lon=' + lon + '&country=' + cc);
      const d = await r.json();
      return (d && d.ok && d.place) || '';
    } catch (e) { return ''; }
  }
  let simPlaceSeq = 0;
  async function markSimPick() {
    const pos = $('#simPickPos');
    const lat = state.simPick.lat, lon = state.simPick.lon;
    if (lat == null || lon == null) {
      if (pos) pos.textContent = '当前：使用预设震源';
      state.simPick.place = '';
      return;
    }
    const coord = lat.toFixed(2) + '°N ' + lon.toFixed(2) + '°E';
    if (pos) pos.textContent = '当前：' + coord + '（已选，正在查地名…）';
    const seq = ++simPlaceSeq;
    const nm = await placeAt(lat, lon);
    if (seq !== simPlaceSeq) return;      // 连点时只认最后一次
    state.simPick.place = nm;
    if (pos) pos.textContent = '当前：' + (nm ? nm + ' · ' : '') + coord + '（已选）';
    toast('演练震中：' + (nm || coord));
  }

  canvas.addEventListener('click', e => {
    if (downAt && Math.hypot(e.clientX - downAt.x, e.clientY - downAt.y) > 4) return;
    const rect = canvas.getBoundingClientRect();
    const lat = latAt(e.clientY - rect.top), lon = normLon(lonAt(e.clientX - rect.left));
    if (!isFinite(lat) || !isFinite(lon)) return;
    if (state.simPick.on) {
      state.simPick.lat = +lat.toFixed(3);
      state.simPick.lon = +lon.toFixed(3);
      markSimPick();
      baseDirty = true;
      return;
    }
    // 和悬停用同一套像素判定：按度数判会在缩小的地图上「点不准」（0.35° 可能只有几个像素）
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    let near = null, nd = 11;
    for (const s of state.stations) {
      const d = Math.hypot(px(s.lon) - mx, py(s.lat) - my);
      if (d < nd) { nd = d; near = s; }
    }
    const tip = $('#tip');
    if (near) {
      // 点到台站：把它的观测状态钉在屏幕上（内容随下一拍刷新），再点空白处取消
      state.tipPin = near.code;
      tip.innerHTML = stationInfoHtml(near);
      tip.style.left = Math.min(e.clientX, innerWidth - 300) + 'px';
      tip.style.top = Math.min(e.clientY + 14, innerHeight - 40) + 'px';
      tip.classList.add('show', 'pin');
      setFocus(near.name, near.lat, near.lon, near.country);
    } else {
      state.tipPin = null;
      tip.classList.remove('pin');
      setFocus('地图点', +lat.toFixed(3), +lon.toFixed(3));
    }
  });

  // ---------------- 声音 ----------------
  let actx = null;
  function audio() {
    if (!actx) { try { actx = new (window.AudioContext || window.webkitAudioContext)(); } catch (e) { } }
    if (actx && actx.state === 'suspended') actx.resume();
    return actx;
  }
  function beep(at, freq, dur, gain, type) {
    const ac = audio(); if (!ac) return;
    const o = ac.createOscillator(), g = ac.createGain();
    o.type = type || 'triangle';
    o.frequency.setValueAtTime(freq, at);
    g.gain.setValueAtTime(0, at);
    g.gain.linearRampToValueAtTime(gain, at + 0.015);
    g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
    o.connect(g); g.connect(ac.destination);
    o.start(at); o.stop(at + dur + 0.02);
  }
  // ---------------- 语音包（EQuake 标注版） ----------------
  // 只使用用户标注过语义的文件；未标注的数字文件不加载、不播放。
  const VOICE = { items: [], map: {} };
  const VOICE_GROUP = { event: '事件', exp: '预想震度', station: '测站震度' };

  async function loadVoicePack() {
    try {
      const r = await fetch('sounds/voice/index.json', { cache: 'no-store' });
      const d = await r.json();
      VOICE.items = d.items || [];
    } catch (e) {
      VOICE.items = [];
    }
    // 同一个语义键可能有两个版本：plain（提示音）/ voice（人声发音）
    VOICE.map = {};
    VOICE.order = [];
    for (const it of VOICE.items) {
      let slot = VOICE.map[it.key];
      if (!slot) {
        slot = VOICE.map[it.key] = {
          key: it.key, label: it.label, group: it.group, plain: null, voice: null,
        };
        VOICE.order.push(it.key);
      }
      if (it.voice) slot.voice = it; else slot.plain = it;
    }
    const cnt = $('#packCount');
    if (cnt) cnt.textContent = VOICE.order.length || '0';
    renderVoiceList();
    buildChimeOptions();
  }

  // 设置里「人声发音」开 → 优先用人声版；某段没有人声版就退回提示音版
  const wantHuman = () => state.settings.human !== false;
  function pickVoice(key) {
    const slot = VOICE.map[key];
    if (!slot) return null;
    if (wantHuman() && slot.voice) return slot.voice;
    return slot.plain || slot.voice || null;
  }

  function buildChimeOptions() {
    const sel = $('#optChime');
    if (!sel) return;
    let html = '<option value="builtin">内置合成音（880/1174Hz 三连音）</option>';
    for (const k of (VOICE.order || [])) {
      const slot = VOICE.map[k];
      if (!slot || (slot.group !== 'event' && slot.group !== 'exp')) continue;
      const it = pickVoice(k);
      html += '<option value="' + k + '">' + slot.label +
        (it ? '（' + it.dur.toFixed(2) + 's）' : '') + '</option>';
    }
    sel.innerHTML = html;
    sel.value = state.settings.chime || 'builtin';
  }

  function renderVoiceList() {
    const box = $('#voiceList');
    if (!box) return;
    if (!VOICE.items.length) {
      box.innerHTML = '<div class="hint">未找到 sounds/voice/index.json</div>';
      return;
    }
    box.innerHTML = ['event', 'exp', 'station'].map(g => {
      const keys = (VOICE.order || []).filter(k => VOICE.map[k] && VOICE.map[k].group === g);
      if (!keys.length) return '';
      return '<div class="vg"><div class="vg-hd">' + VOICE_GROUP[g] + '</div><div class="vg-body">' +
        keys.map(k => {
          const slot = VOICE.map[k];
          const it = pickVoice(k) || {};
          // 标签反映真正会播的那个文件：有些段只有人声版，关掉人声也只能播它
          const tag = it.voice ? '人声' : '提示音';
          return '<button type="button" class="vitem" data-key="' + k + '">' +
            '<b>' + slot.label + '</b><em>' + tag +
            (it.dur ? ' · ' + it.dur.toFixed(2) + 's' : '') + '</em></button>';
        }).join('') +
        '</div></div>';
    }).join('');
  }

  const soundPath = name => 'sounds/voice/' + name;

  // 桌面版走后端：后端只认 sounds/ 目录内的相对路径（voice/x.wav、builtin.wav）
  const serverPath = p => String(p).replace(/^sounds\//, '');

  // 声音调度：按类别分通道，各播各的
  //   alarm 警报音 / evt 播报人声 / st·st2 台站震度 / prev 试听
  // 以前警报音和人声挤在同一条 evt 队列里串着播：警报音没播完，人声就得干等，
  // 后面的续报还在排队，听起来就是「声音乱、慢半拍」。现在拆开，互不等待；
  // 只有同一类里的多段（人声链）还保持顺序，避免两句叠着念。
  const CH = {};
  function chGet(n) {
    return CH[n] || (CH[n] = { q: [], cur: null, gen: 0, busy: false });
  }

  function chPush(name, paths, replace) {
    const c = chGet(name);
    if (replace) {
      c.q = [];
      c.gen++;
      if (c.cur) { try { c.cur.pause(); } catch (e) { } c.cur = null; }
    }
    c.q.push(paths);
    if (!c.busy) { c.busy = true; pumpChannel(name); }
  }

  async function pumpChannel(name) {
    const c = chGet(name);
    for (;;) {
      const files = c.q.shift();
      if (!files) {
        c.busy = false;
        if (c.q.length) { c.busy = true; continue; }
        return;
      }
      const gen = c.gen;
      for (const p of files) {
        if (gen !== c.gen) break;
        const duck = name === 'st' || name === 'st2';
        if (duck) duckEvent(true);
        await new Promise(res => {
          const a = new Audio(p);
          c.cur = a;
          let done = false;
          let iv = 0;
          const fin = () => {
            if (done) return;
            done = true;
            if (iv) clearInterval(iv);
            if (c.cur === a) c.cur = null;
            res();
          };
          a.onended = fin; a.onerror = fin;
          iv = setInterval(() => { if (gen !== c.gen) { try { a.pause(); } catch (e) { } fin(); } }, 50);
          a.play().catch(fin);
          setTimeout(fin, 12000);
        });
        if (duck) duckEvent(false);
      }
    }
  }

  // 台站语音响的时候把事件语音压低，两条一起响也听得清
  function duckEvent(on) {
    const c = CH.evt;
    if (!c || !c.cur) return;
    try { c.cur.volume = on ? 0.32 : 1; } catch (e) { }
  }

  function stopChannel(name) {
    const c = chGet(name);
    c.q = [];
    c.gen++;
    if (c.cur) { try { c.cur.pause(); } catch (e) { } c.cur = null; }
  }

  let sndEpoch = 0;
  function stopAllSound() {
    sndEpoch++;
    ['alarm', 'evt', 'st', 'st2', 'prev'].forEach(stopChannel);
  }

  // 同一段语音短时间内重复请求一律丢掉：两个窗口同时开着、或续报连着来，
  // 都会让同一段语音叠着念两遍（用户听到的"重复播放"就是它）
  const sentSig = new Map();
  function dedupePaths(tag, paths) {
    const uniq = [];
    for (const p of paths) if (uniq.indexOf(p) < 0) uniq.push(p);
    const sig = (tag || '') + '|' + uniq.join(',');
    const t = Date.now();
    const last = sentSig.get(sig) || 0;
    sentSig.set(sig, t);
    if (sentSig.size > 60) sentSig.clear();
    return { dup: t - last < 1500, paths: uniq };
  }

  // 后端播报请求：失败（服务正忙/连接抖动）重试一次，别让一条语音就这么丢了
  function postSound(body, retry) {
    fetch('/api/sound', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }).then(r => {
      if (!r.ok && retry) setTimeout(() => postSound(body, false), 250);
    }).catch(() => {
      if (retry) setTimeout(() => postSound(body, false), 250);
    });
  }

  function playFiles(paths, prio, tag, ttl) {
    if (!paths || !paths.length) return;
    ttl = ttl === undefined ? 8 : ttl;
    const ded = dedupePaths(tag, paths);
    if (ded.dup) return;
    paths = ded.paths;
    if (state.serverAudio) {
      postSound({ files: paths.map(serverPath), prio: prio || 0, tag: tag || '', ttl: ttl }, true);
      return;
    }
    if (tag === 'preview') { stopAllSound(); chPush('prev', paths, true); return; }
    if (tag === 'st') { stVoicePush(paths); return; }
    if (tag === 'alarm') { chPush('alarm', paths, true); return; }
    // 人声比警报音晚 220ms 开口，开头不被三连音盖住；但不等警报播完
    const ep = sndEpoch;
    setTimeout(() => { if (ep === sndEpoch) chPush('evt', paths, false); }, 220);
  }

  // 让后端把台站通道停掉（最终报 / 取消之后不该再冒人声）
  function stopStationSound() {
    stopChannel('st'); stopChannel('st2');
    if (state.serverAudio) postSound({ tag: 'stopst' }, false);
  }

  function chimeFiles() {
    const c = state.settings.chime || 'builtin';
    if (c === 'builtin') return ['sounds/builtin.wav'];
    const it = pickVoice(c);
    return it ? [soundPath(it.file)] : ['sounds/builtin.wav'];
  }

  function expKeyFor(label) {
    const m = { '2': 'exp2', '3': 'exp3', '4': 'exp4', '5弱': 'exp5m' };
    return m[String(label || '').trim()] || null;
  }

  // 测站声用的是语音包里 **st0~st7 这十段震度音**（用户自己提供的那套）。
  // 只有三档例外：震度1 / 5強 / 6強 在语音包里只有人声版（st1-v / st5p-v / st6p-v），
  // 而测站这一路不该念人声 → 退到低一档的非人声提示音、连响两声来区分档位。
  // 想听人声版可以在设置里打开「测站音用人声版」。
  const ST_KEYS = ['st0', 'st1', 'st2', 'st3', 'st4', 'st5m', 'st5p', 'st6m', 'st6p', 'st7'];
  // 不再跨 key 借音：5強/6強 本来就不出声，震度1 也有 st1，
  // 万一某个槽空了就退回合成音，绝不拿别的震度档顶替（避免串音）
  const ST_FALLBACK = {};

  function stFilesForRisk(risk) {
    const r = Math.max(0, Math.min(9, risk | 0));
    const slot = VOICE.map[ST_KEYS[r]];
    const wantHuman = state.settings.stHuman === true;
    const it = slot && (wantHuman ? (slot.voice || slot.plain) : slot.plain);
    if (it) return [soundPath(it.file)];
    const fb = VOICE.map[ST_FALLBACK[r]];
    if (fb && fb.plain) return [soundPath(fb.plain.file)];
    return null;
  }
  // 语音包没加载出来时的兜底：合成一声，别静音
  function stTone(risk) {
    const r = Math.max(0, Math.min(9, risk | 0));
    const ac = audio(); if (!ac) return;
    const t0 = ac.currentTime + 0.02;
    const f0 = 470 + r * 72;
    const n = r >= 7 ? 3 : r >= 5 ? 2 : 1;
    for (let i = 0; i < n; i++) {
      const at = t0 + i * 0.15;
      beep(at, f0, 0.12, 0.20, 'triangle');
      beep(at, f0 * 1.5, 0.09, 0.06, 'sine');
    }
  }
  // 测站音的唯一出口。两声可以重叠（两路轮换），所以「立马衔接」不用等前一段播完。
  let stSlot = 0;
  function stVoicePush(paths) {
    stSlot = stSlot ? 0 : 1;                   // 两路轮换：最多两个测站声同时存在
    chPush(stSlot ? 'st2' : 'st', paths, true);
  }
  function playStSound(risk) {
    const r = Math.max(0, Math.min(9, risk | 0));
    // 5強/6強 不发声：这两档没有原生音，只能拿别的档变调凑，很难听——
    // 用户拍板这两档干脆不出声（6弱 st6m、震度7 st7 照常响，能听出还在升级）
    if (r === 6 || r === 8) return;
    const files = stFilesForRisk(risk);
    if (!files) { stTone(risk); return; }
    if (state.serverAudio) {
      // 桌面版：交给服务端，两路别名轮换，最多两个同时响
      postSound({ files: files.map(serverPath), prio: 1, tag: 'st', ttl: 3 }, true);
      return;
    }
    stVoicePush(files);
  }

  // 测站声规则：**只升不降**。波前扫过时，刚到的台站一定比更远的强，
  // 所以正常听感应该是 1 → 2 → 3 → 4 一路往上；但续报会重算一遍台站，
  // 以前会把远处弱站也当成「新到」，响完 5弱 又回头响 2，听着就是乱窜。
  // 现在：只播比本场已播最高档更高的档，同档不重复，档间至少隔 0.8s。
  const ST_MIN_GAP = 0.8;
  function stAllow(risk, tSt) {
    if (risk <= (state.stMax | 0)) return false;      // 不降档、不重复
    if (tSt - (state.lastStSound || 0) < ST_MIN_GAP) return false;
    return true;
  }
  function stHit(risk, tSt) {
    state.stRec.push({ lv: risk, t: tSt });
    state.lastStSound = tSt;
    state.stLvAt[risk] = tSt;
    state.stMax = Math.max(state.stMax, risk);
    playStSound(risk);
  }

  function alarm(strength) {
    let files = chimeFiles();
    // 内置合成音是短促三连音，强震播两遍；自定义语音只播一遍（否则听起来就是重复）
    if (strength >= 5 && state.settings.chime === 'builtin') files = files.concat(files);
    playFiles(files, 0, 'alarm', 6);
    // 日本事件已经有语音包，不再叠 TTS，避免中日文两路一起念
    const ev = currentEvent();
    const hasPack = VOICE.order && VOICE.order.length;
    if (state.settings.speech && !(ev && ev.country === 'JP' && hasPack)) {
      setTimeout(speak, 300);
    }
  }

  // 事件播报：只对日本（语音包是日文），中国仍用 TTS
  // 判断「这两条是不是同一场地震」。不能只看 uid：同一场地震现在可能由
  // Wolfx / Jian 两个源先后推来，uid 编法不同、发震时刻还差几秒；也不能只看
  // origin_ts：JMA 会修正发震时刻。所以用「时刻 ±90 秒 + 震中 0.8° 内」一起判。
  function sameQuake(a, b) {
    if (!a || !b) return false;
    if (a.uid && b.uid && a.uid === b.uid) return true;
    if (!a.origin_ts || !b.origin_ts) return false;
    if (Math.abs(a.origin_ts - b.origin_ts) > 90) return false;
    return Math.abs((a.lat || 0) - (b.lat || 0)) < 0.8 &&
      Math.abs((a.lon || 0) - (b.lon || 0)) < 0.8;
  }

  const voiceState = { key: '', uid: '', serial: 0, max: '', at: 0, cancelled: false, stopped: '', ev: null };
  function announceEvent(ev) {
    if (!state.settings.voice || !ev) return;
    // 推算事件不念白：它不是官方速报，念出来会和真报文混淆
    if (ev.auto) return;
    // 语音包是日文。日本事件直接用；中国事件只在没开中文 TTS 时才用语音包，
    // 免得「日文念白 + 中文 TTS」两路一起响——但绝不会一声不吭（以前中国事件就是这样全程没声）
    if (ev.country === 'TW') { if (!state.settings.speech) return; }
    else if (!usesShindo(ev) && state.settings.speech) return;
    const files = [];
    const say = key => {
      const it = pickVoice(key);
      if (it) files.push(soundPath(it.file));
    };
    const lab = ev.max_intensity || '';
    const now = nowSec();
    // 身份用「同一场地震」判断，而不是单纯的 origin_ts 字符串：多源推同一场时
    // 发震时刻会差几秒，一换源就被当成新地震，于是本该念「更新报」的地方
    // 又念了一遍「地震发生了」——这就是那条「更新报被换成检测到地震」的根。
    const same = sameQuake(voiceState.ev, ev);
    if (!same) {
      voiceState.ev = ev;
      voiceState.key = String(ev.origin_ts || ev.uid);
      voiceState.uid = ev.uid;
      voiceState.serial = ev.serial || 1;
      voiceState.max = lab;
      voiceState.cancelled = false;
      say('quake');
      const e = expKeyFor(lab);
      if (e) say(e);
    } else if (ev.cancel) {
      // 取消只念一次
      if (!voiceState.cancelled) { voiceState.cancelled = true; say('cancel'); }
    } else if ((ev.serial || 0) > voiceState.serial) {
      voiceState.ev = ev;                    // 续报：更新引用，下次比较才准
      voiceState.serial = ev.serial;
      const changed = lab && lab !== voiceState.max;
      if (changed) voiceState.max = lab;
      if (ev.final) {
        say('final');
      } else if (changed) {
        // 只有震度变了才重念；语音包里没有这一档就干脆不播，
        // 绝不拿「更新报」冒充震度（那才是听起来"跳错语音包"的原因）
        const e = expKeyFor(lab);
        if (e) say(e);
      } else if (now - voiceState.at >= 7) {
        // 震度没变的续报每 7 秒最多一句，别每 2 秒念一次
        say('update');
      }
    }
    // 「取消」表示误报，立刻掐掉台站那一路（同一个事件只停一次）
    if (ev.cancel && voiceState.stopped !== voiceState.key) {
      voiceState.stopped = voiceState.key;
      stopStationSound();
    }
    if (!files.length) return;
    // 两条播报之间至少隔 2 秒，避免叠着念。
    // 注意：以前这里是 `return` 直接把这一条丢掉——可 voiceState.serial/max 已经
    // 提前更新过了，被丢掉的那条永远不会再播（最终报整句消失、震度档位漏念，
    // 就是「有时候声音不出来」的主因）。现在改成排队等待，到点补播。
    if (now - voiceState.at < 2) { queueSay(files); return; }
    sayNow(files);
  }

  // 待播队列：只保留最后一条（更新的播报会顶掉还没念的旧播报）
  let sayPending = null, sayTimer = 0;
  function queueSay(files) {
    sayPending = files;
    if (sayTimer) return;
    const wait = Math.max(60, (2 - (nowSec() - voiceState.at)) * 1000);
    sayTimer = setTimeout(() => {
      sayTimer = 0;
      const f = sayPending;
      sayPending = null;
      // 等待期间事件已经结束/被取消，就不再补播
      const cur = currentEvent();
      if (!f || !cur || cur.cancel || !state.settings.voice) return;
      if (nowSec() - voiceState.at < 2) { queueSay(f); return; }
      sayNow(f);
    }, wait);
  }
  function sayNow(files) {
    voiceState.at = nowSec();
    playFiles(files, 0, 'event', 8);
  }

  // 震度速报（实测）也念一句：没有紧急地震速报的日本地震、或者速报已经结束，
  // 这时候台站是实实在在观测到了震度，一样该出声
  function announceObs(o) {
    if (!o || !state.settings.voice || state.settings.stVoice === false) return;
    const risk = SCALE_RISK[o.max_scale] || 0;
    if (risk < 1) return;
    // 只报最近十分钟内的速报，别一开程序就把昨天的震度念一遍
    if (nowSec() - (o.report_ts || o.origin_ts || 0) > 600) return;
    // 同一场地震的不同来源（速報 10:53:00 / 詳細 10:53:19）发震时刻会差几秒，
    // 按分钟取整当身份，避免「速報播一次、詳細又播一次」。
    const vKey = Math.round((o.origin_ts || 0) / 60);
    if (state.obsVoiceUid !== vKey) {
      state.obsVoiceUid = vKey;
      state.obsVoiceRisk = -1;
    }
    if (risk <= state.obsVoiceRisk) return;
    const tO = nowSec();
    // 震度速報是「事后通报」，不该拿台站那种警报音去吓人：
    // 优先播语音包里的**人声**「震度N」，没有才退回提示音。
    if (tO - (state.lastObsSound || 0) < 1.2) return;
    state.obsVoiceRisk = risk;
    state.lastObsSound = tO;
    playObsSound(risk);
  }

  // 震度速報专用出口：人声优先，且 5強/6強 也有人声版（st5p-v / st6p-v）
  function playObsSound(risk) {
    const r = Math.max(0, Math.min(9, risk | 0));
    const slot = VOICE.map[ST_KEYS[r]];
    const it = slot && (slot.voice || slot.plain);
    if (!it) { stTone(r); return; }
    const files = [soundPath(it.file)];
    if (state.serverAudio) {
      postSound({ files: files.map(serverPath), prio: 2, tag: 'obs', ttl: 5 }, true);
      return;
    }
    chPush('obs', files, true);
  }

  // ---------------- JMA 风格「震度速報」闪现卡 ----------------
  // 新地震的第一报从屏幕顶上掉下来亮 12 秒：黄底黑框、左边大震度数字、
  // 右边震源和发震时刻，还没查明的项写「調査中」——气象厅推特上那种卡。
  // 按 uid 去重只闪一次；演练、推算、取消报都不闪；点一下就收。
  const SOK_HOLD = 12000;
  const sokSeen = new Set();
  let sokTimer = 0, sokEl = null;

  // 底色跟着「报过来的震度」走，用气象厅震度分布图那套标准配色：
  // 1 近白 → 2 蓝 → 3 深蓝 → 4 米黄 → 5弱 黄 → 5強 橙 → 6弱 红 → 6強 暗红 → 7 紫。
  // 深底（3、6弱以上）文字转白，浅底保持黑字，别出现黑字压暗红看不见的情况。
  const SOK_BG = {
    '1': ['#f2f2ff', '#141414'],
    '2': ['#00aaff', '#141414'],
    '3': ['#0041ff', '#ffffff'],
    '4': ['#fae696', '#141414'],
    '5弱': ['#ffe600', '#141414'],
    '5強': ['#ff9900', '#141414'],
    '6弱': ['#ff2800', '#ffffff'],
    '6強': ['#a50021', '#ffffff'],
    '7': ['#b40068', '#ffffff'],
  };

  // 「震度速報」是气象厅的产品：只有 jma_* 的官报才配这张卡。
  // 中国/台湾/全球台网的速报不套它——那边根本没有「震度速報」这回事。
  function isJmaReport(it) {
    const s = String((it && it.source) || '').toLowerCase();
    return !!s && s.indexOf('jma') === 0;
  }

  function sokHide() {
    if (sokTimer) { clearTimeout(sokTimer); sokTimer = 0; }
    const el = sokEl; sokEl = null;
    if (!el) return;
    el.classList.add('out');
    setTimeout(() => el.remove(), 300);
  }

  function sokTime(ts, tzMin) {
    if (!ts) return '--:--:--';
    const d = new Date((ts + (tzMin || 0)) * 1000);
    const p = n => String(n).padStart(2, '0');
    return d.getUTCFullYear() + '/' + p(d.getUTCMonth() + 1) + '/' + p(d.getUTCDate()) +
      ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
  }

  function showSokuho(it) {
    if (!it || it.cancel || it.isSim || it.auto || it.sim) return;
    // 电文慢的话第一报可能迟到一会儿；超过 90 秒的就不补闪了，免得吓人
    if (nowSec() - (it.report_ts || it.origin_ts || 0) > 90) return;
    const isObs = it.max_intensity === undefined && it.max_scale !== undefined;
    let label = isObs ? (GRADE[SCALE_RISK[it.max_scale]] || '') : (String(it.max_intensity || ''));
    // 有的源写 5- / 5+，气象厅写成 5弱 / 5強，先统一，不然配色表匹配不上
    label = label.replace(/\s+/g, '').replace(/-$/, '弱').replace(/\+$/, '強');
    if (!label || label === '0') label = '?';
    const place = String(it.place || '').trim();
    const mag = Number(it.mag) || 0;
    const depth = Number(it.depth) || 0;
    const unknown = [];
    if (!place || place === '不明' || place === '調査中') unknown.push('震源');
    if (!mag) unknown.push('規模');
    if (!depth) unknown.push('深さ');
    const strip = unknown.length
      ? unknown.join(' & ') + ' 調査中'
      : '震源 ' + place + ' · M' + (Math.round(mag * 10) / 10) +
        ' · 深さ ' + Math.round(depth) + 'km';
    const when = sokTime(it.origin_ts || it.report_ts, tzOf(it)) + ' 発生';

    if (sokEl) { sokEl.remove(); sokEl = null; }
    if (sokTimer) { clearTimeout(sokTimer); sokTimer = 0; }
    const el = $('#sokuhoFlash');
    if (!el) return;
    el.hidden = false;
    el.classList.remove('out');
    // 整张卡的底色 / 字色由这一报的震度决定，认不出的震度回落原来的黄底
    const pal = SOK_BG[label] || ['#ffd60a', '#141414'];
    el.style.setProperty('--sok-bg', pal[0]);
    el.style.setProperty('--sok-fg', pal[1]);
    $('#sokInt').textContent = label;
    // 5弱/6強 三个字，大字号塞不下就缩一档
    $('#sokInt').parentNode.classList.toggle('small', label.length > 1);
    $('#sokPlace').textContent = place && place !== '不明' ? ('震源 ' + place) : '震源 調査中';
    $('#sokTime').textContent = when;
    $('#sokStrip').textContent = strip;
    sokEl = el;
    sokTimer = setTimeout(sokHide, SOK_HOLD);
  }
  window.__sokTest = showSokuho;   // 测试钩子：截图脚本直接喂一条假报看效果
  window.__sokIsJma = isJmaReport; // 同上：验证「只有气象厅官报才弹」

  function maybeSokuho() {
    if (state.historyEvent) return;
    // 只认气象厅官报：EEW 速报要日本的，震度速報电文本身就来自 jma_sokuho / jma_obs。
    // 气象厅没发的（中国/台湾/全球台网）不弹这张卡。
    const cands = [];
    const ev = primaryEvent();
    if (ev && !ev.isSim && !ev.auto && isJmaReport(ev)) cands.push(ev);
    const o = state.obs;
    if (o && isJmaReport(o)) cands.push(o);
    for (const it of cands) {
      // 同一场地震不同来源（EEW 速报 / 震度速報电文）发震时刻差几秒，
      // 按分钟取整当身份，速報闪过之后詳細电文就不会再闪一次
      const uid = it.uid || ('OBS-' + Math.round((it.origin_ts || 0) / 60));
      if (sokSeen.has(uid)) continue;
      sokSeen.add(uid);
      if (sokSeen.size > 400) sokSeen.clear();   // 只是个去重集合，别让它无限长
      showSokuho(it);
      if (sokEl) break;                          // 一屏一张，后面的等下一轮
    }
  }

  const sokBox = $('#sokuhoFlash');
  if (sokBox) sokBox.addEventListener('click', sokHide);

  function speak() {
    if (!window.speechSynthesis) return;
    const ev = currentEvent(); if (!ev) return;
    const est = estimate(srcOf(ev), state.focus.lat, state.focus.lon);
    const remain = Math.max(0, est.ts - elapsedOf(ev));
    const zh = ev.country !== 'JP';
    const text = (zh ? '地震预警，' : '緊急地震速報。') + ev.place +
      (zh ? '，预计' : '、予想') + (zh ? est.label : '震度' + est.label) +
      (remain > 0 ? (zh ? '，' + Math.round(remain) + '秒后到达' : '、' + Math.round(remain) + '秒後に到達') : '');
    const u = new SpeechSynthesisUtterance(text);
    u.lang = zh ? 'zh-CN' : 'ja-JP';
    u.rate = 1.02;
    window.speechSynthesis.cancel();
    window.speechSynthesis.speak(u);
  }

  // ---------------- 渲染 ----------------
  const SHORT = {
    jma_eew: '日本紧急地震速报', cn_eew: '中国地震预警',
    jma_cat: '日本震度速报', cenc_cat: '中国地震台网', jma_obs: '日本震度观测点',
    jma_sokuho: '日本震度速报', lpgm: '日本长周期地震动', jma_tsunami: '日本海啸警报',
    cwa_eew: '台湾 CWA 速报', sc_eew: '四川地震局', fj_eew: '福建地震局', cq_eew: '重庆地震局',
  };
  const SRC_TITLE = {
    jma_eew: '日本 緊急地震速報',
    cn_eew: '中国 地震预警',
    jma_cat: '日本 震源・震度情報',
    cenc_cat: '中国 CENC 正式測定',
    jma_obs: '日本 震度観測点（实测）',
    palert: '台湾 P-Alert 強震網',
    whews: 'WHEWS 聚合預警',
    eqsc: 'EQSC JMA 速報',
    dmdata: 'DM-D.S.S 気象庁電文',
    trem: 'TREM-Net 即時震度',
    seedlink: 'SeedLink 波形',
    jian: 'Jian Project 預警流',
    jma_sokuho: '日本 震度速報（VXSE51）',
    lpgm: '日本 長周期地震動観測',
    jma_tsunami: '日本 津波警報・注意報',
    cwa_eew: '台湾 CWA 地震速報',
    sc_eew: '四川省地震局 地震速報',
    fj_eew: '福建省地震局 地震速報',
    cq_eew: '重庆市地震局 地震速報',
  };

  function renderDots() {
    const box = $('#srcDots');
    box.innerHTML = state.sources.map(s => {
      const cls = s.state === 'ok' ? 'ok' : s.state === 'down' ? 'err' : 'wait';
      const tip = (SHORT[s.id] || s.name_zh) + ' · ' +
        (s.state === 'ok' ? (s.latency_ms ? s.latency_ms + 'ms' : '正常') :
          s.state === 'down' ? '连接失败' : s.state === 'stale' ? '缓存' : '连接中') +
        (s.id === 'globalquake' && s.role ? ' · ' + s.role : '');
      return '<span class="sd ' + cls + '" data-tip="' + tip + '"></span>';
    }).join('');
    const ok = state.sources.filter(s => s.state === 'ok').length;
    if (state.sources.length) {
      $('#quietTxt').textContent = (state.sim && state.sim.pending)
        ? '演练 · 台站检测中（还没有台站报到）'
        : '监听中 · ' + ok + '/' + state.sources.length + ' 源正常';
    }
  }

  function renderClocks() {
    const now = nowSec();
    $('#clockBJ').textContent = fmtClock(now, 8 * 3600);
    $('#clockJP').textContent = fmtClock(now, 9 * 3600);
  }

  const evKey = ev => ev.uid + '#' + (ev.serial || 0);

  function renderHero() {
    const ev = displayEvent();
    const hero = $('#hero'), quiet = $('#quiet');
    if (!ev) { hero.hidden = true; quiet.hidden = false; return; }
    const hist = !!ev.isHist;
    quiet.hidden = true;
    hero.hidden = false;

    const est = estimate(srcOf(ev), state.focus.lat, state.focus.lon);
    const elapsed = (hist && !state.histReplayAt) ? 0 : elapsedOf(ev);
    const remain = est.ts - elapsed;
    const th = usesShindo(ev) ? Number(state.settings.thJp) : Number(state.settings.thCn);
    const hot = !hist && est.val >= th;
    hero.className = 'hero' + (hot ? ' alert' : hist ? ' hist' : '');

    $('#heroBadge').textContent = ev.auto ? '推算震源'
      : ev.isSim ? '演练' : hist ? '历史回看'
        : ev.kind === 'gq' ? '全球地震检测'
          : ev.country === 'JP' ? (ev.warn ? '緊急地震速報' : '緊急地震速報・予報')
            : ev.country === 'TW' ? '地震速報（中国台湾）' : '中国地震预警';
    $('#heroSrc').textContent = ev.auto
      ? ('本地推算 · ' + (ev.stations || 0) + ' 站 · 残差 ' +
         (typeof ev.rms === 'number' ? ev.rms.toFixed(2) : '—'))
      : ev.isSim ? '模拟事件 · 震源为台站反演推定' : hist ? (ev.source === 'usgs' ? 'USGS' : '速报目录')
        : ev.kind === 'gq' ? (ev.source_name || 'GlobalQuake 全球台网')
          : ev.country === 'JP' ? '気象庁'
            : ev.country === 'TW' ? '台湾中央气象署 CWA'
              : (ev.source_name || '中国地震预警');
    const qEl = $('#heroQual');
    if (qEl) {
      const q = ev.kind === 'gq' ? ev.gq_quality : null;
      if (ev.kind === 'gq') {
        // 质量评级是 GQ 解算质量（S 最好 → D 最差）。归档包里偶尔没有这一项，
        // 那就显示「质量 —」，别把整颗胶囊藏起来——它本身就是「这条是全球台网检测」的标志。
        qEl.hidden = false;
        qEl.textContent = '质量 ' + (q || '—');
        qEl.className = 'qchip' + (q ? ' q-' + q : '');
        qEl.title = (q ? 'GlobalQuake 质量评级 ' + q : 'GlobalQuake 这一报没有带质量评级') +
          (ev.gq_maxpga != null ? ' · 峰值 PGA ' + ev.gq_maxpga + ' cm/s²' : '');
      } else {
        qEl.hidden = true;
      }
    }
    $('#heroPlace').textContent = String(ev.place || '—').replace(/（演练）/g, '');
    $('#heroMag').textContent = (ev.mag || 0).toFixed(1);
    $('#heroDepth').textContent = Math.round(ev.depth || 10);
    $('#heroTime').textContent = hist ? fmtShort(ev.origin_ts, tzOf(ev)) : fmtHM(ev.origin_ts, tzOf(ev));
    const serialEl = $('#heroSerial');
    if (!hist && ev.serial) { serialEl.hidden = false; serialEl.textContent = '第 ' + ev.serial + ' 报'; }
    else serialEl.hidden = true;
    // 最大预想震度：这才是语音念的那一档（大字 heroInt 是「关注点」的预计震度，
    // 默认关注点是北京，日本地震时它常年是 0——所以必须把两者分开标，不然就是
    // 「语音念了 5 弱、界面颜色却没变」）
    const mxEl = $('#heroMax'), mxVal = $('#heroMaxVal');
    if (mxEl) {
      let mx = ev.max_intensity;
      // 全球台网这类没有官方震度的写到 '?'：别在卡上顶一个「最大 震度?」
      if (!mx || mx === '?' || mx === '—') mx = '';
      // 演练：最大预想震度与区块同源（各县多边形推算的最大档），
      // 不然区块升上去了卡片还停在台站算出来的旧档
      if ((ev.isSim || ev.training) && usesShindo(ev)) {
        const c = simPrefIntensity(ev);
        if (c) mx = gradeOf(c.__max).code;
      }
      if (mx) {
        const risk = riskOfLabel(ev, mx);
        mxEl.hidden = false;
        mxVal.textContent = (usesShindo(ev) ? '震度' : '烈度') + mx;
        mxVal.style.background = risk > 0
          ? RISK[Math.max(0, Math.min(RISK.length - 1, risk))]
          : 'rgba(255,255,255,.12)';
        mxVal.style.color = '#fff';
      } else {
        mxEl.hidden = true;
      }
    }
    // GQ 没有官方震度（max_intensity 永远是 ?）：按报文震源给一个震中烈度估算，
    // 用虚线小胶囊标「估算」，不冒充气象厅/台网的官方数。
    const estEl = $('#heroEst'), estVal = $('#heroEstVal');
    if (estEl) {
      let lab = '';
      if (ev.kind === 'gq' && !hist && ev.lat && ev.lon && (ev.mag || 0) > 0) {
        const c = estimate(srcOf(ev), ev.lat, ev.lon);
        lab = usesShindo(ev) ? ('震度' + c.label) : c.label;
        estEl.title = 'GlobalQuake 只给震源和台站振幅，没有官方烈度：按 M' +
          (ev.mag || 0).toFixed(1) + ' / 深 ' + Math.round(ev.depth || 10) +
          ' km 用司・翠川(1999) 衰减式估算震中烈度' +
          (ev.gq_maxpga != null ? '；GQ 报的峰值 PGA ' + ev.gq_maxpga + ' cm/s²' : '');
      }
      estEl.hidden = !lab;
      if (lab) {
        estVal.textContent = lab;
        // 没有「最大震度」胶囊时自己靠右，别孤零零贴在左边
        estEl.classList.toggle('solo', !!(mxEl && mxEl.hidden));
      }
    }
    const intEl = $('#heroInt');
    intEl.textContent = est.label;
    intEl.classList.add('chip-int');
    if (est.risk === 0) {
      intEl.classList.add('zero');
      intEl.style.background = '';
      intEl.style.color = '';
    } else {
      intEl.classList.remove('zero');
      intEl.style.background = hot ? 'rgba(255,255,255,.94)' : RISK[est.risk];
      intEl.style.color = hot ? RISK[est.risk] : '#fff';
    }
    $('#heroScale').textContent = usesShindo(ev) ? '震度' : '烈度';
    $('#heroFocus').textContent = state.focus.name;
    paintHeroWatch(ev, hist);
    $('#heroTimer').textContent = hist && !state.histReplayAt ? '—'
      : remain > 0
        ? (remain >= 60 ? Math.floor(remain / 60) + ':' + String(Math.round(remain % 60)).padStart(2, '0') : Math.round(remain) + 's')
        : '+' + Math.round(-remain) + 's';
    $('#heroTimerCap').textContent = hist && !state.histReplayAt ? '点「回放」看波前'
      : remain > 0 ? '主要动到达' : '主要动已到达';

    const noPlay = hist && !state.histReplayAt;
    $('#heroPT').textContent = fmtDur(est.tp);
    $('#heroST').textContent = fmtDur(est.ts);
    $('#heroPState').textContent = noPlay ? '—' : elapsed >= est.tp ? '已过' : '还有 ' + fmtDur(est.tp - elapsed);
    $('#heroSState').textContent = noPlay ? '—' : elapsed >= est.ts ? '已过' : '还有 ' + fmtDur(est.ts - elapsed);
    $('.phases').title = TT_TAB
      ? '日本用 JMA2001（气象厅），其他地区用 AK135（IASPEI），均为官方走时表'
      : '双层地壳：Vp 6.0/6.8 km/s · Vs 3.47/3.93 km/s，直达波与首波取早到者';

    const ti = est.ti;
    const f1 = x => isFinite(x) ? x.toFixed(1) : '—';
    const row = (k, v) => '<div class="calc-row"><span>' + k + '</span><b>' + v + '</b></div>';
    $('#calcBox').innerHTML =
      '<div class="calc-hd">走时计算 · ' +
        (ti.model === 'jma2001' ? 'JMA2001 表' : 'AK135 表') + '</div>' +
      row('震中距 Δ', Math.round(ti.delta) + ' km') +
      row('震源深度 h', Math.round(ti.depth) + ' km') +
      (TT_TAB
        ? row('模型', ti.model === 'jma2001' ? 'JMA2001（日本气象厅）' : 'AK135（IASPEI）') +
          row('插值', '距离 × 深度 双线性')
        : row('拟合式', 't = a · D^0.90') +
          row('P 系数 a', TT_AP.toFixed(3)) +
          row('S 系数 a', TT_AS.toFixed(3))) +
      row('P 走时', f1(ti.p.t) + ' s') +
      row('S 走时', f1(ti.s.t) + ' s') +
      '<div class="calc-note">S−P 时间差 ' + f1(ti.s.t - ti.p.t) + ' s' +
      '（P 先到 ' + f1(ti.p.t) + ' s，S 到 ' + f1(ti.s.t) + ' s 开始强摇）</div>' +
      '<div class="calc-hd" style="margin-top:8px">震度推算 · 关注点</div>' +
      row('PGV', ((est.pgv || 0) < 0.1 ? (est.pgv || 0).toFixed(3) : (est.pgv || 0).toFixed(1)) + ' cm/s') +
      (usesShindo(ev)
        ? row('計測震度', shindoVal(est.pgv).toFixed(2) + ' → 震度 ' + est.label)
        : row('中国烈度', duOf(est.pgv).toFixed(1) + ' → ' + est.label)) +
      (() => {
        const b = prefBlocks(ev);
        if (!b) return '<div class="calc-note">司・翠川(1999) 衰减式，未做场地放大</div>';
        return b.__est
          ? '<div class="calc-note">日本境内按气象厅速报震源铺县块，此处数值仅供参考</div>'
          : '<div class="calc-note">日本境内已采用气象厅予想震度，此处数值仅供参考</div>';
      })();

    const span = Math.max(60, Math.min(180, Math.ceil(est.ts + 15)));
    $('#heroBar').style.width = clamp(elapsed / span * 100, 0, 100) + '%';
    $('#heroP').style.left = clamp(est.tp / span * 100, 0, 100) + '%';
    $('#heroS').style.left = clamp(est.ts / span * 100, 0, 100) + '%';

    renderAlts(ev, hist);
  }

  // 多事件适配：卡片顶部列出「同时进行的其它地震」，点一下切过去看
  let altsSig = '';
  function altChip(e, cur) {
    const b = document.createElement('button');
    b.className = 'altchip' + (cur && e.uid === cur.uid ? ' on' : '');
    b.dataset.uid = e.uid || '';
    b.title = String(e.place || '');
    const t = document.createElement('span');
    t.textContent = 'M' + (e.mag || 0).toFixed(1) + ' · ' + String(e.place || '').slice(0, 9);
    const i = document.createElement('i');
    const age = Math.max(0, Math.round(nowSec() - (e.origin_ts || nowSec())));
    i.textContent = age < 60 ? age + 's' : Math.round(age / 60) + 'm';
    b.append(t, i);
    return b;
  }
  function renderAlts(ev, hist) {
    const box = $('#heroAlts');
    if (!box) return;
    const others = hist ? [] : activeEvents().filter(e => e.uid !== ev.uid);
    // 每 15 秒重画一次（胶囊上的「多久前」要跟着走）
    const sig = (hist ? 'h' : '') + '|' + ev.uid + '|' +
      others.map(e => e.uid + ':' + (e.mag || 0).toFixed(1) + ':' +
        Math.floor((nowSec() - (e.origin_ts || nowSec())) / 15)).join(',') +
      '|' + (state.focusUid || '');
    if (sig === altsSig) return;
    altsSig = sig;
    box.innerHTML = '';
    if (!others.length && !state.focusUid) { box.hidden = true; return; }
    box.hidden = false;
    if (others.length) {
      const lb = document.createElement('span');
      lb.className = 'altslb';
      lb.textContent = '同时 ' + (others.length + 1) + ' 起';
      box.appendChild(lb);
      others.slice(0, 5).forEach(e => box.appendChild(altChip(e)));
    }
    if (state.focusUid) {
      const b = document.createElement('button');
      b.className = 'altchip back';
      b.dataset.uid = '';
      b.textContent = '回到最新';
      box.appendChild(b);
    }
  }

  function renderRecent() {
    const box = $('#recentList');
    const items = (state.events || []).slice(0, 14);
    if (!items.length) {
      box.innerHTML = '<div style="padding:14px;color:#8b95a5;font-size:12px">暂无速报</div>';
      return;
    }
    box.innerHTML = items.map(e => {
      const cls = e.country === 'JP' ? 'jp' : (e.country === 'CN' || e.country === 'TW') ? 'cn' : 'ot';
      return '<div class="rec ' + cls + '" data-uid="' + e.uid + '">' +
        '<span class="cc"></span>' +
        '<span class="tm">' + fmtShort(e.origin_ts, tzOf(e)).slice(5) + '</span>' +
        '<span class="pl">' + (e.place || '—') + '</span>' +
        '<span class="mg">' +
        (e.kind === 'gq' && e.gq_quality
          ? '<span class="qmini q-' + e.gq_quality + '">' + e.gq_quality + '</span>' : '') +
        'M' + (e.mag || 0).toFixed(1) + '</span></div>';
    }).join('');
  }

  const GRADE_SHORT = ['0', '1', '2', '3', '4', '5−', '5+', '6−', '6+', '7'];
  let stPending = -1;   // 还没来得及播的台站震度档位
  function renderLegend() {
    const bar = $('#lgBar');
    if (bar) {
      // 色带和下面那排震度色块、以及地图上的测站点用同一套官方震度十色：
      // 左边最强（震度7 紫），越往右越弱，最右是震度0 的灰蓝
      const cells = [];
      for (let i = 9; i >= 0; i--) {
        const c = RISK_RGB[i];
        const rgb = 'rgb(' + c[0] + ',' + c[1] + ',' + c[2] + ')';
        cells.push(rgb + ' ' + ((9 - i) * 10) + '%', rgb + ' ' + ((10 - i) * 10) + '%');
      }
      bar.style.background = 'linear-gradient(90deg,' + cells.join(',') + ')';
    }
    const cnt = $('#lgCount');
    if (cnt) cnt.textContent = stationCountNow() + ' 站';
    // 官方震度十色：这排色块 = 色带 = 地图测站点，三处同一套颜色
    $('#legendCells').innerHTML = RISK.map((c, i) =>
      '<div class="cell" title="震度' + GRADE_SHORT[i] + '"><i style="background:' + c +
      '"></i>' + GRADE_SHORT[i] + '</div>').join('');
  }

  // 现在地图上到底有几个测站（实测优先）
  function stationCountNow() {
    const real = realStations(eventForMap());
    return real ? real.length : (state.stations.length || 0);
  }

  // ---------------- 本地推算震源（JMA 不发的弱震） ----------------
  // 气象厅只对达到阈值的地震发紧急地震速報，震度 3 上下的弱震常常一条电文都没有。
  // 这里的思路：把 kmoni 的逐站实时计测震度当输入，找出「聚成一撮」的台站——
  // 够 7 个站、其中有 2 个站已经到绿色（震度 3 档），就认为发生了一次能定位的震动，
  // 用这撮站的加权质心当震源，再拿最大震度反推一个粗震级。
  //
  // 这是**推算**不是观测：产出的事件带 auto 标记，卡片上写「推算震源」，
  // 地图上也用虚线圈，绝不让它冒充气象厅的电文。
  const AUTO = {
    clusterKm: 60,      // 强震兜底路径里，「震中附近」的范围
    minSt: 7,           // 用户定：7 个站到 2.0 就定位
    triggerLv: 2.0,     // 计测震度 2.0（震度2）
    // 这 7 个站不要求挤在 60km 内——一片震度2 往往铺开一百多公里，
    // 死卡 60km 的话永远凑不齐 7 个（之前「没触发」就卡在这）。
    spreadKm: 160,
    strongSt: 4,        // 强震兜底：震中 60km 内已有 4 个站到 2.0，
                        // 说明有感范围窄但确实发生了，不用硬等 7 个
    greenLv: 1.5,
    coolSec: 90,        // 触发后冷却，别每帧都推一个新震源
    holdSec: 120,       // 推算结果在界面上留多久
    depths: [10, 30, 60],   // 深度候选：数据只有震度，浅源/中源分得开就够了
    gridDeg: 0.2,       // 网格步长（度），以质心为中心 ±4 格 = ±0.8°
    lastAt: 0, lastTs: '',
  };

  function autoHypo() {
    if (!kmoniFresh()) return null;
    if (nowSec() - AUTO.lastAt < AUTO.coolSec) return state.autoEv;
    const lv = state.kmoni.lv || {};
    const pts = [];
    for (const st of state.stations) {
      if (st.country !== 'JP') continue;
      const v = lv[st.code];
      if (v === undefined || v < 0.5) continue;
      pts.push({ st: st, v: v });
    }
    if (!pts.length) return null;
    pts.sort((a, b) => b.v - a.v);
    const seed = pts[0];

    // 触发条件（按用户要求放宽）：
    //   主路径：7 个站到计测震度 2.0，且都落在最强站 160km 内 → 定位
    //   兜底：震中 60km 内已有 4 个站到 2.0（有感范围窄的小震）→ 也定位
    // 以前还要「2 个站到 1.5」，遇到一片震度 2 但铺得开的情况就永远不触发。
    const hit = pts.filter(p => p.v >= AUTO.triggerLv &&
      haversine(seed.st.lat, seed.st.lon, p.st.lat, p.st.lon) <= AUTO.spreadKm);
    let near = null, trig = '';
    if (hit.length >= AUTO.minSt) {
      near = hit;
      trig = 'seven2';
    } else if (seed.v >= 3.0) {
      const loc = pts.filter(p => p.v >= AUTO.triggerLv &&
        haversine(seed.st.lat, seed.st.lon, p.st.lat, p.st.lon) <= AUTO.clusterKm);
      if (loc.length >= AUTO.strongSt) { near = loc; trig = 'strong'; }
    }
    // 诊断：即使没触发也记下「差多少」，排查时不用靠猜
    AUTO.diag = { pts: pts.length, hit: hit.length, need: AUTO.minSt,
      seed: Math.round(seed.v * 100) / 100, trig: trig || 'none' };
    if (!near) return null;
    let blues = 0;
    for (const p of near) if (p.v >= AUTO.greenLv) blues++;

    // 加权质心只当初值用：越强的站话语权越大（v^1.8），别让一圈 0.5 的
    // 边缘站把震中拽走。真正的震源在下一步用衰减模型搜出来。
    let wsum = 0, sx = 0, sy = 0, vmax = 0;
    for (const p of near) {
      p.w = clamp(p.v, 0.5, 5);          // 拟合权重：弱站噪声大，别让它主导
      const w = Math.pow(Math.max(p.v, 0.5), 1.8);
      sx += p.st.lon * w; sy += p.st.lat * w; wsum += w;
      if (p.v > vmax) vmax = p.v;
    }
    if (!(wsum > 0)) return null;
    const cLat = sy / wsum, cLon = sx / wsum;
    const imax = Math.min(7, vmax);

    // ---- 震源反演：网格搜索 + 震级的闭式最小二乘解 ----
    // 用项目里同一套衰减式 pgv(M, h, R) → 计测震度 I = 2.68 + 1.72·log10(PGV)。
    // 关键性质：I 对 M 是**线性**的（I = A·M + c_i，A = 1.72×0.58 = 0.9976），
    // 所以给定 (lat, lon, depth) 时 M 能直接解出来，不用再套一层 M 循环——
    // 243 个候选位置 × 每位置一遍台站就够，一帧算完不到 2ms。
    const A_M = 1.72 * 0.58;
    function fitAt(la, lo, dep) {
      let sw = 0, sv = 0, sc = 0;
      const cs = [];
      for (let i = 0; i < near.length; i++) {
        const p = near[i];
        const R = Math.max(3, Math.hypot(
          haversine(la, lo, p.st.lat, p.st.lon), dep));
        // M=0 时的 PGV（ml 项在 M≤6.5 恒为 1），再线性叠 0.58·M
        const p0 = 1.5 * Math.pow(10, 0.0038 * dep - 1.29 - Math.log10(R) - 0.002 * R);
        const c = 2.68 + 1.72 * Math.log10(Math.max(p0, 1e-6));
        cs.push(c);
        sw += p.w; sv += p.w * p.v; sc += p.w * c;
      }
      if (!(sw > 0)) return null;
      const M = (sv - sc) / (A_M * sw);
      let r = 0;
      for (let i = 0; i < near.length; i++) {
        const d = A_M * M + cs[i] - near[i].v;
        r += near[i].w * d * d;
      }
      return { M: M, resid: r / sw };
    }

    let best = null;
    for (let ia = -4; ia <= 4; ia++) {
      for (let ib = -4; ib <= 4; ib++) {
        const la = cLat + ia * 0.2, lo = cLon + ib * 0.2;
        for (const dep of (AUTO.depths)) {
          const f = fitAt(la, lo, dep);
          if (f && (!best || f.resid < best.resid)) {
            best = { lat: la, lon: lo, depth: dep, M: f.M, resid: f.resid };
          }
        }
      }
    }
    if (!best) return null;
    const lat = best.lat, lon = best.lon, depth = best.depth;
    const mag = clamp(best.M, 1.0, 8.0);
    // 残差开方 = 平均差了几个震度档，用来判断这次推算靠不靠谱
    const rms = Math.sqrt(best.resid);

    // 地名：取最近的台站所在县，显示成「〇〇付近」。
    // 不限定 major——代表点太少，岩手县内陆找不到就只剩「推定震源」，太敷衍。
    let nearCity = null, bd = 1e9;
    for (const st of state.stations) {
      if (st.country !== 'JP') continue;
      const d = haversine(lat, lon, st.lat, st.lon);
      if (d < bd) { bd = d; nearCity = st; }
    }
    const place = nearCity ? (nearCity.region || nearCity.name) + '付近' : '推定震源';

    const now = nowSec();
    AUTO.lastAt = now;
    const risk = clamp(gradeOf(imax).risk, 0, 9);
    return {
      uid: 'AUTO-' + Math.round(now),
      country: 'JP', source: 'auto', source_name: '本地推算（JMA 未发布）',
      event_id: 'AUTO-' + Math.round(now),
      serial: 1,
      origin_ts: now - 3,          // 震动已经发生一小会儿了
      report_ts: now,
      place: place,
      lat: lat, lon: lon, depth: depth,
      mag: Math.round(mag * 10) / 10,
      max_intensity: GRADE[risk],
      max_intensity_num: imax,
      warn: false, final: false, cancel: false, training: false,
      is_sea: false, areas: [],
      auto: true,
      stations: near.length,
      blues: blues,
      trigger: trig,                      // seven2 / strong，排查用
      rms: Math.round(rms * 100) / 100,   // 拟合残差（震度档），越小越可信
      title: '推算震源',
    };
  }

  function tickAuto() {
    const ts = state.kmoni.ts;
    if (ts && ts === AUTO.lastTs) return;    // kmoni 每秒才一帧，重复算没意义
    AUTO.lastTs = ts;
    const prev = state.autoEv;
    const ev = autoHypo();
    state.autoEv = ev;
    // 新的推算结果出来就把镜头带过去。以前靠 applyState 顺带对焦，
    // 而状态推送最快也要几秒一次，弱震往往在镜头转过去之前就摇完了。
    if (ev && (!prev || prev.uid !== ev.uid) && !state.historyEvent) focusOnEvent(ev, true);
  }

  function tickReactions() {
    const ev = currentEvent();
    if (!ev) { state.reactCount = 0; state.reactKey = ''; return; }
    // 按「整场地震」清零，而不是按每一报：原先 key 带 serial，续报一来
    // reacted 就清空，所有已经到时的台站又被当成「刚到」，bestRisk 直接跳到
    // 全场最大档——听起来就是测站声音一会儿高一会儿低地乱窜。
    const qKey = ev.origin_ts
      ? (Math.round(ev.origin_ts / 60) + '|' + Math.round(ev.lat || 0) + ',' + Math.round(ev.lon || 0))
      : String(ev.uid || '');
    if (state.reactKey !== qKey) {
      state.reactKey = qKey;
      state.reacted = new Set();
      state.reactCount = 0;
    }
    const el = elapsedOf(ev);
    let bestRisk = -1;
    for (const st of state.stations) {
      if (state.reacted.has(st.code)) continue;
      const est = estOf(ev, st);
      if (el < est.ts) continue;
      state.reacted.add(st.code);
      state.reactCount++;
      if (est.risk > bestRisk) bestRisk = est.risk;
    }
    // 台站提示音：日本/中国都播（档位同一套）
    // 只用「发震时刻」判断是不是换了一场：续报会换 uid / serial，但 origin_ts 不变，
    // 所以按 uid 清零会把同一档一遍遍重播（「测站一直重复响」）。换场次才清零。
    // 换场次才清零测站音的录音状态。指纹用「发震时刻到分钟 + 震中到度」，
    // 不能直接拿 origin_ts：同一场地震由不同源推来时差几秒，那样会被当成新地震，
    // 已经响过的震度档又从头响一遍（听着就是「重复播报」）。
    const stKey = qKey;
    if (state.stKey !== stKey) {
      state.stKey = stKey;
      state.stRec = []; state.stMax = -1; state.stLvAt = {}; stPending = -1;
    }
    state.stUid = ev.uid;
    const stOn = state.settings.voice && state.settings.stVoice !== false && !ev.cancel &&
      (state.settings.stAfterFinal !== false || !ev.final);
    if (!stOn) stPending = -1;
    else if (bestRisk >= 0) stPending = Math.max(stPending, bestRisk);
    const tSt = nowSec();
    // 只升不降：stPending 取这一帧新到台站的最高档，但只有比已播最高档
    // 更高时才出声（stAllow 里判），避免「5弱之后又响 2」这种倒退。
    if (stPending >= 0 && stAllow(stPending, tSt)) {
      const risk = stPending;
      stPending = -1;
      stHit(risk, tSt);              // 用语音包里的震度音（非人声版）
    }
    if (bestRisk >= 0) updateLegendCap();
    window.__stDbg = { risk: bestRisk, pending: stPending, max: state.stMax,
      rec: (state.stRec || []).length, count: state.reactCount,
      on: stOn, voice: state.settings.voice, stv: state.settings.stVoice };
  }

  function updateLegendCap() {
    const colorBy = !!currentEvent() || !!state.historyEvent;
    const mode = stationMode(colorBy ? eventForMap() : null);
    const cap = $('#legendCap');
    const cnt = $('#lgCount');
    const real = realStations(eventForMap());
    if (cnt) cnt.textContent = (real ? real.length : (state.stations.length || 0)) + ' 站';
    if (mode === 'kmoni') {
      const km = state.kmoni;
      // 按档位统计在摇的台站；震动/微动的分界跟地图点亮门槛保持一致，
      // 否则会出现「图上亮了一片、这行却写 0 站在摇」的矛盾。
      const dist = {};
      let shake = 0, micro = 0;
      for (const code in km.lv) {
        const v = km.lv[code];
        if (v >= 0.5) { shake++; const g = GRADE[kmRiskOf(v)]; dist[g] = (dist[g] || 0) + 1; }
        else if (v >= JP_LIGHT) micro++;
      }
      const parts = Object.keys(dist).sort((a, b) => GRADE.indexOf(b) - GRADE.indexOf(a))
        .map(g => g + '×' + dist[g]).join(' ');
      const ago = Math.max(0, nowSec() - km.at);
      cap.textContent = 'NIED 强震观测网 · 实时震度 · ' + km.ts.replace(/^(\d{8})(\d{2})(\d{2})(\d{2})$/, '$1 $2:$3:$4') +
        '（' + ago.toFixed(0) + 's前）· 取色 ' + (km.n || 0) + ' 站 · ' + shake + ' 站在摇' +
        (micro ? ' · ' + micro + ' 站微动' : '') +
        (parts ? ' · ' + parts : '');
      cap.title = '数据源：防災科研 强震モニタ（NIED K-NET/KiK-net，1749 站）逐秒实时震度图像，' +
        '本机逐台站取色换算。平静台站显示为暗点。悬停或点击台站可看它的实时计测震度（0.1 精度）。' +
        '数据版权归 NIED，仅供本机查看，请勿转发。';
      return;
    }
    if (real) {
      const o = state.obs;
      const age = Math.max(0, nowSec() - (o.origin_ts || nowSec()));
      const when = age < 3600 ? Math.round(age / 60) + 'm前' : (age / 3600).toFixed(1) + 'h前';
      // 按震度档分类计数（强→弱），一眼看出每个档各有多少个观测点
      const dist = {};
      for (const p of real) { const l = p.label || '?'; dist[l] = (dist[l] || 0) + 1; }
      const parts = Object.keys(dist)
        .sort((a, b) => (SCALE_RISK[b] || 0) - (SCALE_RISK[a] || 0))
        .map(l => l + '×' + dist[l]).join(' ');
      cap.textContent = '气象厅观测点 · ' + o.place + ' M' + (o.mag || 0).toFixed(1) +
        ' · 实测 ' + real.length + ' 点 · 最大' + (o.max_label || '—') +
        (parts ? ' · ' + parts : '') + ' · ' + when;
      cap.title = '地图上的测站就是这份震度电文里报到的观测点（P2PQuake 中继），' +
        '按电文报的震度用同一套档位色上色，震度 0（没摇）不显示。';
      return;
    }
    if (mode === 'est') {
      const e0 = eventForMap();
      const blk = prefBlocks(e0);
      const hasBlk = !!blk;
      const n = state.reactCount || 0;
      const tail = n ? ' · 已反应 ' + n + '/' + state.stations.length : '';
      // 三种来源要分清：演练按真值、气象厅原文、气象厅只给了震源所以本地按县块算
      const kind = !hasBlk ? '' : (e0 && e0.isSim && !blk.__est) ? '演练 '
        : blk.__est ? '气象厅速报 · 推算' : '气象厅 ';
      cap.textContent = (hasBlk
        ? kind + '震度（都道府県）'
        : '台站 · 推算震动（颜色对应图例震度档）') + tail;
      cap.title = hasBlk
        ? (blk.__est
          ? '气象厅这封报只给了震源、没给地域别予想震度：仍按都道府县铺块，' +
            '每县取离震源最近的顶点按衰减式算'
          : '日本气象厅给出的地域别震度，原样使用，不做本机外推')
        : '气象厅没有给出地域别予想震度，这里按衰减式外推';
    } else if (mode === 'idle') {
      // 震度速報 已到：这是「地域」级速报，地图上给的是都道府县色块
      const o = state.obs || {};
      const n = Object.keys(o.pref || {}).length;
      cap.textContent = '震度速報 · ' + (o.place || '—') + ' · 最大' + (o.max_label || '—') +
        ' · ' + n + ' 都道府县（地域级，不铺观测点）';
      cap.title = '气象厅震度速報只发布「最大震度」与「震度○以上を観測した地域」，' +
        '所以地图上是都道府县色块。观测点级别的「各地の震度」是另一份电文，' +
        '真实观测点电文一到会自动改成逐点显示。';
    } else if (mode === 'obs') {
      const o = state.obs;
      if (!o) {
        cap.textContent = '台网 ' + (state.stations.length || 0) + ' 站 · 观测中 · 当前无震动';
        cap.title = '没有震度速报时台站保持待机（暗点），震度 0 不画点。' +
          '悬停或点击任一台站，可以看到它的观测状态：这次电文有没有报到它、报到震度几、离震中多远。';
      } else {
        const age = Math.max(0, nowSec() - (o.origin_ts || nowSec()));
        const when = age < 3600 ? Math.round(age / 60) + 'm前' : (age / 3600).toFixed(1) + 'h前';
        cap.textContent = '台站实测 ' + o.place + ' M' + (o.mag || 0).toFixed(1) +
          ' · 最大' + (o.max_label || '—') + ' · ' + (o.point_count || 0) + '点 · ' + when;
        cap.title = '气象厅震度观测点（P2PQuake 中继）。悬停或点击台站可看它的观测状态：' +
          '报到的是实测震度，没报到的是震度 0（未达 1）。中国暂无公开台站观测。';
      }
    } else {
      cap.textContent = '台站 · 无事件';
      cap.title = '';
    }
  }

  // ---------------- 震度速報 电文卡 ----------------
  // 气象厅的震度速報就两件事：最大震度、以及「震度○以上を観測した地域」。
  // 以前演练把上千个台站逐个算成震度当「实测点」铺在图上——那不是速报，
  // 是伪造观测。现在速报只出这张电文卡 + 地图上的都道府县色块。
  let obsCardKey = '', obsDismissedTs = 0;

  // 真实电文只带 pref（县 → 最大震度），电文的「地域」分组在这里现算，
  // 口径与气象厅一致：从最大震度往下每档列一次「震度○以上を観測した地域」。
  function obsRegionsOf(o) {
    if (o.regions && o.regions.length) return o.regions;
    const pref = o.pref || {};
    const lab = o.scales || {};
    const okName = k => k && k !== '-' && k.length >= 2;
    const scs = Array.from(new Set(Object.keys(pref).filter(okName).map(k => pref[k].scale)))
      .filter(v => v >= 30).sort((a, b) => b - a);
    return scs.map(sc => ({
      scale: sc,
      label: lab[sc] || GRADE[SCALE_RISK[sc]] || '?',
      prefs: Object.keys(pref).filter(k => okName(k) && (pref[k].scale || 0) >= sc)
        .sort((a, b) => (pref[b].scale - pref[a].scale) || (a < b ? -1 : 1)),
    }));
  }

  function renderObsCard() {
    const box = $('#obsCard');
    if (!box) return;
    // 位置和事件卡是同一个（左上），所以速报在的时候要让「监听中」那颗绿点让开
    const setObs = v => {
      if (box.hidden !== !v) {
        box.hidden = !v;
        document.body.classList.toggle('obs-on', v);
      }
      // 速报卡和事件卡同在左上角：事件卡还在时把速报卡顺延到它下面，
      // 两张都看得见（震度速報 常常和 EEW 的续报同时在线）。
      if (v) {
        const h = $('#hero');
        box.style.top = (h && !h.hidden)
          ? Math.round(h.getBoundingClientRect().bottom + 10) + 'px' : '82px';
      }
    };
    const o = state.obs;
    if (!o || !o.pref || !Object.keys(o.pref).length) {
      obsCardKey = '';
      setObs(false);
      return;
    }
    // 双保险：震度速報是「地震结束之后」的产物。万一服务端抢先塞过来，
    // 也等终报落定再上屏，免得两张卡抢同一个位置。
    if (o.sim) {
      const ev = displayEvent();
      if (ev && !quakeSettled(ev)) { setObs(false); return; }
    }
    if (o.origin_ts && obsDismissedTs === o.origin_ts) {
      setObs(false);
      return;
    }
    // 半小时前的速报没必要一直挂着
    if (nowSec() - (o.report_ts || o.origin_ts || 0) > 1800) {
      setObs(false);
      return;
    }
    const key = [o.origin_ts, o.report_ts, o.max_scale, o.point_count].join('|');
    if (key === obsCardKey) { setObs(true); return; }
    obsCardKey = key;

    const risk = SCALE_RISK[o.max_scale] || 0;
    $('#obsNo').textContent = '第 ' + (o.issue_no || 1) + ' 号';
    $('#obsIssue').textContent = o.issue_str || '';
    $('#obsQuake').textContent = o.quake_str || ('震源 ' + (o.place || '不明'));
    const maxEl = $('#obsMax');
    maxEl.textContent = o.max_label || '—';
    maxEl.style.color = (RISK && RISK[risk]) || '#eaf4ff';
    $('#obsWhere').textContent = o.place || '';
    const groups = obsRegionsOf(o);
    $('#obsList').innerHTML = groups.length
      ? groups.map(g => '<div class="obs-grp"><h4>震度' + g.label + '以上を観測した地域</h4><p>' +
        (g.prefs.join('、') || '—') + '</p></div>').join('')
      : '<p class="obs-none">震度3以上の地域はありません</p>';
    $('#obsFoot').textContent = o.sim
      ? '※ 演练推算 · 与気象庁「震度速報」同构，只报地域不给观测点'
      : o.source === 'jma_sokuho'
        ? '気象庁「震度速報」· 地域级，观测点尚未发布'
        : '気象庁「震度速報」· 报到观测点 ' + (o.point_count || 0) + ' 处';
    setObs(true);
  }

  // 「当前主源」：每 10 秒由服务端按最近几次延迟的中位数重算一次，
  // 同类同国之间比（日本速报不会因为中国源快 200ms 就被顶掉）。
  function primaryOf(id) {
    const p = state.primary || {};
    for (const kind in p) {
      const grp = p[kind] || {};
      for (const c in grp) if (grp[c] && grp[c].id === id) return grp[c];
    }
    return null;
  }
  function primaryList() {
    const p = state.primary || {};
    const out = [];
    for (const kind in p) {
      const grp = p[kind] || {};
      for (const c in grp) if (grp[c]) out.push(grp[c]);
    }
    return out;
  }

  function esc(s) {
    return String(s).replace(/[&<>"]/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  }

  // GQ 主备两台各自的体检结果：鼠标停在「备」角标上就能看到谁怎么死的。
  // 官方客户端也是这套双机配置（client.properties 的 primary/secondary）。
  function gqHostTip(s) {
    const hs = s.hosts || [];
    const part = hs.map(h => (h.name || h.host) + ' ' + h.host + '：' +
      (h.ok === true ? '连通' + (h.last_ok_str ? ' ' + h.last_ok_str : '')
        : h.ok === false ? '失败 ' + (h.fails || 1) + ' 次' +
          (h.last_err ? '（' + h.last_err + '）' : '')
          : '未试') +
      (h.probe_ok ? ' · 体检 ' + (h.probe_ms || 0) + 'ms' : ''));
    const head = s.manual
      ? '用户手动选的是' + (s.role || '主服务器') +
        (s.manual_str ? '（' + s.manual_str + '）' : '')
      : (s.role && s.role.indexOf('备') >= 0)
        ? '主服务器抽风，已自动切到备用' +
          (s.last_switch_str ? '（' + s.last_switch_str + '）' : '')
        : '正在用' + (s.role || '主服务器');
    return head + ' · ' + part.join(' ｜ ') +
      (s.switch_count ? ' · 累计切换 ' + s.switch_count + ' 次' : '');
  }

  // GQ 服务器手动切换芯片：主 / 备，点哪个用哪个；右边一个「自动」开关
  function gqSwitchHtml(s) {
    const hs = s.hosts || [];
    if (!hs.length) return '';
    const short = n => (n || '').indexOf('备') >= 0 ? '备'
      : (n || '').indexOf('自') >= 0 ? '自' : '主';
    const chips = hs.map((h, i) => {
      const cur = i === (s.host_idx || 0);
      const tip = (h.name || h.host) + ' · ' + h.host + ':' + (h.port || 38000) + '\n' +
        (cur ? '正在使用' : '点一下切到这台') +
        (h.probe_ok ? '（体检 ' + (h.probe_ms || 0) + 'ms'
          + (h.probe_str ? ' · ' + h.probe_str : '') + '）'
          : '（还没体检）');
      return '<i class="gqsw' + (cur ? ' on' : '') + '" data-gqswap="' + i +
        '" title="' + esc(tip) + '">' + short(h.name) + '</i>';
    }).join('');
    const autoTip = '自主切换：' + (s.auto
      ? '开 · 启动时挑最快的、断了自动换、主机恢复后自动切回'
      : '关 · 只做故障切换') + '\n点一下' + (s.auto ? '关掉' : '打开');
    return '<span class="gqswrap">' + chips +
      '<i class="gqauto' + (s.auto ? ' on' : '') + '" data-gqauto="1" title="' +
      esc(autoTip) + '">自动</i></span>';
  }

  function renderSrcTable() {
    const body = $('#srcTable');
    if (!body) return;
    // 按延迟升序：谁最快排最上面，主源一眼就在第一行
    const list = state.sources.slice().sort((a, b) => {
      const av = a.state === 'ok' ? (a.latency_ms || 9e9) : 9e9 + (a.state === 'noauth' ? 1e6 : 0);
      const bv = b.state === 'ok' ? (b.latency_ms || 9e9) : 9e9 + (b.state === 'noauth' ? 1e6 : 0);
      return av - bv;
    });
    const rows = list.map(s => {
      // 「主」只标预警与观测两类：目录（震后测定）比延迟没意义，
      // 它们本来就是几十秒后才更新的东西。
      const main = (s.kind === 'eew' || s.kind === 'obs') ? primaryOf(s.id) : null;
      // GQ 主备：官方有两台（server / server-backup），客户端自己会切。
      // 切到备用时挂个「备」角标，鼠标悬停能看两台各自的体检结果。
      const alt = (s.id === 'globalquake' && s.role && s.role.indexOf('备') >= 0)
        ? '<i class="palt" title="' + esc(gqHostTip(s)) + '">备</i>' : '';
      const gqsw = (s.id === 'globalquake') ? gqSwitchHtml(s) : '';
      const st = s.state === 'ok' ? ['ok', '正常' + (s.latency_ms ? ' ' + s.latency_ms + 'ms' : '')]
        : s.state === 'wip' ? ['wait', '接入中']
          : s.state === 'noauth' ? ['wait', '待授权']
          : s.state === 'down' ? ['err', '失败']
            : s.state === 'stale' ? ['warn', '缓存 ' + Math.round(s.age_s || 0) + 's']
              : ['warn', '连接中'];
      return '<tr' + (main ? ' class="is-main"' : '') + '>' +
        '<td>' + (SRC_TITLE[s.id] || s.name_zh) +
        (main ? '<i class="pmain">主</i>' : '') + alt + gqsw + '</td>' +
        '<td class="pr">' + (s.provider || 'Wolfx') + '</td>' +
        '<td class="st">' + (s.poll || '—') + 's</td>' +
        '<td class="st"><i class="sdot ' + st[0] + '"></i>' + st[1] + '</td></tr>';
    });
    rows.push('<tr><td>历史目录 FDSN</td><td class="pr">USGS</td>' +
      '<td class="st">按需</td><td class="st"><i class="sdot ok"></i>可查询</td></tr>');
    body.innerHTML = rows.join('');
  }

  function renderAll() {
    renderDots();
    renderHero();
    renderRecent();
    updateLegendCap();
    renderSrcTable();
    renderTsuAlert();
    renderObsLpgm();
  }

  // ---------------- 数据 ----------------
  function applyState(data) {
    if (!data || !data.ts) return;
    state.offset = data.ts - Date.now() / 1000;
    state.sources = data.sources || [];
    state.eew = data.eew || { JP: null, CN: null, GQ: null };
    state.eewList = data.eew_list || [];
    state.gqAct = data.gq_act || [];
    state.events = data.events || [];
    state.alerts = data.alerts || [];
    state.obs = data.obs || null;
    state.tsunami = data.tsunami || null;
    state.lpgm = data.lpgm || null;
    announceObs(state.obs);
    try { maybeSokuho(); } catch (e) { }
    // 每 10 秒重算一次的「延迟最低源」+ 台湾 P-Alert 测站实时 PGA
    state.primary = data.primary || {};
    state.palert = data.palert || null;
    // 演练直接跟随服务端：服务端有就给、没有就清。不再有本地计时状态，
    // 也就不存在「残留计时器把新演练判成过期」这类问题
    state.sim = data.sim || null;
    state.simSpeed = data.sim_speed || 1;
    state.connected = true;
    $('#mapStatus').textContent = '实时 · ' + fmtClock(data.ts, 8 * 3600) + ' 北京';
    $('#dlgVer').textContent = 'v' + (data.version || '');
    $('#verChip').textContent = 'v' + (data.version || '');

    // 每个环节独立容错：任何一个抛错（比如 TTS/语音包异常）都不能打断
    // 状态链，否则之后所有更新全部失效——表现就是「界面再也不动了」
    // 播报/警报只认「自动选中的那起」：用户手动切到旧事件时，新地震仍然会报
    try { checkAlarm(); } catch (e) { }
    try { announceEvent(primaryEvent()); } catch (e) { }
    try { followView(); } catch (e) { }
    try { renderAll(); } catch (e) { }
  }

  // ---------------- 视野跟随：有地震飞过去，没地震回中日全景 ----------------
  // 以前只有「飞过去」：最后一报收卡后镜头永远停在震中，下次看到还是智利外海。
  // 这里补上归位：收卡后等 10 秒，没有新事件就飞回东亚全景。
  // 那 10 秒是留给余震和续报的，免得两报之间来回飞。
  const HOME_HOLD = 10000;
  let homeTimer = 0, atHome = true, homeHoldUntil = 0;

  // 用户自己拖过/缩过：5 秒内不抢镜头，别在人家看细节时突然飞走
  function noteUserView() {
    homeHoldUntil = performance.now() + 5000;
    if (homeTimer) { clearTimeout(homeTimer); homeTimer = 0; }
  }

  function flyHome(dur) {
    lastFocusUid = '';                 // 同一场地震下一报还能重新居中
    atHome = true;
    flyTo(HOME_MID.lat, HOME_MID.lon, homeScale(), dur || 900);
  }

  function followView() {
    if (!state.settings.follow || state.historyEvent) return;
    // 用户手动关了事件卡 = 他想自己看地图，这时候别再倒计时把人拉回全景
    if (state.dismissedKey && nowSec() < state.hiddenUntil) return;
    const ev = displayEvent();
    if (ev) {
      if (homeTimer) { clearTimeout(homeTimer); homeTimer = 0; }
      atHome = false;
      focusOnEvent(ev);
      return;
    }
    if (atHome || homeTimer) return;             // 已经在全景 / 已在倒计时
    if (performance.now() < homeHoldUntil) return;
    homeTimer = setTimeout(() => {
      homeTimer = 0;
      if (displayEvent() || state.historyEvent) return;   // 期间又震了
      flyHome(900);
    }, HOME_HOLD);
  }

  let lastFocusUid = '';
  function focusOnEvent(ev, force) {
    if (!ev || !ev.lat || !ev.lon) return;
    if (!force && ev.uid === lastFocusUid) return;
    lastFocusUid = ev.uid;
    // 尺度按「离得远不远」分两档：
    //  · 中日台的事：保留当前缩放（已经放大在看细节就别退回去），至少东亚全景 ×1.6；
    //  · 全球检测：固定回到大区域视野，否则从日本县一级飞智利会落在荒郊野外，
    //    屏幕上只剩一个点，连海陆都分不清。
    // 推算的弱震有感范围只有几十公里，所以给它 5× 的地区级视野。
    const near = ev.auto || ev.country === 'JP' || ev.country === 'CN' || ev.country === 'TW';
    const sTo = ev.auto ? homeScale() * 5
      : near ? Math.max(view.s, homeScale() * 1.6)
        : homeScale() * 1.1;
    flyTo(ev.lat, ev.lon, sTo, 620);
    estKey = '';
  }

  // ---------------- 桌面通知 / 标题闪烁 ----------------
  // 页面在后台（尤其最小化）时，事件卡是看不见的。用浏览器通知补一条
  // 「出了屏幕也能注意到」的通道；没授权就只闪标签标题，不做静默失败。
  const NOTIFY = { asked: false, lastAt: 0, timer: 0, base: '震线 EEW', recent: [] };

  function notifyReady(ask) {
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (ask && Notification.permission === 'default' && !NOTIFY.asked) {
      NOTIFY.asked = true;
      try { Notification.requestPermission(); } catch (e) { }
    }
    return false;
  }

  function notifyEvent(ev, why) {
    if (!state.settings.notify || !ev) return;
    if (quietBlocks(ev)) return;      // 夜间免打扰时段不弹系统通知
    // 同一场地震只弹一次：用「时刻±90s + 震中 0.8°」判同源，
    // 而不是按 uid——JMA 和 GQ 对同一场地震的 uid 编法完全不同。
    // （以前这里还有一条 1.2 秒全局节流，两起不同地震同时到时，
    //   后一条会被直接丢掉且已记进 seen，等于永远不补发。）
    const now = nowSec();
    if (NOTIFY.recent.some(r => now - r.at < 90 && sameQuake(r.ev, ev))) return;
    NOTIFY.recent = NOTIFY.recent.filter(r => now - r.at < 300);
    NOTIFY.recent.push({ ev, at: now });
    const label = ev.kind === 'gq'
      ? '全球地震检测'
      : ev.country === 'JP' ? (ev.warn ? '緊急地震速報' : '緊急地震速報・予報')
        : ev.country === 'TW' ? '地震速報（中国台湾）' : '地震预警';
    flashTitle('M' + (ev.mag || 0).toFixed(1) + ' ' + (ev.place || ''));
    if (!notifyReady(false)) return;
    try {
      const n = new Notification('震线 EEW · ' + label + ' M' + (ev.mag || 0).toFixed(1), {
        body: (ev.place || '震源調査中') + ' · 深 ' + Math.round(ev.depth || 10) + 'km' +
          (ev.gq_quality ? ' · 质量 ' + ev.gq_quality : '') + (why ? '\n' + why : ''),
        tag: 'eew-' + ev.uid, renotify: true, requireInteraction: true, silent: true,
      });
      n.onclick = () => {
        try { window.focus(); } catch (e) { }
        state.focusUid = ev.uid;
        state.historyEvent = null;
        try { renderAll(); } catch (e) { }
        n.close();
      };
    } catch (e) { }
  }

  function flashTitle(msg) {
    NOTIFY.msg = msg;
    if (NOTIFY.timer) return;                 // 已经在闪：只换文字，不重开定时器
    NOTIFY.base = (document.title || '震线 EEW').replace(/^⚠ /, '');
    let on = false;
    NOTIFY.timer = setInterval(() => {
      document.title = (on = !on) ? '⚠ ' + (NOTIFY.msg || '') : NOTIFY.base;
    }, 900);
  }
  function stopFlash() {
    if (!NOTIFY.timer) return;
    clearInterval(NOTIFY.timer);
    NOTIFY.timer = 0;
    document.title = NOTIFY.base || '震线 EEW';
  }
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) stopFlash();
    else if (state.settings.notify) notifyReady(true);
  });
  // 通知权限必须在「用户手势」里申请，页面加载时直接问会被浏览器丢掉。
  // 第一次点任意位置时顺手问一次，之后不再打扰。
  document.addEventListener('click', function () {
    if (state.settings.notify) notifyReady(true);
  }, { once: true });

  function checkAlarm() {
    // 逐起事件判定，而不是只看「当前主卡的那一起」：
    // 日本有 M3 在报时，主卡被它占着（本区域加权 +2.5），智利 M6 抢不到主卡，
    // 于是永远轮不到响铃——这就是「GQ 预警收不到」的另一半原因。
    // 主卡只有一张，但「该不该提醒」是每起地震自己的事。
    const sim = liveSim();
    const cands = sim
      ? [Object.assign({}, sim, { active: true, isSim: true })]
      : activeEvents().filter(headlineWorthy);
    if (!cands.length) return;
    let fired = false, dirty = false;
    for (const ev of cands) {
      const key = evKey(ev);
      if (state.seen.has(key)) continue;
      state.seen.add(key);
      dirty = true;
      // 多关注点：任一点达阈值就算「影响我这里」，取最坏的那个
      const w = worstEst(ev);
      const est = w.est;
      const atOther = w.point !== state.focus;
      // 夜间免打扰：时段内的小震不响铃、不弹通知（卡片照样更新）
      const hush = quietBlocks(ev);
      // 同一场地震的警报音只响一次：只有「影响我这里」的震度真的变强一档才再响，
      // 否则 7 条续报会把同一段警报音播 7 遍（听起来就是重复）
      const aKey = String(ev.origin_ts || ev.uid);
      const already = state.lastAlarmKey === aKey && est.risk <= (state.lastAlarmRisk || 0);
      const th = usesShindo(ev) ? Number(state.settings.thJp) : Number(state.settings.thCn);
      // 全球检测（GQ）没有「本地震度」这回事：智利 M7 在关注点永远是 0 度，
      // 按日本阈值走就等于一辈子不响。它按震级门槛单独判定。
      const gqAlert = ev.kind === 'gq' &&
        (ev.mag || 0) >= Number(state.settings.gqAlert ?? 4.5);
      // 日本/台湾的「緊急地震速報」本身就是警报：卡片照弹，桌面通知照发，
      // 只是本地预估不够时不出警报音（免得天天被远处的 M4 吵）。
      const warnOnly = !!(ev.warn || ev.country === 'TW') && !ev.cancel &&
        (ev.country === 'JP' || ev.country === 'TW');
      const where = atOther ? '@' + w.point.name + ' ' : '';
      const why = ev.kind === 'gq'
        ? '全球台网检测 · ' + where + '预估' + est.label
        : where + '预估' + est.label + ' · 距 ' + Math.round(est.R) + 'km';
      if (!ev.isSim && !gqAlert && est.val < th) {
        if (warnOnly) notifyEvent(ev, '预估' + est.label + '（本地未达阈值）');
        continue;
      }
      if (ev.training && !ev.isSim) continue;
      if (already) continue;
      if (hush) continue;          // 免打扰时段：不响、不弹，只更新界面
      // 警报音一次只响一条（多条同时到就按优先级取第一条），
      // 但桌面通知每起都发——通知是「谁在摇」，不是「响一声」。
      notifyEvent(ev, why);
      if (fired) continue;
      if (!state.settings.sound && !state.settings.speech) continue;
      state.lastAlarmKey = aKey;
      state.lastAlarmAt = nowSec();
      state.lastAlarmRisk = est.risk;
      alarm(est.risk);
      fired = true;
    }
    if (dirty) LS.set('seen', Array.from(state.seen).slice(-300));
  }

  function connect() {
    if (window.EventSource) {
      const es = new EventSource('/api/stream');
      es.onmessage = e => { try { applyState(JSON.parse(e.data)); } catch (err) { } };
      es.onerror = () => {
        state.connected = false;
        $('#mapStatus').textContent = '连接中断，重试中…';
        fetch('/api/me', { cache: 'no-store' }).then(r => {
          if (r.status === 401) location.replace('/login');
        }).catch(() => { });
      };
    }
    poll();                    // 先立刻拉一次，别让界面空等 5 秒的「连接中…」
    setInterval(poll, 5000);
  }
  async function poll() {
    try {
      const r = await fetch('/api/state', { cache: 'no-store' });
      if (r.status === 401) { location.replace('/login'); return; }
      applyState(await r.json());
    } catch (e) {
      $('#mapStatus').textContent = '无法连接本地服务';
    }
  }

  // ---- 登录态 ----
  async function loadMe() {
    try {
      const r = await fetch('/api/me', { cache: 'no-store' });
      if (r.status === 401) { location.replace('/login'); return; }
      const d = await r.json();
      if (d.user) {
        const name = d.user.name;
        const last = localStorage.getItem('eew.user') || '';
        if (last !== name) {
          // 旧版全局键迁移给第一个登录的账号；之后每个账号各自一套
          const pre = 'eew.' + name + '.';
          const hasNs = Object.keys(localStorage).some(k => k.startsWith(pre));
          if (!hasNs) {
            const legacy = Object.keys(localStorage).filter(k =>
              /^eew\.[^.]+$/.test(k) && k !== 'eew.user');
            for (const k of legacy) {
              try { localStorage.setItem(pre + k.slice(4), localStorage.getItem(k)); } catch (e) { }
            }
            for (const k of legacy) { try { localStorage.removeItem(k); } catch (e) { } }
          }
          localStorage.setItem('eew.user', name);
          if (!sessionStorage.getItem('eew.ns')) {
            sessionStorage.setItem('eew.ns', '1');
            location.reload();
            return;
          }
        }
        state.me = d.user.name;
        // 只有「站点所有者」有账号管理；其余账号只看到自己的数据源密钥。
        const isOwner = !!d.user.owner;
        state.owner = !!d.user.owner;
        $('#userName').textContent = d.user.name + (d.user.owner ? ' · 所有者' : '');
        $('#userChip').hidden = false;
        const ae = $('#adminEntry');
        if (ae) ae.hidden = !isOwner;
      }
    } catch (e) { }
  }
  $('#btnLogout').addEventListener('click', async () => {
    try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { }
    location.replace('/login');
  });
  async function loadStations() {
    try {
      const r = await fetch('/api/stations');
      const d = await r.json();
      state.stations = d.stations || [];
      state.builtin = d.builtin || 0;
      $('#builtinCount').textContent = state.builtin;
      $('#customCount').textContent = (state.stations.length - state.builtin) + ' 个自定义';
    } catch (e) { }
  }

  // ---------------- kmoni 逐秒取色循环 ----------------
  // 服务端代理 GIF → 浏览器原生解码 → 逐台站取色得到实时计测震度
  const KMONI_IMG_W = 352, KMONI_IMG_H = 400;
  const kmCanvas = document.createElement('canvas');
  kmCanvas.width = KMONI_IMG_W; kmCanvas.height = KMONI_IMG_H;
  const kmCtx = kmCanvas.getContext('2d', { willReadFrequently: true });
  const kmImg = new Image();

  async function kmoniLoop() {
    let lastTs = '';
    while (true) {
      try {
        // 条件轮询：帧号没变服务端只回 204（几乎零开销），所以可以 350ms 一问，
        // 新帧一生成（服务端逐秒抓）立刻就能拿到，整条链路的显示延迟 ≤1.4s
        const r = await fetch('/api/kmoni?ts=' + encodeURIComponent(lastTs), { cache: 'no-store' });
        if (r.status === 204) { /* 还没新帧，稍后再问 */ }
        else if (r.ok) {
          lastTs = r.headers.get('X-Kmoni-Ts') || lastTs;
          const blob = await r.blob();
          const url = URL.createObjectURL(blob);
          await new Promise((res, rej) => {
            kmImg.onload = res; kmImg.onerror = () => rej(new Error('img decode'));
            kmImg.src = url;
          });
          URL.revokeObjectURL(url);
          if (kmImg.width !== KMONI_IMG_W || kmImg.height !== KMONI_IMG_H)
            throw new Error('size ' + kmImg.width + 'x' + kmImg.height);
          kmCtx.drawImage(kmImg, 0, 0);
          const d = kmCtx.getImageData(0, 0, KMONI_IMG_W, KMONI_IMG_H).data;
          const lv = {};
          let n = 0;
          for (const st of state.stations) {
            // 海底站不在这里取色：jma_s 震度图上整片海域是全黑（实测扫过，没有海底符号），
            // 硬取只会读到岸边陆站的像素。海底站的震动用推算值（见 stationInfoHtml）。
            if (st.country !== 'JP' || st.sea) continue;
            const [bx, by] = kmPixel(st.lon, st.lat);
            if (bx < 0 || by < 0 || bx >= KMONI_IMG_W || by >= KMONI_IMG_H) continue;  // 图幅外（冲绳）
            // 逐台站取色：先打正中心，打空就在 1~2 px 环上找最近的实心像素
            let v = null;
            for (const [ox, oy] of KM_RING) {
              const x = bx + ox, y = by + oy;
              if (x < 0 || y < 0 || x >= KMONI_IMG_W || y >= KMONI_IMG_H) continue;
              const i = (y * KMONI_IMG_W + x) * 4;
              if (d[i + 3] < 128) continue;                 // 透明 = 此点无数据
              v = kmoniValOf(d[i], d[i + 1], d[i + 2]);
              if (v !== null && v !== undefined) break;
            }
            if (v !== null && v !== undefined) {
              lv[st.code] = v; n++;
              histPush(st.code, v, nowSec());
            }
          }
          if (n > 800)   // 命中过少说明取色坐标或图片异常，丢弃这帧
            state.kmoni = { ok: true, at: nowSec(), ts: lastTs, lv, n };
          else state.kmoni.n = n;   // 保留上一帧，顺手记下这次的命中数便于排查
          window.__km = { ts: lastTs, n: n, at: state.kmoni.at, ok: state.kmoni.ok };
        } else throw new Error('http ' + r.status);
      } catch (e) {
        state.kmoni.ok = false;   // 数据老化，绘制自动回退到原有模式
      }
      await new Promise(r2 => setTimeout(r2, 350));
    }
  }

  // ---------------- 历史 ----------------
  async function openSheet() {
    $('#sheet').hidden = false;
    await loadHistory();
  }
  function closeSheet() { $('#sheet').hidden = true; }

  async function loadHistory() {
    if (state.history.loading) return;
    state.history.loading = true;
    $('#histHint').textContent = '加载中…';
    try {
      const q = 'days=' + state.history.days + '&minmag=' + state.history.minmag + '&limit=600';
      const r = await fetch('/api/history?' + q, { cache: 'no-store' });
      const d = await r.json();
      state.history.items = d.items || [];
      $('#histHint').textContent = d.count + ' 条 · 本地 ' + d.archive + ' · USGS ' + d.usgs;
      renderHistory();
    } catch (e) {
      $('#histHint').textContent = '加载失败，稍后重试';
    }
    state.history.loading = false;
  }

  function renderHistory() {
    const box = $('#histList');
    const items = state.history.items || [];
    if (!items.length) {
      box.innerHTML = '<div style="padding:24px;text-align:center;color:#8b95a5">该条件下没有地震</div>';
      return;
    }
    box.innerHTML = items.map(e => {
      const on = state.historyEvent && state.historyEvent.uid === e.uid ? ' on' : '';
      const cc = e.country === 'JP' ? '日' : e.country === 'CN' ? '中' : e.country === 'TW' ? '台' : '外';
      const cls = e.country === 'JP' ? 'jp' : (e.country === 'CN' || e.country === 'TW') ? 'cn' : 'ot';
      return '<div class="hrow' + on + '" data-uid="' + e.uid + '">' +
        '<span class="tag ' + cls + '">' + cc + '</span>' +
        '<div class="body"><div class="l1">' + (e.place || '—') + '</div>' +
        '<div class="l2"><span>' + fmtShort(e.origin_ts, tzOf(e)) + '</span>' +
        '<span>深 ' + Math.round(e.depth || 0) + 'km</span>' +
        (e.source === 'usgs' ? '<span>USGS</span>' : '') + '</div></div>' +
        '<span class="mag">' + (e.mag || 0).toFixed(1) + '</span></div>';
    }).join('');
  }

  function selectHistory(uid) {
    const e = (state.history.items || []).find(x => x.uid === uid);
    if (!e) return;
    state.historyEvent = e;
    state.histReplayAt = 0;
    lastFocusUid = '';
    focusOnEvent(e, true);
    closeSheet();
    renderAll();
    toast('历史回看 · ' + (e.place || ''));
  }

  // ---------------- 设置 ----------------
  const dlg = $('#settingsDlg');
  const FOCUS_PRESETS = [
    ['北京', 39.904, 116.407, 'CN'], ['上海', 31.230, 121.474, 'CN'],
    ['成都', 30.572, 104.066, 'CN'], ['昆明', 25.038, 102.718, 'CN'],
    ['東京', 35.690, 139.692, 'JP'], ['大阪', 34.686, 135.520, 'JP'],
    ['札幌', 43.062, 141.354, 'JP'], ['福岡', 33.606, 130.418, 'JP'],
  ];
  function paintFocus() {
    $('#focusPick').innerHTML = FOCUS_PRESETS.map(p =>
      '<button type="button" class="fp' + (state.focus.name === p[0] ? ' on' : '') +
      '" data-name="' + p[0] + '" data-lat="' + p[1] + '" data-lon="' + p[2] + '" data-cc="' + p[3] + '">' + p[0] + '</button>').join('');
    $('#curFocus').textContent = state.focus.name + '  ' + state.focus.lat.toFixed(3) + ', ' + state.focus.lon.toFixed(3);
  }
  // ---------------- 多关注点 / 夜间免打扰 ----------------
  function focusPoints() {
    const out = [state.focus];
    for (const w of (state.watches || [])) {
      if (!w || !isFinite(w.lat) || !isFinite(w.lon)) continue;
      if (Math.abs(w.lat - state.focus.lat) < 0.01 &&
        Math.abs(w.lon - state.focus.lon) < 0.01) continue;      // 和主点重合就别重复算
      out.push(w);
    }
    return out.slice(0, 4);
  }

  // 所有关注点里摇得最厉害的那个：告警判定看它，卡片大数字仍显示主点
  function worstEst(ev) {
    let best = null;
    for (const p of focusPoints()) {
      const e = estimate(srcOf(ev), p.lat, p.lon);
      if (!best || e.val > best.est.val) best = { point: p, est: e };
    }
    return best;
  }

  // 卡片里的「其他关注点」一行：只显示主点以外、且真的会摇到的点（≥ 震度 1/烈度 1）
  function paintHeroWatch(ev, hist) {
    const box = $('#heroWatch'); if (!box) return;
    const pts = focusPoints().slice(1);
    if (!pts.length || hist) { box.hidden = true; box.innerHTML = ''; return; }
    const src = srcOf(ev);
    const th = usesShindo(ev) ? Number(state.settings.thJp) : Number(state.settings.thCn);
    const chips = pts.map(p => {
      const e = estimate(src, p.lat, p.lon);
      // 中国烈度用罗马数字，1 度也值得显示；日本震度 0 就别占位了
      const show = usesShindo(ev) ? e.val >= 0.5 : e.val >= 1;
      if (!show) return '';
      const hot = e.val >= th;
      return '<span class="wch' + (hot ? ' hot' : '') + '">' + esc(p.name) +
        '<b>' + e.label + '</b></span>';
    }).filter(Boolean).join('');
    box.innerHTML = chips;
    box.hidden = !chips;
  }

  function paintWatches() {
    const box = $('#watchList'); if (!box) return;
    box.innerHTML = (state.watches || []).map((w, i) =>
      '<span class="wtag">' + esc(w.name) +
      '<em>' + w.lat.toFixed(2) + ',' + w.lon.toFixed(2) + '</em>' +
      '<button type="button" data-i="' + i + '" title="移除">×</button></span>').join('');
  }

  function addWatch(name, lat, lon) {
    state.watches = state.watches || [];
    if (!isFinite(lat) || !isFinite(lon)) return;
    if (state.watches.length >= 3) { toast('最多再加 3 个关注点'); return; }
    if (Math.abs(lat - state.focus.lat) < 0.02 && Math.abs(lon - state.focus.lon) < 0.02) {
      toast('这就已经是主关注点了'); return;
    }
    if (state.watches.some(w => Math.abs(w.lat - lat) < 0.02 && Math.abs(w.lon - lon) < 0.02)) {
      toast('这个点已经加过了'); return;
    }
    state.watches.push({ name: name || '关注点', lat, lon });
    LS.set('watches', state.watches);
    paintWatches();
    renderAll();
    toast('已添加关注点：' + (name || '关注点'));
  }

  // 夜间免打扰：时段内低于门槛的事件不响铃、不弹桌面通知（卡片照常刷）
  function inQuietHours() {
    const st = state.settings;
    if (!st.quiet) return false;
    const from = ((Number(st.quietFrom) || 0) % 24 + 24) % 24;
    const to = ((Number(st.quietTo) || 0) % 24 + 24) % 24;
    if (from === to) return false;
    const h = new Date().getHours();
    return from < to ? (h >= from && h < to) : (h >= from || h < to);
  }
  function quietBlocks(ev) {
    return inQuietHours() && (ev.mag || 0) < (Number(state.settings.quietMag) || 5.5);
  }

  function setFocus(name, lat, lon, country) {
    state.focus = { name, lat, lon, country: country || (lon > 128 && lat > 20 && lat < 46 ? 'JP' : 'CN') };
    LS.set('focus', state.focus);
    paintFocus();
    paintWatches();
    renderAll();
    toast('关注地点：' + name);
  }

  $('#focusPick').addEventListener('click', e => {
    const b = e.target.closest('.fp'); if (!b) return;
    setFocus(b.dataset.name, +b.dataset.lat, +b.dataset.lon, b.dataset.cc);
  });
  $('#btnFocusFind').addEventListener('click', () => {
    const q = $('#focusSearch').value.trim();
    if (!q) return;
    const hit = state.stations.find(s => s.name === q) ||
      state.stations.find(s => s.name.includes(q) || q.includes(s.name));
    if (hit) setFocus(hit.name, hit.lat, hit.lon, hit.country);
    else toast('没找到「' + q + '」，试试城市名或直接用坐标');
  });
  $('#focusSearch').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('#btnFocusFind').click(); }
  });
  $('#btnFocusCoord').addEventListener('click', () => {
    const lat = parseFloat($('#focusLat').value), lon = parseFloat($('#focusLon').value);
    if (!isFinite(lat) || !isFinite(lon)) { toast('请输入有效经纬度'); return; }
    setFocus('自定义点', lat, lon);
  });

  // ---- 备选关注点（最多 3 个）----
  $('#btnWatchAdd').addEventListener('click', () => {
    const q = $('#watchSearch').value.trim();
    if (!q) return;
    const m = q.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,，\s]\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (m) {
      const lat = +m[1], lon = +m[2];
      // 坐标输入没名字：就近找 60km 内的已知台站/城市借用一个，找不到才叫「关注点」
      let name = '关注点', bd = 60;
      for (const st of state.stations) {
        const d = haversine(lat, lon, st.lat, st.lon);
        if (d < bd) { bd = d; name = st.name; }
      }
      addWatch(name, lat, lon);
      $('#watchSearch').value = '';
      return;
    }
    const hit = state.stations.find(x => x.name === q) ||
      state.stations.find(x => x.name.includes(q) || q.includes(x.name));
    if (hit) { addWatch(hit.name, hit.lat, hit.lon); $('#watchSearch').value = ''; }
    else toast('没找到「' + q + '」，试试城市名或「纬度,经度」');
  });
  $('#watchSearch').addEventListener('keydown', e => {
    if (e.key === 'Enter') { e.preventDefault(); $('#btnWatchAdd').click(); }
  });
  $('#watchList').addEventListener('click', e => {
    const b = e.target.closest('button[data-i]'); if (!b) return;
    state.watches.splice(+b.dataset.i, 1);
    LS.set('watches', state.watches);
    paintWatches();
    renderAll();
  });

  // ---------------- 复制事件文字 ----------------
  function eventText(ev) {
    if (!ev) return '';
    const est = estimate(srcOf(ev), state.focus.lat, state.focus.lon);
    const kind = ev.kind === 'gq' ? '全球地震检测'
      : ev.country === 'JP' ? (ev.warn ? '緊急地震速報' : '緊急地震速報・予報')
        : ev.country === 'TW' ? '地震速報（中国台湾）' : '地震预警';
    const scale = usesShindo(ev) ? '震度' : '烈度';
    const maxi = ev.max_intensity && ev.max_intensity !== '?' ? ' · 最大' + scale + ev.max_intensity : '';
    return [
      '【' + kind + '】' + (ev.place || '震源調査中'),
      fmtShort(ev.origin_ts, 480) + ' 北京时 · M' + (ev.mag || 0).toFixed(1) +
      ' · 深 ' + Math.round(ev.depth || 10) + 'km' + maxi,
      state.focus.name + ' 预计' + scale + est.label + ' · 距 ' + Math.round(est.R) + 'km' +
      (est.ts > 0 ? ' · 主要动 ' + Math.max(0, Math.round(est.ts - elapsedOf(ev))) + 's' : ''),
      '震线 EEW · 自动生成，非官方报文',
    ].join('\n');
  }

  async function copyText(txt) {
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(txt);
        return true;
      }
    } catch (e) { }
    // 非 https 地址下 clipboard API 不可用，退回临时 textarea
    try {
      const ta = document.createElement('textarea');
      ta.value = txt;
      ta.style.cssText = 'position:fixed;left:-9999px;top:0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (e) { return false; }
  }

  // ---------------- 统计面板 ----------------
  const statsDlg = $('#statsDlg');
  let statsDays = 7;

  const pad2 = n => String(n).padStart(2, '0');
  function fmtLong(ts) {
    const d = new Date((ts + 480 * 60) * 1000);
    return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate()) +
      ' ' + pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes());
  }

  function drawStatsChart(perDay) {
    const cv = $('#statsChart'); if (!cv) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth || 520, h = 132;
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
    const g = cv.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const n = perDay.length;
    if (!n) return;
    const top = Math.max(1, ...perDay.map(d => d.count));
    const padB = 16, padT = 8;
    const inner = h - padB - padT;
    // 基线 + 顶部刻度
    g.strokeStyle = 'rgba(255,255,255,.10)';
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(0, h - padB + .5);
    g.lineTo(w, h - padB + .5);
    g.stroke();
    g.fillStyle = 'rgba(255,255,255,.35)';
    g.font = '9px ui-monospace,monospace';
    g.fillText(String(top), 0, padT + 7);
    const gap = n > 45 ? 1 : n > 24 ? 2 : 4;
    const bw = Math.max(2, (w - gap * (n - 1)) / n);
    for (let i = 0; i < n; i++) {
      const d = perDay[i];
      const bh = Math.max(d.count ? 3 : 1, inner * (d.count / top));
      const x = i * (bw + gap);
      const grd = g.createLinearGradient(0, h - padB - bh, 0, h - padB);
      grd.addColorStop(0, d.count === top ? '#a8c4ff' : '#6d9bf5');
      grd.addColorStop(1, d.count === top ? '#5b8def' : '#3f66b8');
      g.fillStyle = d.count ? grd : 'rgba(255,255,255,.10)';
      g.fillRect(x, h - padB - bh, bw, bh);
      // 当天最大震级：小橙点，纵轴按 M7 归一
      if (d.max > 0) {
        const y = h - padB - inner * Math.min(1, d.max / 7);
        g.fillStyle = 'rgba(251,146,60,.95)';
        g.beginPath();
        g.arc(x + bw / 2, y, Math.min(2.6, Math.max(1.4, bw / 5)), 0, Math.PI * 2);
        g.fill();
      }
    }
    // 日期刻度：首、中、尾
    g.fillStyle = 'rgba(255,255,255,.3)';
    if (n > 1) {
      const mid = perDay[Math.floor(n / 2)];
      g.textAlign = 'center';
      g.fillText(mid.date.slice(5), w / 2, h - 4);
      g.textAlign = 'left';
    }
  }

  function renderStats(d) {
    if (!d) return;
    statsDataCache = d;
    $('#kpiTotal').textContent = d.total;
    $('#kpiMax').textContent = d.total ? 'M' + (d.max_mag || 0).toFixed(1) : '—';
    $('#kpiAvg').textContent = (d.total / Math.max(1, d.days)).toFixed(1);
    const pd = d.per_day || [];
    const best = pd.reduce((a, b) => (!a || b.count > a.count ? b : a), null);
    $('#kpiMaxDay').textContent = best && best.count ? best.date.slice(5) : '—';
    $('#statsSpan').textContent = pd.length ? pd[0].date + ' → ' + pd[pd.length - 1].date : '';
    drawStatsChart(pd);
    $('#statsAxis').innerHTML = pd.length
      ? '<span>' + pd[0].date.slice(5) + '</span><span>' + pd[pd.length - 1].date.slice(5) + '</span>'
      : '';

    const regs = d.by_region || [];
    const maxR = Math.max(1, ...regs.map(x => x.count));
    $('#statsRegion').innerHTML = regs.length ? regs.map(x =>
      '<div class="sbar"><span>' + esc(x.name) + '</span>' +
      '<i style="width:' + Math.max(2, Math.round(x.count / maxR * 100)) + '%"></i>' +
      '<em>' + x.count + '</em></div>').join('') : '<div class="stats-empty">这段时间没有记录</div>';

    const labels = ['< 3', '3 ~ 4', '4 ~ 5', '5 ~ 6', '6 ~ 7', '≥ 7'];
    const mb = d.mag_buckets || [];
    const maxM = Math.max(1, ...mb);
    $('#statsMag').innerHTML = mb.length ? mb.map((v, i) =>
      '<div class="sbar m"><span>M ' + labels[i] + '</span>' +
      '<i style="width:' + Math.max(2, Math.round(v / maxM * 100)) + '%"></i>' +
      '<em>' + v + '</em></div>').join('') : '<div class="stats-empty">—</div>';

    const t = d.max_event;
    $('#statsTop').innerHTML = t
      ? '<b>M' + (t.mag || 0).toFixed(1) + '</b> · ' + esc(t.place || '—') +
      '<br>' + fmtLong(t.origin_ts) + ' · 深 ' + Math.round(t.depth || 0) + 'km' +
      (t.province ? ' · ' + esc(t.province) : '')
      : '<span class="hint">这段时间没有记录</span>';
  }

  async function loadStats(days) {
    statsDays = days;
    try {
      const r = await fetch('/api/stats?days=' + days, { cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      renderStats(await r.json());
    } catch (e) {
      $('#statsRegion').innerHTML = '<div class="stats-empty">统计加载失败，稍后再试</div>';
    }
  }

  function csvCell(v) {
    const s = String(v == null ? '' : v);
    return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
  }

  async function exportStatsCsv() {
    const btn = $('#btnStatsCsv');
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = '导出中…';
    try {
      const r = await fetch('/api/history?days=' + statsDays + '&minmag=0&limit=2000&local=1',
        { cache: 'no-store' });
      const d = await r.json();
      const items = d.items || [];
      if (!items.length) { toast('这段时间没有可导出的记录'); return; }
      const rows = [['时间(北京)', '震级', '深度km', '地点', '纬度', '经度', '国家']];
      for (const it of items) {
        rows.push([fmtLong(it.origin_ts), (it.mag || 0).toFixed(1), Math.round(it.depth || 0),
        it.place || '', (it.lat || 0).toFixed(3), (it.lon || 0).toFixed(3), it.country || '']);
      }
      const csv = '\ufeff' + rows.map(r2 => r2.map(csvCell).join(',')).join('\r\n');
      const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'eew-' + statsDays + 'd-' + new Date().toISOString().slice(0, 10) + '.csv';
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 5000);
      toast('已导出 ' + items.length + ' 条');
    } catch (e) {
      toast('导出失败，检查服务是否在运行');
    } finally {
      btn.disabled = false; btn.textContent = old;
    }
  }

  // ---------------- 更新日志 ----------------
  // 20 条改动随安装包发一份 data/changelog.json，点开才读，不联网、不占启动时间。
  const logDlg = $('#logDlg');
  let logCache = null;
  function renderLog(d) {
    const entries = (d && d.entries) || [];
    const box = $('#logList');
    if (!box) return;
    $('#logCount').textContent = entries.length + ' 条';
    $('#logFoot').textContent = '最近 ' + entries.length + ' 条 · 随版本内置 · ' +
      (d.updated ? '更新于 ' + d.updated : '');
    box.innerHTML = entries.map(e => {
      const tag = e.ver
        ? '<span class="log-ver">v' + esc(e.ver) + '</span>'
        : '<span class="log-ver infra">' + (e.kind === 'infra' ? '部署' : '早期') + '</span>';
      const date = e.date ? '<span class="log-date">' + esc(e.date) + '</span>' : '';
      const title = e.title ? '<span class="log-title">' + esc(e.title) + '</span>' : '';
      const items = (e.items || []).map(x => '<li>' + esc(x) + '</li>').join('');
      return '<div class="log-item"><div class="log-hd">' + tag + title + date +
        '</div><ul>' + items + '</ul></div>';
    }).join('');
  }
  async function openLog() {
    if (!logDlg) return;
    if (!logDlg.open) logDlg.showModal();
    if (logCache) return;
    try {
      const r = await fetch('/api/changelog', { cache: 'no-store' });
      logCache = await r.json();
      renderLog(logCache);
    } catch (e) {
      $('#logList').textContent = '读取更新日志失败：' + e.message;
    }
  }
  if (logDlg) {
    const b = $('#btnChangelog');
    if (b) b.addEventListener('click', openLog);
    logDlg.addEventListener('click', e => { if (e.target === logDlg) logDlg.close(); });
  }

  if (statsDlg) {
    const openStats = () => {
      if (!statsDlg.open) statsDlg.showModal();
      loadStats(statsDays);
    };
    $('#btnStats').addEventListener('click', openStats);
    $('#statsClose').addEventListener('click', () => statsDlg.close());
    $('#statsDone').addEventListener('click', () => statsDlg.close());
    statsDlg.addEventListener('click', e => { if (e.target === statsDlg) statsDlg.close(); });
    $('#statsDays').addEventListener('click', e => {
      const b = e.target.closest('button[data-days]'); if (!b) return;
      $$('#statsDays button').forEach(x => x.classList.toggle('on', x === b));
      loadStats(parseInt(b.dataset.days, 10) || 7);
    });
    $('#btnStatsCsv').addEventListener('click', exportStatsCsv);
    // 窗口大小变了，柱状图要重画（canvas 宽高是像素值，不会自己跟进）
    window.addEventListener('resize', () => {
      if (statsDlg.open && statsDataCache) drawStatsChart(statsDataCache.per_day || []);
    });
  }
  let statsDataCache = null;

  function bindSwitch(id, key) {
    const el = $(id);
    el.checked = !!state.settings[key];
    el.addEventListener('change', () => {
      state.settings[key] = el.checked;
      LS.set('settings', state.settings);
      syncSound();
    });
  }
  bindSwitch('#optSound', 'sound');
  bindSwitch('#optSpeech', 'speech');
  bindSwitch('#optVoice', 'voice');
  bindSwitch('#optHuman', 'human');
  bindSwitch('#optStHuman', 'stHuman');
  bindSwitch('#optStVoice', 'stVoice');
  bindSwitch('#optStAfterFinal', 'stAfterFinal');
  bindSwitch('#optAutoZoom', 'autoZoom');
  bindSwitch('#optFollow', 'follow');
  bindSwitch('#optQuiet', 'quiet');
  const syncQuietUI = () => {
    const on = !!state.settings.quiet;
    document.querySelectorAll('.quiet-row').forEach(el => el.classList.toggle('off', !on));
    const f = $('#optQuietFrom'), t = $('#optQuietTo'), m = $('#optQuietMag');
    if (f) f.value = String(state.settings.quietFrom ?? 23);
    if (t) t.value = String(state.settings.quietTo ?? 7);
    if (m) m.value = String(state.settings.quietMag ?? 5.5);
  };
  syncQuietUI();
  $('#optQuiet').addEventListener('change', syncQuietUI);
  [['#optQuietFrom', 'quietFrom', 0, 23], ['#optQuietTo', 'quietTo', 0, 23],
  ['#optQuietMag', 'quietMag', 2, 7.5]].forEach(([id, key, lo, hi]) => {
    const el = $(id); if (!el) return;
    el.addEventListener('change', () => {
      let v = parseFloat(el.value);
      if (!isFinite(v)) v = key === 'quietMag' ? 5.5 : (key === 'quietTo' ? 7 : 23);
      v = Math.max(lo, Math.min(hi, v));
      el.value = String(v);
      state.settings[key] = v;
      LS.set('settings', state.settings);
    });
  });
  const ntf = $('#optNotify');
  if (ntf) {
    ntf.checked = state.settings.notify !== false;
    ntf.addEventListener('change', () => {
      state.settings.notify = ntf.checked;
      LS.set('settings', state.settings);
      if (ntf.checked) {
        if (!notifyReady(true) && 'Notification' in window) {
          toast('浏览器拦截了通知权限，请在地址栏左侧允许通知');
        } else {
          toast('桌面通知已开启');
        }
      } else {
        stopFlash();
        toast('桌面通知已关闭');
      }
    });
  }
  const wn = $('#optWaveN');
  if (wn) {
    wn.value = String(state.settings.waveN ?? 10);
    wn.addEventListener('change', e => {
      let v = parseInt(e.target.value, 10);
      if (!isFinite(v)) v = 10;
      v = Math.max(1, Math.min(10, v));
      e.target.value = String(v);
      state.settings.waveN = v;
      LS.set('settings', state.settings);
      toast('同屏波前地震数：' + v + ' 起');
    });
  }
  const optDecay = $('#optDecay');
  optDecay.value = String(state.settings.decay || 1.6);
  $('#decayVal').textContent = (state.settings.decay || 1.6) + '\u00d7';
  optDecay.addEventListener('input', () => {
    state.settings.decay = parseFloat(optDecay.value) || 1.6;
    $('#decayVal').textContent = state.settings.decay + '\u00d7';
    LS.set('settings', state.settings);
  });
  $('#optHuman').addEventListener('change', () => { renderVoiceList(); buildChimeOptions(); });
  $('#optChime').addEventListener('change', e => {
    state.settings.chime = e.target.value;
    LS.set('settings', state.settings);
    const it = pickVoice(e.target.value);
    // 试听优先级 -1：立刻打断正在播的告警，保证点了就听到
    playFiles(it ? [soundPath(it.file)] : ['sounds/builtin.wav'], -1, 'preview', 2);
  });
  $('#voiceList').addEventListener('click', e => {
    const b = e.target.closest('.vitem');
    if (!b) return;
    const it = pickVoice(b.dataset.key);
    if (it) playFiles([soundPath(it.file)], -1, 'preview', 2);
  });

  $('#optThJp').value = String(state.settings.thJp);
  $('#optThCn').value = String(state.settings.thCn);
  $('#optThJp').addEventListener('change', e => { state.settings.thJp = +e.target.value; LS.set('settings', state.settings); });
  $('#optThCn').addEventListener('change', e => { state.settings.thCn = +e.target.value; LS.set('settings', state.settings); });
  const gm = $('#optGqMag');
  if (gm) {
    gm.value = String(state.settings.gqMag ?? 3);
    gm.addEventListener('change', e => {
      let v = parseFloat(e.target.value);
      if (!isFinite(v)) v = 3;
      v = Math.max(1, Math.min(8, v));
      e.target.value = String(v);
      state.settings.gqMag = v;
      LS.set('settings', state.settings);
      toast('全球检测最小震级：M' + v + '（下一次刷新生效）');
    });
  }
  const ga = $('#optGqAlert');
  if (ga) {
    ga.value = String(state.settings.gqAlert ?? 4.5);
    ga.addEventListener('change', e => {
      let v = parseFloat(e.target.value);
      if (!isFinite(v)) v = 4.5;
      v = Math.max(2, Math.min(8, v));
      e.target.value = String(v);
      state.settings.gqAlert = v;
      LS.set('settings', state.settings);
      toast('全球检测 M' + v + ' 以上立即弹卡提醒');
    });
  }

  // ---- 设置双通道保存 ----
  // WebView2 曾开隐私模式（旧版），localStorage 关窗即清——设置全丢。
  // 现在除了 localStorage，还落到服务端 settings.json；启动时服务端有存档就恢复，
  // 之后任何改动 4s 内自动上报，关窗时再用 sendBeacon 兜底发一次。
  const SET_CTRLS = [['#optSound', 'sound'], ['#optSpeech', 'speech'], ['#optVoice', 'voice'],
    ['#optHuman', 'human'], ['#optStHuman', 'stHuman'], ['#optStVoice', 'stVoice'],
    ['#optStAfterFinal', 'stAfterFinal'], ['#optAutoZoom', 'autoZoom'],
    ['#optNotify', 'notify'], ['#optFollow', 'follow'], ['#optQuiet', 'quiet']];
  function syncSettingsUI() {
    for (const [id, key] of SET_CTRLS) { const el = $(id); if (el) el.checked = !!state.settings[key]; }
    const dc = $('#optDecay');
    if (dc) { dc.value = String(state.settings.decay || 1.6); $('#decayVal').textContent = (state.settings.decay || 1.6) + '\u00d7'; }
    const tj = $('#optThJp'), tc = $('#optThCn');
    if (tj) tj.value = String(state.settings.thJp);
    if (tc) tc.value = String(state.settings.thCn);
    const gmEl = $('#optGqMag');
    if (gmEl) gmEl.value = String(state.settings.gqMag ?? 3);
    const gaEl = $('#optGqAlert');
    if (gaEl) gaEl.value = String(state.settings.gqAlert ?? 4.5);
    const wnEl = $('#optWaveN');
    if (wnEl) wnEl.value = String(state.settings.waveN ?? 10);
  }
  let _srvSetMerge = false;
  fetch('/api/settings', { cache: 'no-store' }).then(r => r.json()).then(d => {
    const s = d && d.settings;
    if (!s || typeof s !== 'object' || !Object.keys(s).length) return;
    _srvSetMerge = true;
    Object.assign(state.settings, s);
    bumpDecay(state.settings);          // 服务端存的老 decay:1 也要抬到新基准
    LS.set('settings', state.settings);
    syncSettingsUI();
    if (state.settings.chime) $('#optChime').value = state.settings.chime;
  }).catch(() => { });
  let _lastSavedSet = JSON.stringify(state.settings);
  setInterval(() => {
    const cur = JSON.stringify(state.settings);
    if (cur === _lastSavedSet) return;
    _lastSavedSet = cur;
    LS.set('settings', state.settings);
    fetch('/api/settings', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings: JSON.parse(cur),
        user: localStorage.getItem('eew.user') || '' }) }).catch(() => { });
  }, 4000);
  window.addEventListener('beforeunload', () => {
    try {
      navigator.sendBeacon('/api/settings', new Blob(
        [JSON.stringify({ settings: state.settings,
          user: localStorage.getItem('eew.user') || '' })], { type: 'application/json' }));
    } catch (e) { }
  });

  function syncSound() {
    const on = state.settings.sound || state.settings.speech;
    $('#btnSound').classList.toggle('on', on);
    $('#btnSound').classList.toggle('muted', !on);
  }
  const btnTheme = $('#btnTheme');
  if (btnTheme) btnTheme.addEventListener('click', () => {
    applyTheme(themeNow() === 'light' ? 'dark' : 'light');
  });
  const themeSeg = $('#themeSeg');
  if (themeSeg) themeSeg.addEventListener('click', e => {
    const b = e.target.closest('button[data-theme]');
    if (b) applyTheme(b.dataset.theme);
  });
  $('#btnSound').addEventListener('click', () => {
    state.settings.sound = !state.settings.sound;
    if (state.settings.sound) audio();
    LS.set('settings', state.settings);
    syncSound();
    toast(state.settings.sound ? '声音告警已开启' : '声音告警已关闭');
  });
  // ---- 我的数据源密钥（按账号存，各自一条预警流）----
  async function loadMyTokens() {
    try {
      const d = await (await fetch('/api/mytokens', { cache: 'no-store' })).json();
      const st = $('#myTokState');
      if (!st) return;
      if (d.own) {
        st.textContent = '本账号已配密钥 · ' + (d.jian || '');
        st.className = 'hint mono mytok-state-on';
        $('#myTokInput').placeholder = '已保存 ' + d.jian + '，填新的可替换';
      } else {
        st.textContent = '本账号还没配密钥（不会借用别人的）';
        st.className = 'hint mono mytok-state-off';
        $('#myTokInput').placeholder = 'rt_…（填了才有 Jian 预警）';
      }
    } catch (e) { }
  }
  async function saveMyTokens(tok) {
    let d = {};
    try {
      const r = await fetch('/api/mytokens', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ jian: tok }),
      });
      d = await r.json().catch(() => ({}));
      if (!r.ok || !d.ok) { toast(d.error || '保存失败'); return false; }
    } catch (e) { toast('保存失败：网络异常'); return false; }
    return true;
  }
  const btnTokSave = $('#btnMyTokSave');
  if (btnTokSave) btnTokSave.addEventListener('click', async () => {
    const v = ($('#myTokInput').value || '').trim();
    if (!v) { toast('先填入 rt_ 开头的密钥'); return; }
    if (await saveMyTokens(v)) {
      $('#myTokInput').value = '';
      toast('已保存，正在用你的密钥重连');
      loadMyTokens();
    }
  });
  const btnTokClear = $('#btnMyTokClear');
  if (btnTokClear) btnTokClear.addEventListener('click', async () => {
    if (await saveMyTokens('')) { toast('已清除，回落到共享密钥'); loadMyTokens(); }
  });

  // ---- 本机公开数据源令牌（机器级，所有账号共用，谁登录都能补） ----
  // 这三路（EQSC / TREM-Net / SeedLink）抓的是公开数据，一台机器只跑一份连接，
  // 所以按机器存。普通账号也能填——否则「没配的那个源」在别人的账号里永远配不了。
  let srcTokData = null;

  function srcTokRow(key, info) {
    const card = el('div', 'mach');
    const hd = el('div', 'mach-hd');
    hd.appendChild(el('b', '', info.label || key));
    hd.appendChild(el('span', 'hint mono ' + (info.set ? 'mytok-state-on' : 'mytok-state-off'),
      info.set ? ('已配置 · ' + info.mask) : '未配置'));
    card.appendChild(hd);
    const row = el('div', 'acct-tok');
    const inp = el('input', 'input mono');
    inp.autocomplete = 'off';
    inp.spellcheck = false;
    inp.placeholder = key === 'seedlink'
      ? (info.set ? 'host:port（填新的替换）' : 'host:port，例如 geofon.gfz-potsdam.de:18000')
      : (info.set ? '填新的替换' : '粘贴令牌…');
    const bSave = el('button', 'btn sm', '保存');
    const bClear = el('button', 'btn sm', '清除');
    bClear.disabled = !info.set;
    const push = async (val) => {
      try {
        const r = await fetch('/api/srctokens', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ [key]: val }),
        });
        const d = await r.json().catch(() => ({}));
        if (!r.ok || !d.ok) { toast(d.error || '保存失败'); return; }
        if (d.machine) srcTokData = d.machine;
        toast(val ? ('已保存 ' + (info.label || key) + '，全机生效')
          : ('已清除 ' + (info.label || key) + '，全机生效'));
        renderSrcTokens();
      } catch (e) { toast('保存失败：网络异常'); }
    };
    bSave.addEventListener('click', () => {
      const v = (inp.value || '').trim();
      if (!v) { toast('先填入内容'); return; }
      push(v);
    });
    bClear.addEventListener('click', () => push(''));
    row.appendChild(inp);
    row.appendChild(bSave);
    row.appendChild(bClear);
    card.appendChild(row);
    return card;
  }

  function renderSrcTokens() {
    const box = $('#srcTokList');
    if (!box || !srcTokData) return;
    box.textContent = '';
    const keys = Object.keys(srcTokData);
    for (const k of keys) box.appendChild(srcTokRow(k, srcTokData[k]));
    const lack = keys.filter(k => !(srcTokData[k] || {}).set);
    const st = $('#srcTokState');
    if (st) {
      st.textContent = lack.length ? ('未配置：' + lack.join(' / ')) : '三路都已配置';
      st.className = 'hint mono ' + (lack.length ? 'mytok-state-off' : 'mytok-state-on');
    }
  }

  async function loadSrcTokens() {
    try {
      const d = await (await fetch('/api/srctokens', { cache: 'no-store' })).json();
      if (d && d.machine) { srcTokData = d.machine; renderSrcTokens(); }
    } catch (e) { }
  }

  // ---------------- 账号与密钥管理（仅站点所有者） ----------------
  const adminDlg = $('#adminDlg');
  let adminData = null;

  function agoText(ts) {
    if (!ts) return '从未登录';
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 90) return '刚刚';
    if (s < 3600) return Math.floor(s / 60) + ' 分钟前';
    if (s < 86400) return Math.floor(s / 3600) + ' 小时前';
    if (s < 86400 * 30) return Math.floor(s / 86400) + ' 天前';
    return Math.floor(s / 86400 / 30) + ' 个月前';
  }

  async function adminApi(url, body) {
    const opt = body === undefined
      ? { cache: 'no-store' }
      : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
    const r = await fetch(url, opt);
    let d = {};
    try { d = await r.json(); } catch (e) { }
    if (!r.ok || d.ok === false) throw new Error(d.error || ('HTTP ' + r.status));
    return d;
  }

  function el(tag, cls, txt) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt !== undefined) n.textContent = txt;
    return n;
  }

  function srcTag(u) {
    if (u.source === 'own') return el('span', 'tag own', '专属密钥');
    if (u.source === 'shared') return el('span', 'tag shr', '共享密钥');
    return el('span', 'tag non', '无密钥');
  }

  function devName(ua) {
    const s = String(ua || '');
    let os = '未知设备';
    if (/iPhone/.test(s)) os = 'iPhone';
    else if (/iPad/.test(s)) os = 'iPad';
    else if (/Android/.test(s)) os = 'Android 手机';
    else if (/Windows/.test(s)) os = 'Windows 电脑';
    else if (/Macintosh|Mac OS X/.test(s)) os = 'Mac';
    else if (/Linux/.test(s)) os = 'Linux';
    else if (/curl|python|okhttp|Postman/i.test(s)) os = '程序 / 命令行';
    let br = '';
    if (/Edg\//.test(s)) br = 'Edge';
    else if (/OPR\//.test(s)) br = 'Opera';
    else if (/Chrome\//.test(s)) br = 'Chrome';
    else if (/Firefox\//.test(s)) br = 'Firefox';
    else if (/Safari\//.test(s)) br = 'Safari';
    return br ? (os + ' · ' + br) : os;
  }

  function devList(u) {
    const box = el('div', 'dev-list');
    const list = u.devices || [];
    for (const d of list) {
      const row = el('div', 'dev' + (d.me ? ' me' : ''));
      const nm = el('div', 'dev-nm');
      nm.appendChild(el('b', '', devName(d.ua)));
      nm.appendChild(el('span', 'dev-meta',
        (d.ip || '—') + ' · 最近 ' + agoText(d.last_seen)));
      row.appendChild(nm);
      if (d.me) {
        row.appendChild(el('span', 'tag own', '本机'));
      } else {
        const b = el('button', 'btn sm', '下线');
        b.addEventListener('click', async () => {
          try {
            await adminApi('/api/admin/device', { username: u.name, id: d.id });
            toast('已让这台设备下线');
            await loadAdmin();
          } catch (e) { toast(e.message || '操作失败'); }
        });
        row.appendChild(b);
      }
      box.appendChild(row);
    }
    if (!list.length) box.appendChild(el('div', 'hint', '没有在线设备'));
    return box;
  }


  function acctCard(u) {
    const myName = state.me || '';
    const card = el('div', 'acct' + (u.name === myName ? ' me' : ''));

    const hd = el('div', 'acct-hd');
    hd.appendChild(el('b', '', u.name));
    if (u.owner) hd.appendChild(el('span', 'tag adm', '所有者'));
    hd.appendChild(srcTag(u));
    hd.appendChild(el('span', 'sp'));
    const meta = el('div', 'acct-meta',
      '最后登录 ' + agoText(u.last_login) + ' · ' + (u.sessions || 0) + ' 台设备在线');
    card.appendChild(hd);
    card.appendChild(meta);

    if (u.jian_ok === false && u.jian_err) {
      card.appendChild(el('div', 'acct-meta', '流异常：' + u.jian_err));
    }

    card.appendChild(devList(u));

    // 密钥行
    const tk = el('div', 'acct-tok');
    if (u.source === 'own') tk.appendChild(el('span', 'hint mono', u.token || '(空)'));
    const inp = el('input', 'input mono');
    inp.placeholder = u.source === 'own' ? '填新的 rt_… 替换' : 'rt_…（填了就有专属流）';
    inp.autocomplete = 'off';
    inp.spellcheck = false;
    const bSave = el('button', 'btn sm', '保存');
    const bClear = el('button', 'btn sm', '清除');
    bClear.disabled = u.source !== 'own';
    tk.appendChild(inp);
    tk.appendChild(bSave);
    tk.appendChild(bClear);
    card.appendChild(tk);

    const doTok = async (val) => {
      try {
        await adminApi('/api/admin/tokens', { username: u.name, jian: val });
        toast(val ? ('已为 ' + u.name + ' 设置专属密钥') : ('已清除 ' + u.name + ' 的专属密钥'));
        await loadAdmin();
      } catch (e) { toast(e.message || '操作失败'); }
    };
    bSave.addEventListener('click', () => {
      const v = (inp.value || '').trim();
      if (!v) { toast('先填入 rt_ 开头的密钥'); return; }
      doTok(v);
    });
    bClear.addEventListener('click', () => doTok(''));

    // 操作行
    const ft = el('div', 'acct-ft');
    const isMe = u.name === myName;

    const bPwd = el('button', 'btn sm', '改密码');
    bPwd.addEventListener('click', () => {
      if (card.querySelector('.acct-pwd')) return;
      const row = el('div', 'acct-pwd');
      const p = el('input', 'input mono');
      p.type = 'password';
      p.placeholder = '新密码（≥6 位）';
      p.autocomplete = 'new-password';
      const ok = el('button', 'btn sm primary', '确定');
      const no = el('button', 'btn sm', '取消');
      const run = async () => {
        const v = (p.value || '').trim();
        if (v.length < 6) { toast('密码至少 6 位'); return; }
        try {
          await adminApi('/api/admin/users', { action: 'password', username: u.name, password: v });
          toast('已改密码，该账号所有设备被下线');
          await loadAdmin();
        } catch (e) { toast(e.message || '改密码失败'); }
      };
      ok.addEventListener('click', run);
      p.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') run(); });
      no.addEventListener('click', () => row.remove());
      row.appendChild(p); row.appendChild(ok); row.appendChild(no);
      card.appendChild(row);
      p.focus();
    });
    ft.appendChild(bPwd);

    if (!isMe) {
      ft.appendChild(el('span', 'hint', '普通账号：只能管自己的数据源密钥'));
    }

    if (u.sessions > 0 && !u.owner) {
      const bKick = el('button', 'btn sm', '全部下线');
      bKick.addEventListener('click', async () => {
        try {
          const d = await adminApi('/api/admin/users', { action: 'kick', username: u.name });
          toast('已清掉 ' + (d.kicked || 0) + ' 个会话');
          await loadAdmin();
        } catch (e) { toast(e.message || '操作失败'); }
      });
      ft.appendChild(bKick);
    }

    if (!isMe) {
      const bDel = el('button', 'btn sm danger', '删除账号');
      let armed = 0;
      bDel.addEventListener('click', async () => {
        if (!armed) {
          armed = setTimeout(() => { armed = 0; bDel.classList.remove('arm'); bDel.textContent = '删除账号'; }, 4000);
          bDel.classList.add('arm');
          bDel.textContent = '确认删除？';
          return;
        }
        clearTimeout(armed); armed = 0;
        try {
          await adminApi('/api/admin/users', { action: 'delete', username: u.name });
          toast('已删除账号 ' + u.name);
          await loadAdmin();
        } catch (e) { toast(e.message || '删除失败'); }
      });
      ft.appendChild(bDel);
    } else {
      ft.appendChild(el('span', 'hint', '（当前登录）'));
    }
    card.appendChild(ft);
    return card;
  }

  function renderAdmin() {
    if (!adminData) return;
    $('#adminCount').textContent = adminData.users.length + ' 个';
    const box = $('#adminList');
    box.textContent = '';
    for (const u of adminData.users) box.appendChild(acctCard(u));
    // 机器级令牌不在这里改：设置 → 数据源 里人人可改，那边一份入口就够。
    $('#adminNote').textContent = '账号管理仅 ' + (adminData.owner || '所有者') + ' 可用';
  }

  async function loadAdmin() {
    try {
      adminData = await adminApi('/api/admin/users');
      renderAdmin();
    } catch (e) {
      $('#adminNote').textContent = e.message || '加载失败';
    }
  }

  const btnAdmin = $('#btnAdmin');
  if (btnAdmin) btnAdmin.addEventListener('click', () => {
    $('#adminNote').textContent = '';
    adminDlg.showModal();
    loadAdmin();
  });
  if ($('#adminClose')) $('#adminClose').addEventListener('click', () => adminDlg.close());
  if ($('#adminDone')) $('#adminDone').addEventListener('click', () => adminDlg.close());
  if ($('#adminRefresh')) $('#adminRefresh').addEventListener('click', loadAdmin);
  if ($('#btnAdminCreate')) $('#btnAdminCreate').addEventListener('click', async () => {
    const n = ($('#adminNewName').value || '').trim();
    const p = ($('#adminNewPwd').value || '');
    if (!n) { toast('先填用户名'); return; }
    try {
      // 新号一律普通账号，服务端也不收 admin 字段了
      await adminApi('/api/admin/users', { action: 'create', username: n, password: p });
      $('#adminNewName').value = '';
      $('#adminNewPwd').value = '';
      toast('已创建账号 ' + n);
      await loadAdmin();
    } catch (e) { toast(e.message || '创建失败'); }
  });
  $('#btnSettings').addEventListener('click', () => {
    paintFocus(); dlg.showModal(); loadMyTokens(); loadSrcTokens();
  });

  $('#btnImport').addEventListener('click', async () => {
    const raw = $('#stCsv').value.trim();
    if (!raw) { toast('请先粘贴 CSV'); return; }
    const rows = raw.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
      .map(l => l.split(/[,\t]/).map(x => x.trim()))
      .filter(r => r.length >= 4 && !/^code$/i.test(r[0]));
    const items = rows.map(r => ({
      code: r[0], name: r[1], country: (r[2] || 'CN').toUpperCase(),
      lat: r[3], lon: r[4], net: r[5] || '自定义',
    })).slice(0, 2000);
    if (!items.length) { toast('没有解析到有效行'); return; }
    const res = await fetch('/api/stations/custom', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stations: items,
        user: localStorage.getItem('eew.user') || '' }),
    });
    const d = await res.json();
    toast('已导入 ' + d.count + ' 个台站');
    await loadStations();
    $('#stCsv').value = '';
  });
  $('#btnClearCustom').addEventListener('click', async () => {
    await fetch('/api/stations/custom', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ stations: [],
        user: localStorage.getItem('eew.user') || '' }),
    });
    toast('已清空自定义台站');
    await loadStations();
  });

  $('#optSimPick').addEventListener('change', e => {
    state.simPick.on = e.target.checked;
    const pos = $('#simPickPos');
    if (e.target.checked) {
      if (state.simPick.lat != null) markSimPick();
      else pos.textContent = '当前：请点击地图选择震中';
      toast('点击地图选择演练震中');
      dlg.close();
    } else {
      pos.textContent = '当前：使用预设震源';
      state.simPick.place = '';
      baseDirty = true;
    }
  });

  $('#btnSim').addEventListener('click', async () => {
    const cc = $('#simCountry').value;
    const mag = parseFloat($('#simMag').value) || 6.5;
    const depth = parseFloat($('#simDepth').value) || 15;
    const body = { country: cc, mag, depth, elapsed: 6 };
    if (state.simPick.on && state.simPick.lat != null) {
      body.lat = state.simPick.lat;
      body.lon = state.simPick.lon;
      // 地名跟着坐标走：反查过一次就带上，没查到时让后端按坐标现算
      if (state.simPick.place) body.place = state.simPick.place;
    }
    // 先清掉「手动忽略」的标记，免得新演练沿用上一次的忽略键
    state.dismissedKey = null; state.simPickFlash = 0;
    await fetch('/api/sim', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    toast('演练开始：M' + mag + ' 深' + depth + 'km');
    dlg.close();
  });
  $('#btnSimClear').addEventListener('click', async () => {
    await fetch('/api/sim/clear', { method: 'POST', body: '{}' });
    state.sim = null;
    toast('演练已结束');
  });

  // ---------------- 控件 ----------------
  let toastTimer = 0;
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2400);
  }

  // 空格 = 演练倍速开关（1× ⇄ 3×）。原来的空格是「回放」，但演练里按空格
  // 十有八九是想快点看完，回放还留在事件卡的「回放」按钮上。
  // 时间轴在服务端按虚拟秒推进，所以中途变速不会把已经排好的报文打乱。
  function toggleSimSpeed() {
    const next = (state.simSpeed >= 2) ? 1 : 3;
    fetch('/api/sim/speed', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ speed: next }),
    }).then(r => r.json()).then(d => {
      state.simSpeed = d.speed || next;
      if (state.sim) toast('演练 ' + state.simSpeed + '× 速');
      else toast('当前没有演练 · 下次演练 ' + state.simSpeed + '×');
    }).catch(() => toast('变速失败 · 服务未响应'));
  }

  $('#btnHistory').addEventListener('click', openSheet);
  $('#recentMore').addEventListener('click', openSheet);
  $('#sheetClose').addEventListener('click', closeSheet);
  $('#sheetBg').addEventListener('click', closeSheet);
  $('#recentList').addEventListener('click', e => {
    const row = e.target.closest('.rec'); if (!row) return;
    const item = (state.events || []).find(x => x.uid === row.dataset.uid);
    if (!item) return;
    state.historyEvent = item;
    state.histReplayAt = 0;
    lastFocusUid = '';
    focusOnEvent(item, true);
    renderAll();
  });

  $('#histDays').addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    state.history.days = +b.dataset.days;
    $$('#histDays button').forEach(x => x.classList.toggle('on', x === b));
    loadHistory();
  });
  $('#histMag').addEventListener('change', e => {
    state.history.minmag = parseFloat(e.target.value);
    loadHistory();
  });
  $('#histList').addEventListener('click', e => {
    const row = e.target.closest('.hrow'); if (!row) return;
    selectHistory(row.dataset.uid);
  });

  $('#phasesRow').addEventListener('click', () => {
    const box = $('#calcBox');
    box.hidden = !box.hidden;
  });

  // 数据源表：GQ 服务器手动切换（点「主」「备」芯片）+「自动」开关
  $('#srcTable').addEventListener('click', async e => {
    const sw = e.target.closest('[data-gqswap]');
    const au = e.target.closest('[data-gqauto]');
    if (!sw && !au) return;
    e.preventDefault();
    e.stopPropagation();
    const g = state.sources.find(x => x.id === 'globalquake');
    try {
      if (sw) {
        const idx = +sw.getAttribute('data-gqswap');
        if (g && idx === (g.host_idx || 0)) { toast('已经在用这台了'); return; }
        const r = await fetch('/api/gq/switch', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ idx: idx })
        }).then(x => x.json());
        if (!r.ok) { toast('切换失败：' + (r.error || '未知错误')); return; }
        toast('GQ 已切到' + (r.role || '另一台') + '，正在重连…');
      } else {
        const next = !(g && g.auto);
        const r = await fetch('/api/gq/auto', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ auto: next })
        }).then(x => x.json());
        if (!r.ok) { toast('设置失败：' + (r.error || '未知错误')); return; }
        toast('GQ 自主切换已' + (r.auto ? '打开（择优 + 故障切换 + 自动回主）' : '关闭（只做故障切换）'));
      }
    } catch (err) {
      toast('请求失败：' + err.message);
    }
  });

  // 多事件适配：点胶囊切到那起地震；「回到最新」清掉手动选择
  $('#heroAlts').addEventListener('click', e => {
    const b = e.target.closest('.altchip');
    if (!b) return;
    const uid = b.dataset.uid || '';
    state.focusUid = uid || null;
    state.dismissedKey = null;
    state.hiddenUntil = 0;
    const ev = currentEvent();
    if (ev) focusOnEvent(ev, true);
    renderAll();
  });

  $('#heroClose').addEventListener('click', () => {
    if (state.historyEvent) {
      state.historyEvent = null;
      state.histReplayAt = 0;
      renderAll();
      return;
    }
    const ev = currentEvent();
    if (ev) {
      state.dismissedKey = evKey(ev);
      state.hiddenUntil = nowSec() + 90;
    }
    renderHero();
  });

  $('#heroReplay').addEventListener('click', () => {
    if (state.historyEvent) {
      state.histReplayAt = nowSec();
      toast('回放：波前从震中扩散');
      setTimeout(() => { state.histReplayAt = 0; renderHero(); }, 300000);
      return;
    }
    if (!currentEvent()) { toast('当前没有进行中的预警'); return; }
    state.replayAt = nowSec();
    toast('重放波前');
    setTimeout(() => { state.replayAt = 0; }, 120000);
  });

  $('#heroCopy').addEventListener('click', async () => {
    const ev = displayEvent();
    if (!ev) { toast('当前没有可复制的事件'); return; }
    const txt = eventText(ev);
    const ok = await copyText(txt);
    toast(ok ? '事件文字已复制' : '复制失败，请手动选中');
  });

  $$('.tools button').forEach(btn => {
    btn.addEventListener('click', () => {
      const act = btn.dataset.act;
      if (act === 'zin') zoomBy(1.5);
      if (act === 'zout') zoomBy(1 / 1.5);
      if (act === 'home') { fitView(); atHome = true; if (homeTimer) { clearTimeout(homeTimer); homeTimer = 0; } }
      if (act === 'globe') fitWorld();
      if (act === 'full') toggleFull();
      if (act === 'layers') {
        const pop = $('#layerPop');
        pop.hidden = !pop.hidden;
        btn.classList.toggle('on', !pop.hidden);
      }
    });
  });

  $('#layerPop').addEventListener('change', e => {
    const cb = e.target.closest('input[data-layer]');
    if (!cb) return;
    state.layers[cb.dataset.layer] = cb.checked ? 1 : 0;
    LS.set('layers3', state.layers);
    baseDirty = true; fieldDirty = true;
  });
  if (state.layers.pref === undefined) state.layers.pref = 1;
  if (state.layers.palert === undefined) state.layers.palert = 1;
  $$('#layerPop input[data-layer]').forEach(cb => { cb.checked = !!state.layers[cb.dataset.layer]; });

  function syncStColorSeg() {
    $$('#stColorSeg button').forEach(b => b.classList.toggle('on', b.dataset.sc === (state.stColor || 'auto')));
  }
  syncStColorSeg();
  $('#stColorSeg').addEventListener('click', e => {
    const b = e.target.closest('button[data-sc]');
    if (!b) return;
    state.stColor = b.dataset.sc;
    LS.set('stColor', state.stColor);
    syncStColorSeg();
    baseDirty = true;
    updateLegendCap();
  });

  function toggleFull() {
    document.body.classList.toggle('focus-map');
    const on = document.body.classList.contains('focus-map');
    $('#btnFull').classList.toggle('on', on);
    setTimeout(() => {
      resize();
      const ev = currentEvent();
      if (ev) { lastFocusUid = ''; focusOnEvent(ev, true); }
    }, 120);
  }

  document.addEventListener('click', e => {
    const pop = $('#layerPop');
    if (pop.hidden) return;
    if (e.target.closest('#layerPop') || e.target.closest('#btnLayers')) return;
    pop.hidden = true;
    $('#btnLayers').classList.remove('on');
  });

  window.addEventListener('keydown', e => {
    const t = e.target;
    if (t && t.matches && t.matches('input,textarea,select')) return;
    const k = e.key.toLowerCase();
    if (k === 'm') $('#btnSound').click();
    if (k === 't') applyTheme(themeNow() === 'light' ? 'dark' : 'light');
    if (k === 's') $('#btnSettings').click();
    if (k === 'f') toggleFull();
    if (k === 'r') fitView();
    if (k === 'g') fitWorld();
    if (k === 'h') openSheet();
    if (k === 'escape') closeSheet();
    if (k === ' ') { e.preventDefault(); toggleSimSpeed(); }
  });

  window.addEventListener('resize', () => resize());

  // ---------------- 启动 ----------------
  applyTheme(themeNow(), false);   // 首帧那一下由 head 里的同步脚本顶着，这里补齐按钮/底图配色
  renderLegend();
  paintFocus();
  paintWatches();
  setTimeout(() => {
    const h = $('#mapHint');
    if (h) h.style.opacity = '0';
  }, 12000);
  syncSound();
  resize();
  loadVoicePack();
  fetch('/api/health', { cache: 'no-store' })
    .then(r => r.json())
    .then(d => { state.serverAudio = !!d.server_audio; })
    .catch(() => { });
  loadCoast();
  loadPalertStations();
  loadMe();
  loadStations().then(() => { connect(); kmoniLoop(); });

  // GlobalQuake 全球测站表：连接后服务端才拿得到，空了就隔段时间再试。
  // 注意：服务端分块累积（71 包），首查可能只有几百站——没到齐就继续轮询，
  // 逐次覆盖刷新（灰点随到随画），到齐（s.length >= n）才停。
  let gqStTries = 0;
  (function loadGQStations() {
    fetch('/api/gq_stations', { cache: 'no-store' }).then(r => r.json()).then(d => {
      if (d && d.s && d.s.length) {
        state.gqSt = d.s;
        state.gqIdx = new Map();
        for (const s of d.s) state.gqIdx.set(s[0], s);
        baseDirty = true;
        if (d.s.length < d.n && ++gqStTries < 40) { setTimeout(loadGQStations, 4000); return; }
      } else if (++gqStTries < 40) {
        setTimeout(loadGQStations, 8000);
      }
    }).catch(() => { if (++gqStTries < 40) setTimeout(loadGQStations, 15 * 1000); });
  })();
  // 图例（含 kmoni 帧时间戳与「Ns前」）以前只在有震动时刷新，看着像「不更新」。
  // 这里挂到主循环上每秒刷一次，帧时间戳会肉眼可见地逐秒跳。
  $('#obsClose').addEventListener('click', () => {
    obsDismissedTs = (state.obs && state.obs.origin_ts) || 0;
    $('#obsCard').hidden = true;
    document.body.classList.remove('obs-on');
  });
  let lastCapAt = 0;
  (function loop() {
    draw();
    const t = Date.now();
    if (t - lastCapAt >= 1000) {
      lastCapAt = t;
      try { updateLegendCap(); } catch (e) { }
      try { renderObsCard(); } catch (e) { window.__lastErr = 'obs: ' + (e && e.message); }
    }
    requestAnimationFrame(loop);
  })();
  window.__eew = state;   // 调试用：可以在控制台里看/改状态
  // 任何未捕获异常都留个记号：主循环一旦被 draw() 里的异常打断，
  // 之后卡片就再也不刷新，而控制台在打包版里看不到——只能靠自己记。
  window.__lastErr = '';
  window.addEventListener('error', e => {
    window.__lastErr = (e.message || '') + ' @' + (e.filename || '').split('/').pop() + ':' + (e.lineno || 0);
  });
  window.__dispEv = displayEvent;   // 调试钩子：当前真正在显示的卡片（null = 已收起）
  window.__auto = autoHypo;         // 调试钩子：手动跑一次「本地推算震源」
  window.__screenOf = (lat, lon) => [px(lon), py(lat)];   // 经纬度 → 画布像素（截图定位用）
  window.__view = view;             // 调试钩子：视野（缩放倍率 = view.s / view.fit）
  window.__followView = followView;  // 调试钩子：跑一次「跟随/归位」判定
  window.__flyHome = flyHome;        // 调试钩子：立刻飞回中日全景
  window.__shakeFrame = autoFrameShake;   // 调试钩子：直接跑一次绿框自动取景
  window.__tsuAlert = renderTsuAlert;     // 调试钩子：重绘海啸警报横幅
  window.__obsLpgm = renderObsLpgm;       // 调试钩子：重绘卡片长周期行
  window.__obsCard = renderObsCard;       // 调试钩子：重绘震度速報卡
  window.__liveMax = updateLiveMax;       // 调试钩子：重算右上角实时震度
  window.__renderAll = renderAll;         // 调试钩子：整页重绘
  window.__goto = (lat, lon, sMul) => {   // 调试钩子：定位到 (lat,lon)，sMul 为相对东亚基准的倍数
    cancelViewTween();
    framedUid = '';
    view.s = homeScale() * (sMul || 1);
    centerOn(lat, lon);
    baseDirty = fieldDirty = true;
  };
  window.__viewProbe = () => view.s;      // 调试钩子：读当前缩放
  // 调试钩子：走时 / 反演同一套公式，方便对数（__tt(300,10) → P/S 走时）
  window.__tt = (delta, depth, model) => travelInfo(delta, depth, model);
  window.__ttModel = ttModelFor;
  window.__autoDiag = () => AUTO.diag || null;
  // 调试：注入一场「假震度场」直接跑推算，用来验证触发门槛（不写进任何真实数据）
  window.__autoTest = lv => {
    state.kmoni = { ok: true, at: nowSec(), ts: 'test', lv: lv || {}, n: Object.keys(lv || {}).length };
    AUTO.lastAt = 0; AUTO.diag = null;      // 测试时绕过冷却，保证每次都重新判
    const ev = autoHypo();
    return { ev: ev ? { lat: ev.lat, lon: ev.lon, mag: ev.mag, depth: ev.depth,
      trigger: ev.trigger, stations: ev.stations } : null, diag: AUTO.diag };
  };
  window.__dist = (t, depth, model) => ({ p: distP(t, depth, model), s: distS(t, depth, model) });
  window.__landHit = (lon, lat) => landHit(lon, lat);          // 调试钩子：该点是不是陆地
  window.__tsu = () => (state.tsu ? {                       // 调试钩子：当前海啸参数
    uid: state.tsu.uid, mag: state.tsu.mag, depth: state.tsu.depth,
    h0: +state.tsu.h0.toFixed(2),
    regions: state.tsu.regions.length,
    top: state.tsu.regions.slice(0, 8).map(r => r.pref + '/' + r.name + ':' + r.h.toFixed(1) + 'm/' + Math.round(r.eta) + 's'),
  } : null);
  window.__apply = applyState;            // 调试钩子：手动灌一份 state（测多事件等）
  window.__st = { play: playStSound, tone: stTone, files: stFilesForRisk };   // 测站音调试
  setInterval(() => {
    renderClocks();
    tickReactions();
    tickAuto();
    updateLiveMax();
    if (displayEvent()) renderHero();
  }, 250);
})();
