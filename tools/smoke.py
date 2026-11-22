# -*- coding: utf-8 -*-
"""冒烟测试：起一个临时端口的服务端，把关键接口和关键前端标记过一遍。

    python tools/smoke.py

「修一个崩一个」不能靠自觉，得有一条 30 秒能跑完的底线。
任何一项失败都会打印原因并以非 0 退出，方便提交前顺手跑一下。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import http.cookiejar

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PORT = 8950 + int(time.time()) % 30
BASE = "http://127.0.0.1:%d" % PORT
ENV_PY = r"C:/Users/86138/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

_ok = _fail = 0


def op():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj),
                                       urllib.request.ProxyHandler({}))


def check(name, fn, opener):
    global _ok, _fail
    try:
        r = fn(opener)
        if r is False:
            raise AssertionError("返回 False")
        print("  ✓ %s" % name)
        _ok += 1
    except Exception as e:
        print("  ✗ %s → %s" % (name, str(e)[:160]))
        _fail += 1


def get_json(o, path):
    with o.open(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def post_json(o, path, obj=None):
    data = json.dumps(obj or {}).encode()
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with o.open(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def main():
    py = ENV_PY if os.path.isfile(ENV_PY) else sys.executable
    srv = subprocess.Popen([py, os.path.join(ROOT, "server.py"),
                            "--port", str(PORT), "--no-browser"], cwd=ROOT)
    try:
        for _ in range(60):
            try:
                urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
                    BASE + "/api/health", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        o = op()

        def login(_o):
            d = post_json(o, "/api/login", {"username": "eggy", "password": "feng1988"})
            return bool(d.get("ok") or d.get("user"))

        check("登录", login, o)

        check("健康检查带版本", lambda _o: bool(get_json(o, "/api/health").get("version")), o)
        check("状态快照", lambda _o: "sources" in get_json(o, "/api/state"), o)
        check("设置读取", lambda _o: "settings" in get_json(o, "/api/settings"), o)
        check("我的令牌", lambda _o: "own" in get_json(o, "/api/mytokens"), o)
        check("B站状态", lambda _o: get_json(o, "/api/bili/status").get("ok") is True, o)
        check("B站二维码", lambda _o: len(post_json(o, "/api/bili/qr/new").get("svg") or "") > 1000, o)
        check("录屏帧（无录制时 204）", lambda _o: _expect_204(o), o)
        check("数据源开关（关再开）", lambda _o: _toggle_roundtrip(o), o)
        check("开放链接协议校验", lambda _o: _reject_file_url(o), o)
        check("CapQuake 三路已接入", lambda _o: _cap_sources(o), o)

        # 前端关键标记：这几处坏过，得盯住
        html = o.open(BASE + "/", timeout=10).read().decode("utf-8", "replace")
        js = o.open(BASE + "/app.js?v=308", timeout=10).read().decode("utf-8", "replace")
        check("表格有 4 列 + 开关列", lambda _o: "<th>开</th>" in html, o)
        check("Jian 引导常驻", lambda _o: "jh-guide" in html and "auth.sismotide.top" in html, o)
        check("感谢条（技术支持 Mon3tr）", lambda _o: "Mon3tr" in html, o)
        check("前端有 srcsw / kmoniTop", lambda _o: "srcsw" in js and "kmoniTop" in js, o)
        check("自定义封面入口", lambda _o: "biliCoverFile" in html, o)
        check("启动不弹旧事件（种子逻辑）", lambda _o: "alertSeeded" in js, o)
    finally:
        srv.terminate()
    print("\n%d 通过 / %d 失败" % (_ok, _fail))
    return 0 if _fail == 0 else 1


def _cap_sources(o):
    ids = {s["id"] for s in get_json(o, "/api/state")["sources"]}
    need = {"cap_cea", "cap_jma", "cap_cwa"}
    if not need <= ids:
        raise AssertionError("缺 " + str(need - ids))
    return True


def _expect_204(o):
    req = urllib.request.Request(BASE + "/api/rec/frame")
    try:
        with o.open(req, timeout=10) as r:
            return r.status == 204
    except urllib.error.HTTPError as e:
        return e.code == 204


def _toggle_roundtrip(o):
    sid = "jma_eew"
    post_json(o, "/api/source/toggle", {"id": sid, "on": False})
    time.sleep(0.4)
    row = [s for s in get_json(o, "/api/state")["sources"] if s["id"] == sid]
    off = bool(row) and row[0].get("enabled") is False and row[0]["state"] == "off"
    post_json(o, "/api/source/toggle", {"id": sid, "on": True})
    time.sleep(0.4)
    row = [s for s in get_json(o, "/api/state")["sources"] if s["id"] == sid]
    on = bool(row) and row[0].get("enabled") is True
    if not (off and on):
        raise AssertionError("关=%s 开=%s" % (off, on))
    return True


def _reject_file_url(o):
    try:
        post_json(o, "/api/open-url", {"url": "file:///C:/Windows"})
    except urllib.error.HTTPError as e:
        return e.code == 400
    return False


if __name__ == "__main__":
    sys.exit(main())
