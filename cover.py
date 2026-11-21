# -*- coding: utf-8 -*-
"""投稿封面（A 版「速报卡」）：1146×717，按事件参数实时生成。

设计要点（定稿 A 版）：
  · 深蓝渐变底 + 细网格，左上品牌，中间大震级与震度色块
  · 右侧同心波标出震源，底部数据源条
  · 数值全部来自真实事件，不画假数据
"""
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1146, 717
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONTB = r"C:\Windows\Fonts\msyhbd.ttc"

CYAN = (74, 163, 255)
RED = (255, 90, 78)
AMBER = (255, 196, 107)


def _font(sz, bold=False):
    try:
        return ImageFont.truetype(FONTB if bold else FONT, sz)
    except OSError:
        return ImageFont.load_default()


def _grad(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img.resize((w, h))


def _glow(base, xy, radius, color, alpha=70):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    d.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.55))
    base.alpha_composite(layer)


def make_cover(meta, out_path):
    """meta: place/mag/depth/inten/src/serial/est；返回生成的文件路径。"""
    place = str(meta.get("place") or "地震")[:22]
    mag = meta.get("mag")
    mag_s = ("%.1f" % float(mag)) if mag not in (None, "", "?") else "—"
    depth = meta.get("depth")
    depth_s = str(int(float(depth))) if depth not in (None, "", "?") else "—"
    inten = str(meta.get("inten") or "").strip() or "—"
    src = str(meta.get("src") or "地震速報")[:28]
    serial = meta.get("serial")
    est = bool(meta.get("est"))

    img = Image.new("RGBA", (W, H))
    img.paste(_grad((W, H), (9, 17, 28), (5, 9, 15)), (0, 0))
    _glow(img, (W * 0.25, H * 0.42), 330, RED, 70)
    d = ImageDraw.Draw(img)
    for x in range(0, W, 48):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 8), width=1)
    for y in range(0, H, 48):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 8), width=1)

    # 品牌
    d.text((60, 52), "震线 EEW", font=_font(30, True), fill=(255, 255, 255))
    d.text((60, 92), "中日地震预警监视台", font=_font(16), fill=(176, 194, 212))

    d.text((60, 186), "緊急地震速報" if meta.get("jp", True) else "地震预警",
           font=_font(32, True), fill=AMBER)
    # 地点（长名自动缩字号）
    fsize = 84
    while fsize > 40 and d.textlength(place, font=_font(fsize, True)) > W * 0.56:
        fsize -= 4
    d.text((60, 236), place, font=_font(fsize, True), fill=(255, 255, 255))

    # 大震级
    d.text((58, 372), "M", font=_font(56, True), fill=(255, 255, 255))
    d.text((112, 330), mag_s, font=_font(126, True), fill=(255, 255, 255))

    # 最大震度色块（inten 本身可能已经带「震度/烈度」前缀，别叠成「震度 震度4」）
    f = _font(42, True)
    jp = bool(meta.get("jp", True))
    scale = "震度" if jp else "烈度"
    core = inten.replace("震度", "").replace("烈度", "").strip()
    label = (scale + " " + core) if core and core != "—" else (scale + " —")
    tw = d.textlength(label, font=f)
    bx, by = 330, 366
    d.rounded_rectangle([bx, by, bx + tw + 44, by + 66], radius=12, fill=RED)
    d.text((bx + 22, by + 8), label, font=f, fill=(10, 14, 20))

    line = "深 %s km" % depth_s
    if serial:
        line += "　·　第 %s 報" % serial
    if est:
        line += "　·　震度按峰值 PGA 估算"
    d.text((60, 524), line, font=_font(28), fill=(190, 205, 220))

    # 右侧同心波
    cx, cy = W * 0.79, H * 0.52
    for i, r in enumerate((70, 130, 200, 280)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 90, 78, 150 - i * 30), width=3)
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=RED)
    d.text((cx - 30, cy + 300), "震源", font=_font(24), fill=(255, 140, 130))

    # 底条
    d.rectangle([0, H - 74, W, H], fill=(8, 13, 20, 235))
    d.line([(0, H - 74), (W, H - 74)], fill=(255, 255, 255, 24), width=1)
    d.text((60, H - 58), "数据源：" + src, font=_font(24), fill=(170, 186, 202))
    d.text((W - 300, H - 58), "自动录制 · 自动投稿", font=_font(24), fill=(110, 130, 150))

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=92)
    return out_path
