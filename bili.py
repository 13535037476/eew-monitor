# -*- coding: utf-8 -*-
"""B站自动投稿：把地震录像传上去（预上传 → 分片上传 → 完成 → 提交稿件）。

用的是网页投稿那套接口（member.bilibili.com/preupload + /x/vu/web/add/v3），
和 biliup 等开源工具同一条路：登录靠浏览器里的三个 cookie——
  SESSDATA / bili_jct / DedeUserID
cookie 有过期时间（一般几个月），失效后 /api/bili/test 会返回「未登录」。

配置与上传记录都存在 data/ 下，不进版本库（含登录态）。
"""
import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid

CFG_PATH = None          # 延迟到 init() 里定，避免 import 顺序问题
_LOCK = threading.Lock()
_LOG = {}                # 文件名 -> {state, bvid, err, ts, title}
_Q = None
_STATE = {"ok": 0, "fail": 0, "last": "", "err": "", "busy": False, "running": False}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _data_dir():
    # 延迟导入：server 也会 import bili，顶层互相 import 会绕进循环
    import server
    return getattr(server, "DATA_DIR",
                   os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))


def cfg_path():
    return os.path.join(_data_dir(), "bili.json")


def log_path():
    return os.path.join(_data_dir(), "bili_uploads.json")


def cfg():
    try:
        with open(cfg_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save_cfg(d):
    p = cfg_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


def log():
    global _LOG
    if not _LOG:
        try:
            with open(log_path(), encoding="utf-8") as f:
                _LOG = json.load(f)
        except (OSError, ValueError):
            _LOG = {}
        if not isinstance(_LOG, dict):
            _LOG = {}
    return _LOG


def _log_save():
    try:
        p = log_path()
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(log(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except OSError:
        pass


def _cookies(c=None):
    c = c or cfg()
    return "SESSDATA=%s; bili_jct=%s; DedeUserID=%s" % (
        c.get("sessdata", ""), c.get("bili_jct", ""), c.get("dedeuserid", ""))


def _req_full(url, data=None, method=None, headers=None, timeout=60, c=None):
    """和 _req 一样，但连响应头一起返回（扫码登录要从 Set-Cookie 收票）。"""
    # 登录（passport）和投稿（member）要各自正确的 Referer/Origin，
    # 全部塞 member 的头会被 passport 当成跨站请求挡掉。
    if "passport.bilibili.com" in url:
        ref, org = "https://www.bilibili.com/", "https://www.bilibili.com"
    elif "api.bilibili.com" in url:
        ref, org = "https://www.bilibili.com/", "https://www.bilibili.com"
    else:
        ref, org = "https://member.bilibili.com/", "https://member.bilibili.com"
    h = {"User-Agent": UA, "Referer": ref, "Origin": org, "Accept": "application/json, text/plain, */*"}
    cc = c or cfg()
    if cc.get("sessdata"):
        h["Cookie"] = _cookies(cc)
    if headers:
        h.update(headers)
    body = data
    if isinstance(data, dict) and not (headers or {}).get("Content-Type"):
        body = json.dumps(data).encode("utf-8")
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.getcode(), r.read(), r.headers


def _req(url, data=None, method=None, headers=None, timeout=60, c=None):
    code, raw, _h = _req_full(url, data=data, method=method, headers=headers,
                              timeout=timeout, c=c)
    return code, raw


def _cookies_from_headers(headers):
    """从 Set-Cookie 里挑出登录要用的三样（B站新版只在这里给）。"""
    out = {}
    try:
        items = headers.get_all("Set-Cookie") or []
    except Exception:
        items = []
    for line in items:
        first = line.split(";", 1)[0]
        if "=" not in first:
            continue
        k, v = first.split("=", 1)
        k = k.strip()
        if k in ("SESSDATA", "bili_jct", "DedeUserID"):
            out[k] = v.strip()
    return out


_NO_REDIRECT = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _follow_cookies(url, max_hops=5):
    """逐跳跟随 302，收集每一跳 Set-Cookie 里的登录票。

    新版 B站 扫码成功后不再把 cookie 直接拼在 data.url 里，
    而是给一个 crossDomain/ticket 地址，票在跳转响应的 Set-Cookie 上。
    必须禁用自动重定向、自己跟，才能看到中间每一跳的响应头。
    """
    global _NO_REDIRECT
    if _NO_REDIRECT is None:
        _NO_REDIRECT = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect())
    got = {}
    cur = url
    for _ in range(max_hops):
        if not cur.startswith("http"):
            break
        req = urllib.request.Request(cur, headers={
            "User-Agent": UA, "Referer": "https://www.bilibili.com/",
            "Accept": "text/html,application/xhtml+xml,*/*",
        })
        try:
            r = _NO_REDIRECT.open(req, timeout=20)
        except urllib.error.HTTPError as exc:
            r = exc
            hdrs = exc.headers
        else:
            hdrs = r.headers
        got.update({k: v for k, v in _cookies_from_headers(hdrs).items() if v})
        if "SESSDATA" in got and got["SESSDATA"]:
            break
        nxt = ""
        try:
            nxt = hdrs.get("Location") or ""
        except Exception:
            nxt = ""
        if not nxt:
            break
        cur = urllib.parse.urljoin(cur, nxt)
    return got


def check_login(c=None):
    """拉一次自己的账号信息：既验证 cookie，也顺手拿到昵称。"""
    c = c or cfg()
    if not c.get("sessdata"):
        return {"ok": False, "error": "还没填 SESSDATA"}
    try:
        code, raw = _req("https://api.bilibili.com/x/web-interface/nav", c=c, timeout=20)
        d = json.loads(raw.decode("utf-8", "replace"))
        if d.get("code") == 0 and (d.get("data") or {}).get("isLogin"):
            u = d["data"]
            return {"ok": True, "uname": u.get("uname"), "mid": u.get("mid"),
                    "vip": (u.get("vipStatus") == 1)}
        return {"ok": False, "error": "未登录（cookie 失效？）：%s" % (d.get("message") or d.get("code"))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def _preupload(name, size, c):
    q = urllib.parse.urlencode({
        "name": name, "size": size, "r": "upos", "profile": "ugcfx/bup",
        "ssl": "0", "version": "2.14.0", "build": "2140000",
    })
    code, raw = _req("https://member.bilibili.com/preupload?" + q, c=c, timeout=30)
    d = json.loads(raw.decode("utf-8", "replace"))
    if not d.get("OK") and d.get("code") != 0:
        raise RuntimeError("预上传失败：%s" % (d.get("message") or d))
    up = d.get("upos_uri") or ""
    return {
        "endpoint": (d.get("endpoint") or "").replace("//", "https://", 1),
        "upos_uri": up,
        "auth": d.get("auth") or "",
        "biz_id": d.get("biz_id"),
        "chunk_size": int(d.get("chunk_size") or 8 * 1024 * 1024),
        # 提交稿件时的 filename 是 upos:// 去掉协议那一段
        "filename": up.replace("upos://", "", 1),
    }


def _upload_file(path, name, c):
    size = os.path.getsize(path)
    up = _preupload(name, size, c)
    chunk = up["chunk_size"]
    n = max(1, (size + chunk - 1) // chunk)
    parts = []
    url_base = up["endpoint"] + "/" + up["filename"]
    with open(path, "rb") as f:
        for i in range(n):
            start = i * chunk
            buf = f.read(chunk)
            q = urllib.parse.urlencode({
                "partNumber": i + 1, "uploadId": up["biz_id"], "chunk": i,
                "chunks": n, "size": len(buf), "start": start,
                "end": start + len(buf) - 1, "total": size,
            })
            code, raw = _req(url_base + "?" + q, data=buf, method="PUT",
                             headers={"X-Upos-Auth": up["auth"],
                                      "Content-Type": "application/octet-stream"},
                             c=c, timeout=180)
            try:
                etag = json.loads(raw.decode("utf-8", "replace")).get("etag") or ""
            except Exception:
                etag = raw.decode("utf-8", "replace").strip()[:64]
            parts.append({"partNumber": i + 1, "eTag": etag})
    q = urllib.parse.urlencode({
        "uploadId": up["biz_id"], "biz_id": up["biz_id"], "output": "json",
        "profile": "ugcfx/bup", "name": name, "chunks": n,
    })
    code, raw = _req(url_base + "?" + q,
                     data=json.dumps({"parts": parts}).encode(),
                     method="POST",
                     headers={"X-Upos-Auth": up["auth"],
                              "Content-Type": "application/json"},
                     c=c, timeout=120)
    d = json.loads(raw.decode("utf-8", "replace") or "{}")
    if str(d.get("OK", "")) not in ("1", "True", "true") and d.get("code") not in (0, None):
        raise RuntimeError("合并分片失败：%s" % (d.get("message") or d))
    return up["filename"]


def upload_cover(path, c):
    """封面走 /x/vu/web/cover/up，返回 B站给的图片 URL（失败返回空串）。"""
    import base64
    try:
        with open(path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        body = urllib.parse.urlencode({"cover": "data:image/jpeg;base64," + b64}).encode()
        url = "https://member.bilibili.com/x/vu/web/cover/up?ts=%d&csrf=%s" % (
            int(time.time() * 1000), c.get("bili_jct", ""))
        code, raw = _req(url, data=body, timeout=60, c=c,
                         headers={"Content-Type": "application/x-www-form-urlencoded"})
        d = json.loads(raw.decode("utf-8", "replace"))
        if d.get("code") == 0:
            return (d.get("data") or {}).get("url") or ""
        _STATE["err"] = "封面上传失败：%s" % (d.get("message") or d.get("code"))
    except Exception as e:
        _STATE["err"] = ("封面上传异常：%s" % str(e)[:120])
    return ""


def _add_submission(filename, meta, c):
    tid = int(c.get("tid") or 188)
    title = (c.get("title_tpl") or "【地震预警】{place} M{mag} 最大震度{inten}").format(
        place=meta.get("place", ""), mag=meta.get("mag", 0),
        inten=meta.get("inten", ""), date=meta.get("date", ""),
        serial=meta.get("serial", ""))
    desc = (c.get("desc_tpl") or
            "震线 EEW 自动录制的地震预警画面。\n\n地点：{place}\n震级：M{mag}\n最大震度：{inten}\n"
            "数据源：{src}\n录制时间：{date}\n\n本视频由震线 EEW 自动生成。").format(
        place=meta.get("place", ""), mag=meta.get("mag", 0),
        inten=meta.get("inten", ""), src=meta.get("src", ""),
        date=meta.get("date", ""), serial=meta.get("serial", ""))
    payload = {
        "copyright": 1,
        "source": "",
        "tid": tid,
        "title": title[:80],
        "desc": desc[:1900],
        "tag": (c.get("tags") or "地震,地震预警,EEW").replace("，", ","),
        "cover": meta.get("cover_url") or c.get("cover") or "",
        "videos": [{"filename": filename, "title": title[:80], "desc": ""}],
        "no_reprint": 1,
        "open_elec": 1,
    }
    csrf = c.get("bili_jct", "")
    url = "https://member.bilibili.com/x/vu/web/add/v3?ts=%d&csrf=%s" % (int(time.time() * 1000), csrf)
    code, raw = _req(url, data=payload, timeout=60, c=c)
    d = json.loads(raw.decode("utf-8", "replace"))
    if d.get("code") != 0:
        raise RuntimeError("投稿被拒：%s（code=%s）" % (d.get("message"), d.get("code")))
    return d.get("data") or {}


def _record(name, ok, bvid="", err="", title=""):
    L = log()
    L[name] = {"state": "ok" if ok else "fail", "bvid": bvid, "err": err[:160],
               "title": title, "ts": int(time.time())}
    # 只留最近 300 条
    if len(L) > 300:
        for k in sorted(L, key=lambda k: L[k].get("ts") or 0)[:-300]:
            del L[k]
    if ok:
        _STATE["ok"] += 1
    else:
        _STATE["fail"] += 1
        _STATE["err"] = err[:160]
    _STATE["last"] = name
    _log_save()


def upload_one(path, meta, c=None):
    c = c or cfg()
    name = os.path.basename(path)
    try:
        # 先出封面（A 版「速报卡」），传上去拿到 URL；失败就退回平台自动取帧
        try:
            import cover as cover_mod
            cp = os.path.join(_data_dir(), "covers", os.path.splitext(name)[0] + ".jpg")
            m2 = dict(meta)
            # 中国的事件用「烈度」，日本/其他用「震度」；按数据源名判断
            m2["jp"] = not any(k in str(meta.get("src") or "") for k in ("中国", "CENC", "台网"))
            cover_mod.make_cover(m2, cp)
            url = upload_cover(cp, c)
            if url:
                meta = dict(meta)
                meta["cover_url"] = url
        except Exception as e:
            _STATE["err"] = ("封面生成失败：%s" % str(e)[:120])
        fn = _upload_file(path, name, c)
        data = _add_submission(fn, meta, c)
        bvid = (data or {}).get("bvid") or ""
        _record(name, True, bvid=bvid, title=meta.get("title", ""))
        return {"ok": True, "bvid": bvid}
    except Exception as e:
        _record(name, False, err=str(e))
        return {"ok": False, "error": str(e)[:200]}


def _worker():
    while True:
        job = _Q.get()
        if not job:
            continue
        path, meta = job
        _STATE["busy"] = True
        try:
            upload_one(path, meta)
        finally:
            _STATE["busy"] = False


def init():
    global _Q
    if _Q is None:
        import queue
        _Q = queue.Queue()
        threading.Thread(target=_worker, daemon=True).start()


def enqueue(path, meta):
    """后台排队投稿；没开启或文件不存在就跳过。"""
    c = cfg()
    if not c.get("enabled"):
        return False
    if not os.path.isfile(path):
        return False
    init()
    _Q.put((path, meta or {}))
    return True


def status():
    L = log()
    items = sorted(L.items(), key=lambda kv: kv[1].get("ts") or 0, reverse=True)[:8]
    c0 = cfg()
    return {
        "ok": True,
        "uname": c0.get("uname") or "",
        "mid": c0.get("mid") or 0,
        "enabled": bool(cfg().get("enabled")),
        "has_login": bool(cfg().get("sessdata")),
        "tid": cfg().get("tid") or 188,
        "tags": cfg().get("tags") or "",
        "recent": [{"name": k, **v} for k, v in items],
        "ok_count": sum(1 for v in L.values() if v.get("state") == "ok"),
        "fail_count": sum(1 for v in L.values() if v.get("state") == "fail"),
        "queued": _Q.qsize() if _Q is not None else 0,
        "busy": _STATE["busy"],
        "last_err": _STATE["err"],
    }


# ---------------- 扫码登录 ----------------
# 和网页版同一条路：生成二维码 → 手机 B站扫码 → 轮询拿到跨域 url，
# 里面带着 SESSDATA / bili_jct / DedeUserID，存下来就是「用户的账号」。
# 同时保留多个未过期的二维码：点两次「刷新」不会再互相把对方顶成过期
_QR = {"keys": {}, "state": "idle", "uname": "", "mid": 0, "err": ""}
_DONE = {}          # key -> {at, uname, mid, sessdata, bili_jct, dedeuserid}
_DONE_TTL = 300
_QR_TTL = 240


def _qr_svg(url):
    import io
    import qrcode
    from qrcode.image.svg import SvgPathImage
    img = qrcode.make(url, image_factory=SvgPathImage, box_size=8, border=2)
    b = io.BytesIO()
    img.save(b)
    return b.getvalue().decode("utf-8", "replace")


def qr_new():
    try:
        code, raw = _req(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
            "?source=main-fe-header",
            headers={"Referer": "https://www.bilibili.com/"}, c={}, timeout=20)
        d = json.loads(raw.decode("utf-8", "replace"))
        if d.get("code") != 0:
            return {"ok": False, "error": d.get("message") or ("code=%s" % d.get("code"))}
        data = d.get("data") or {}
        url, key = data.get("url") or "", data.get("qrcode_key") or ""
        if not url or not key:
            return {"ok": False, "error": "二维码生成失败"}
        now = time.time()
        for k in [k for k, t in _QR["keys"].items() if now - t > _QR_TTL]:
            _QR["keys"].pop(k, None)          # 顺手清掉过期的
        _QR["keys"][key] = now
        _QR.update({"state": "waiting", "uname": "", "mid": 0, "err": ""})
        return {"ok": True, "key": key, "svg": _qr_svg(url)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:160]}


def qr_poll(key):
    # 已经成功过的码：直接回放成功结果。B站 事后可能对同一个 key 回 86038，
    # 前端在成功那一刻还在轮询，晚到的一包会把「登录成功」改写成「二维码过期」。
    done = _DONE.get(key) if key else None
    if done and time.time() - done["at"] < _DONE_TTL:
        return {"ok": True, "state": "ok", "uname": done.get("uname"),
                "mid": done.get("mid"), "cached": True}
    born = _QR["keys"].get(key) if key else None
    if not born:
        return {"ok": False, "state": "expired", "error": "二维码已失效，请重新生成"}
    if time.time() - born > _QR_TTL:
        _QR["keys"].pop(key, None)
        return {"ok": False, "state": "expired", "error": "二维码已过期，请重新生成"}
    try:
        code, raw, hdrs = _req_full(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
            "?qrcode_key=%s&source=main-fe-header" % urllib.parse.quote(key),
            headers={"Referer": "https://www.bilibili.com/"}, c={}, timeout=20)
        d = json.loads(raw.decode("utf-8", "replace"))
        data = d.get("data") or {}
        st = data.get("code")
        if st == 86101:
            return {"ok": True, "state": "waiting"}
        if st == 86090:
            return {"ok": True, "state": "scanned"}
        if st == 86038:
            _QR["keys"].pop(key, None)
            _QR["state"] = "expired"
            return {"ok": False, "state": "expired", "error": "二维码已过期，请重新生成"}
        if st == 0:
            url = data.get("url") or ""
            q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
            sess = (q.get("SESSDATA") or [""])[0]
            jct = (q.get("bili_jct") or [""])[0]
            uid = (q.get("DedeUserID") or [""])[0]
            # 新版 B站 可能不在 url 查询串里给票：轮询响应自己的 Set-Cookie 先收一遍
            if not sess:
                ck = _cookies_from_headers(hdrs)
                sess = ck.get("SESSDATA", "") or sess
                jct = ck.get("bili_jct", "") or jct
                uid = ck.get("DedeUserID", "") or uid
            # 还不够就手动跟 302（crossDomain/ticket 那串），每跳的 Set-Cookie 都收
            if not sess and url.startswith("http"):
                ck2 = _follow_cookies(url)
                sess = ck2.get("SESSDATA", "") or sess
                jct = ck2.get("bili_jct", "") or jct
                uid = ck2.get("DedeUserID", "") or uid
            if not sess:
                _STATE["err"] = "登录回调里没有 SESSDATA"
                return {"ok": False, "state": "fail",
                        "error": "登录成功但缺 SESSDATA（B站 回调既没带票也没给 Set-Cookie，"
                                 "请重试；仍失败把这句发我）",
                        "url": url[:180]}
            if not jct or not uid:
                # 能登录但投稿会 403：先把缺哪一项说清楚，别让人猜
                miss = "bili_jct" if not jct else "DedeUserID"
                _STATE["err"] = "登录回调缺 " + miss
                return {"ok": False, "state": "fail",
                        "error": "登录成功但缺 %s（换上后能登录，但投稿会被拒；请重扫码）" % miss}
            c = cfg()
            c.update({"sessdata": sess, "bili_jct": jct, "dedeuserid": uid})
            if "enabled" not in c:
                c["enabled"] = True      # 扫码登录通常就是为了开投稿
            r = check_login(c)
            if r.get("ok"):
                c["uname"] = r.get("uname") or ""
                c["mid"] = r.get("mid") or 0
            try:
                save_cfg(c)
            except OSError as exc:
                return {"ok": False, "state": "fail", "error": "保存失败：%s" % exc}
            st2 = "ok" if r.get("ok") else "fail"
            _QR.update({"state": st2, "uname": r.get("uname") or "",
                        "mid": r.get("mid") or 0, "err": r.get("error") or ""})
            return {"ok": r.get("ok", False), "state": st2,
                    "uname": r.get("uname"), "mid": r.get("mid"),
                    "error": r.get("error")}
        return {"ok": True, "state": "waiting",
                "code": d.get("code"), "message": d.get("message") or ""}
    except Exception as e:
        return {"ok": False, "state": "fail", "error": str(e)[:160]}


def logout():
    c = cfg()
    for k in ("sessdata", "bili_jct", "dedeuserid"):
        c[k] = ""
    try:
        save_cfg(c)
    except OSError:
        pass
    _QR["keys"].clear()
    _QR.update({"state": "idle", "uname": "", "mid": 0})
    return {"ok": True}
