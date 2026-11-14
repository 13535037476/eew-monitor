# -*- coding: utf-8 -*-
"""离线复算 kmoni jma_s 图的取色命中率，验证前端 `n > 800` 采纳门槛是否够。

自带最小 GIF 解码（不依赖 Pillow）：LZW + 索引色 → RGB。
用法：python tools/probe_kmoni.py
"""
import datetime
import io
import json
import os
import re
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def lzw_decode(data, min_code_size):
    clear = 1 << min_code_size
    end = clear + 1
    dict_size = end + 1
    code_size = min_code_size + 1
    table = [[i] for i in range(clear)] + [[], []]
    out = bytearray()
    bitbuf = 0
    bitcnt = 0
    prev = None
    for byte in data:
        bitbuf |= byte << bitcnt
        bitcnt += 8
        while bitcnt >= code_size:
            code = bitbuf & ((1 << code_size) - 1)
            bitbuf >>= code_size
            bitcnt -= code_size
            if code == clear:
                table = [[i] for i in range(clear)] + [[], []]
                dict_size = end + 1
                code_size = min_code_size + 1
                prev = None
                continue
            if code == end:
                return bytes(out)
            if prev is None:
                entry = table[code]
                out += bytes(entry)
                prev = entry
                continue
            if code < len(table) and table[code]:
                entry = table[code]
            else:
                entry = prev + [prev[0]]
            out += bytes(entry)
            table.append(prev + [entry[0]])
            dict_size += 1
            if dict_size >= (1 << code_size) and code_size < 12:
                code_size += 1
            prev = entry
    return bytes(out)


def read_gif(buf):
    """返回 (w, h, rgb_bytes, interlaced)"""
    assert buf[:3] == b"GIF", "not gif"
    w = buf[6] | (buf[7] << 8)
    h = buf[8] | (buf[9] << 8)
    packed = buf[10]
    pos = 13
    gct = None
    if packed & 0x80:
        n = 1 << ((packed & 7) + 1)
        gct = [tuple(buf[pos + i * 3: pos + i * 3 + 3]) for i in range(n)]
        pos += n * 3
    while pos < len(buf):
        b = buf[pos]
        pos += 1
        if b == 0x3B:
            break
        if b == 0x21:
            pos += 1                      # 扩展标签
            while pos < len(buf):
                sz = buf[pos]
                pos += 1
                if sz == 0:
                    break
                pos += sz
            continue
        if b != 0x2C:
            break
        left = buf[pos] | (buf[pos + 1] << 8)
        top = buf[pos + 2] | (buf[pos + 3] << 8)
        iw = buf[pos + 4] | (buf[pos + 5] << 8)
        ih = buf[pos + 6] | (buf[pos + 7] << 8)
        ipacked = buf[pos + 8]
        pos += 9
        lct = gct
        if ipacked & 0x80:
            n = 1 << ((ipacked & 7) + 1)
            lct = [tuple(buf[pos + i * 3: pos + i * 3 + 3]) for i in range(n)]
            pos += n * 3
        mcs = buf[pos]
        pos += 1
        chunks = []
        while pos < len(buf):
            sz = buf[pos]
            pos += 1
            if sz == 0:
                break
            chunks.append(buf[pos:pos + sz])
            pos += sz
        idx = lzw_decode(b"".join(chunks), mcs)
        if ipacked & 0x40:                # 交错：4 段重排
            rows = [idx[i * iw:(i + 1) * iw] for i in range(ih)]
            order = (list(range(0, ih, 8)) + list(range(4, ih, 8)) +
                     list(range(2, ih, 4)) + list(range(1, ih, 2)))
            fixed = [None] * ih
            for src_i, dst_i in enumerate(order):
                if src_i < len(rows):
                    fixed[dst_i] = rows[src_i]
            idx = b"".join(r for r in fixed if r)
        rgb = bytearray(iw * ih * 3)
        for i, k in enumerate(idx[:iw * ih]):
            c = lct[k] if lct and k < len(lct) else (0, 0, 0)
            rgb[i * 3:i * 3 + 3] = bytes(c)
        return iw, ih, bytes(rgb), bool(ipacked & 0x40)
    raise ValueError("no image block")


def grab(off=2.0):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=9, seconds=-off)
    t = t.replace(microsecond=0)
    ts = t.strftime("%Y%m%d%H%M%S")
    url = ("http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_s/"
           + ts[:8] + "/" + ts + ".jma_s.gif")
    req = urllib.request.Request(url, headers={
        "Referer": "http://www.kmoni.bosai.go.jp/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return ts, r.read()


def main():
    src = io.open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    pal = json.loads("[" + re.search(r"const KMONI_PAL = \[(.*?)\];", src, re.S).group(1) + "]")
    pmap = {tuple(p): i * 0.1 - 3 for i, p in enumerate(pal)}
    sx, bx, sy, by = 20.25, -2603.611, -24.5, 1133.952
    ring = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1),
            (2, 0), (-2, 0), (0, 2), (0, -2), (2, 1), (2, -1), (-2, 1), (-2, -1),
            (1, 2), (-1, 2), (1, -2), (-1, -2)]
    stations = json.load(io.open(os.path.join(ROOT, "data", "knet_stations.json"),
                                 encoding="utf-8"))["stations"]

    def valof(r, g, b):
        v = pmap.get((r, g, b))
        if v is not None:
            return v
        if r + g + b < 30:
            return None
        best, bd = None, 40
        for i, p in enumerate(pal):
            d = (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2
            if d < bd:
                bd, best = d, i * 0.1 - 3
        return best

    ts, data = grab()
    w, h, rgb, inter = read_gif(data)
    print("帧 %s  %dx%d  %dB  交错=%s" % (ts, w, h, len(data), inter))
    hit = out = miss = 0
    hist = {}
    for s in stations:
        lat, lon = s[3], s[4]
        cx, cy = round(sx * lon + bx), round(sy * lat + by)
        if cx < 0 or cy < 0 or cx >= w or cy >= h:
            out += 1
            continue
        v = None
        for ox, oy in ring:
            x, y = cx + ox, cy + oy
            if x < 0 or y < 0 or x >= w or y >= h:
                continue
            i = (y * w + x) * 3
            v = valof(rgb[i], rgb[i + 1], rgb[i + 2])
            if v is not None:
                break
        if v is None:
            miss += 1
        else:
            hit += 1
            k = round(v, 1)
            hist[k] = hist.get(k, 0) + 1
    print("命中 %d / 幅内 %d（图幅外 %d，未命中 %d）" % (hit, len(stations) - out, out, miss))
    print("最常见震度:", sorted(hist.items(), key=lambda kv: -kv[1])[:8])


if __name__ == "__main__":
    main()
