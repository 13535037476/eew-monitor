"""生成 web/icon.ico —— 深色圆角底 + 同心圆 + 橙色波形（与界面里的 logo 一致）

纯标准库：先用 4x 超采样画一遍，再盒式降采样，得到带抗锯齿的多尺寸 ICO。
"""
import math
import os
import struct

SIZES = [16, 24, 32, 48, 64, 128, 256]
SS = 4  # 超采样倍数


def blend(dst, i, r, g, b, a):
    """把 (r,g,b,a) 叠加到 dst[i] 上（dst 为 float RGBA 0..1）"""
    dr, dg, db, da = dst[i]
    na = a + da * (1 - a)
    if na <= 0:
        dst[i] = (0, 0, 0, 0)
        return
    dst[i] = ((r * a + dr * da * (1 - a)) / na,
              (g * a + dg * da * (1 - a)) / na,
              (b * a + db * da * (1 - a)) / na,
              na)


def draw(size):
    n = size * SS
    px = [(0.0, 0.0, 0.0, 0.0)] * (n * n)
    R = n * 0.22  # 圆角半径

    def inside_round(x, y):
        cx = min(max(x, R), n - R)
        cy = min(max(y, R), n - R)
        return (x - cx) ** 2 + (y - cy) ** 2 <= R * R

    # 底
    for y in range(n):
        for x in range(n):
            if inside_round(x + 0.5, y + 0.5):
                blend(px, y * n + x, 0.043, 0.086, 0.133, 1.0)

    # 同心圆
    for frac, alpha, wid in ((0.80, 0.30, 0.020), (0.52, 0.45, 0.018)):
        rr = n * frac / 2
        for y in range(n):
            for x in range(n):
                d = math.hypot(x + 0.5 - n / 2, y + 0.5 - n / 2)
                if abs(d - rr) <= n * wid / 2:
                    blend(px, y * n + x, 0.247, 0.816, 0.878, alpha)

    # 中心点
    rr = n * 0.09
    for y in range(n):
        for x in range(n):
            d = math.hypot(x + 0.5 - n / 2, y + 0.5 - n / 2)
            if d <= rr:
                blend(px, y * n + x, 0.247, 0.816, 0.878, 1.0)

    # 波形（心电样式）
    pts = [(0.06, 0.56), (0.24, 0.56), (0.33, 0.30), (0.44, 0.78),
           (0.53, 0.42), (0.62, 0.56), (0.94, 0.56)]
    thick = n * 0.045
    segs = []
    for i in range(len(pts) - 1):
        x1, y1 = pts[i][0] * n, pts[i][1] * n
        x2, y2 = pts[i + 1][0] * n, pts[i + 1][1] * n
        segs.append((x1, y1, x2, y2))
    for y in range(n):
        for x in range(n):
            pxx, pyy = x + 0.5, y + 0.5
            best = 1e9
            for x1, y1, x2, y2 in segs:
                dx, dy = x2 - x1, y2 - y1
                L2 = dx * dx + dy * dy or 1
                t = max(0.0, min(1.0, ((pxx - x1) * dx + (pyy - y1) * dy) / L2))
                ex, ey = x1 + t * dx, y1 + t * dy
                best = min(best, math.hypot(pxx - ex, pyy - ey))
            if best <= thick / 2:
                blend(px, y * n + x, 1.0, 0.690, 0.125, 1.0)

    # 降采样
    out = []
    for y in range(size):
        row = []
        for x in range(size):
            r = g = b = a = 0.0
            for dy in range(SS):
                for dx in range(SS):
                    pr, pg, pb, pa = px[(y * SS + dy) * n + (x * SS + dx)]
                    r += pr * pa; g += pg * pa; b += pb * pa; a += pa
            k = SS * SS
            if a > 0:
                row.append((r / a, g / a, b / a, a / k))
            else:
                row.append((0, 0, 0, 0))
        out.append(row)
    return out


def ico_image(size):
    """ICO 里的 BMP(DIB) 条目：BITMAPINFOHEADER + BGRA(自下而上) + AND 掩码"""
    img = draw(size)
    hdr = struct.pack("<IiiHHIIiiII", 40, size, size * 2, 1, 32, 0,
                      size * size * 4, 0, 0, 0, 0)
    body = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            r, g, b, a = img[y][x]
            body += bytes((int(b * 255), int(g * 255), int(r * 255), int(a * 255)))
    mask_row = ((size + 31) // 32) * 4
    body += bytes(mask_row * size)
    return hdr + bytes(body)


def main():
    out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "icon.ico")
    images = [(s, ico_image(s)) for s in SIZES]
    data = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries = b""
    for s, blob in images:
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    with open(out, "wb") as f:
        f.write(data + entries + b"".join(b for _, b in images))
    print("icon:", out, os.path.getsize(out) // 1024, "KB")


if __name__ == "__main__":
    main()
