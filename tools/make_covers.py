# -*- coding: utf-8 -*-
"""B站封面设计稿：1146×717（B站推荐 16:10），三种风格各出一版。

不依赖地图数据，用配色/排版/图形语言表达「地震速报」：
  A 速报卡：左侧大震级，右侧信息列，底部品牌条
  B 震中波纹：深色底 + 网格 + 同心波 + 右下信息块
  C 波形条：地震波形横贯，顶部标题压在上方
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1146, 717
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "eew_release", "covers")
OUT = os.path.abspath(OUT)
FONT = r"C:\Windows\Fonts\msyh.ttc"
FONTB = r"C:\Windows\Fonts\msyhbd.ttc"

BG0 = (7, 13, 21)
BG1 = (12, 22, 34)
CYAN = (74, 163, 255)
RED = (255, 90, 78)
AMBER = (255, 196, 107)
GREY = (150, 170, 190)

EV = dict(place="千葉県東方沖", mag="5.2", depth="40", inten="震度4", src="気象庁 緊急地震速報")


def font(sz, bold=False):
    try:
        return ImageFont.truetype(FONTB if bold else FONT, sz)
    except OSError:
        return ImageFont.load_default()


def vgrad(size, top, bottom):
    w, h = size
    img = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / max(1, h - 1)
        img.putpixel((0, y), tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    return img.resize((w, h))


def glow(base, xy, radius, color, alpha=90):
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x, y = xy
    d.ellipse([x - radius, y - radius, x + radius, y + radius], fill=color + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.55))
    base.alpha_composite(layer)


def brand(d, x, y, size=30, color=(255, 255, 255), sub=True):
    d.text((x, y), "震线 EEW", font=font(size, True), fill=color)
    if sub:
        d.text((x, y + size + 6), "中日地震预警监视台", font=font(int(size * 0.52)), fill=(176, 194, 212))


def chip(d, xy, text, fg, bg, size=30, pad=(18, 8), radius=10):
    f = font(size, True)
    tw = d.textlength(text, font=f)
    x, y = xy
    box = [x, y, x + tw + pad[0] * 2, y + size + pad[1] * 2]
    d.rounded_rectangle(box, radius=radius, fill=bg)
    d.text((x + pad[0], y + pad[1] - 2), text, font=f, fill=fg)
    return box


def cover_a():
    img = Image.new("RGBA", (W, H))
    img.paste(vgrad((W, H), (9, 17, 28), (5, 9, 15)), (0, 0))
    d = ImageDraw.Draw(img)
    glow(img, (W * 0.25, H * 0.42), 330, (255, 90, 78), 70)
    d = ImageDraw.Draw(img)
    # 左上角细网格
    for x in range(0, W, 48):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 8), width=1)
    for y in range(0, H, 48):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 8), width=1)

    brand(d, 60, 52)
    d.text((60, 190), "緊急地震速報", font=font(34, True), fill=AMBER)
    d.text((60, 240), EV["place"], font=font(84, True), fill=(255, 255, 255))
    # 大震级
    d.text((58, 350), "M", font=font(58, True), fill=(255, 255, 255))
    d.text((118, 330), EV["mag"], font=font(128, True), fill=(255, 255, 255))
    chip(d, (330, 372), EV["inten"], (10, 14, 20), (255, 90, 78), size=44, pad=(22, 10), radius=12)
    d.text((60, 520), "深 %s km　·　判定 第 3 報　·　最大震度 %s" % (EV["depth"], EV["inten"]),
           font=font(30), fill=(190, 205, 220))
    # 右侧震中波纹
    cx, cy = W * 0.79, H * 0.52
    for i, r in enumerate((70, 130, 200, 280)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 90, 78, 150 - i * 30), width=3)
    d.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], fill=(255, 90, 78))
    d.text((cx - 58, cy + 300), "震源", font=font(26), fill=(255, 140, 130))
    # 底条
    d.rectangle([0, H - 74, W, H], fill=(8, 13, 20, 235))
    d.line([(0, H - 74), (W, H - 74)], fill=(255, 255, 255, 24), width=1)
    d.text((60, H - 58), "数据源：" + EV["src"], font=font(26), fill=(170, 186, 202))
    d.text((W - 340, H - 58), "自动录制 · 自动投稿", font=font(26), fill=(110, 130, 150))
    return img.convert("RGB")


def cover_b():
    img = Image.new("RGBA", (W, H))
    img.paste(vgrad((W, H), (10, 20, 32), (4, 8, 14)), (0, 0))
    glow(img, (W * 0.62, H * 0.46), 300, (74, 163, 255), 60)
    d = ImageDraw.Draw(img)
    for x in range(0, W, 40):
        d.line([(x, 0), (x, H)], fill=(255, 255, 255, 7), width=1)
    for y in range(0, H, 40):
        d.line([(0, y), (W, y)], fill=(255, 255, 255, 7), width=1)

    # 同心波 + 十字准星
    cx, cy = W * 0.63, H * 0.45
    for i, r in enumerate(range(40, 420, 46)):
        a = max(20, 130 - i * 12)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 90, 78, a), width=2)
    d.line([(cx - 460, cy), (cx + 460, cy)], fill=(255, 255, 255, 18), width=1)
    d.line([(cx, cy - 300), (cx, cy + 300)], fill=(255, 255, 255, 18), width=1)
    d.ellipse([cx - 9, cy - 9, cx + 9, cy + 9], fill=(255, 90, 78))
    d.text((cx + 16, cy - 44), "M" + EV["mag"], font=font(40, True), fill=(255, 255, 255))

    # 左上标题块
    d.text((60, 70), "地震预警", font=font(60, True), fill=(255, 255, 255))
    d.text((62, 146), "EARTHQUAKE EARLY WARNING", font=font(22, True), fill=(110, 132, 154))
    d.line([(60, 196), (420, 196)], fill=(255, 90, 78), width=4)

    # 右下信息块：半透明层必须单独合成，直接画在 RGBA 上会变成实心白
    bx, by = 60, H - 300
    card = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dc = ImageDraw.Draw(card)
    dc.rounded_rectangle([bx, by, bx + 520, by + 210], radius=16,
                         fill=(255, 255, 255, 16), outline=(255, 255, 255, 30))
    img.alpha_composite(card)
    d = ImageDraw.Draw(img)
    d.text((bx + 28, by + 24), EV["place"], font=font(44, True), fill=(255, 255, 255))
    d.text((bx + 28, by + 96), "深 %s km　%s" % (EV["depth"], EV["inten"]), font=font(30), fill=(206, 220, 234))
    d.text((bx + 28, by + 146), EV["src"], font=font(24), fill=(150, 172, 194))
    brand(d, W - 260, H - 96, size=30)
    return img.convert("RGB")


def cover_c():
    img = Image.new("RGBA", (W, H))
    img.paste(vgrad((W, H), (8, 15, 24), (5, 10, 16)), (0, 0))
    d = ImageDraw.Draw(img)
    # 波形
    random.seed(7)
    base = H * 0.66
    pts = []
    for x in range(0, W + 8, 4):
        t = x / W
        env = math.exp(-((t - 0.45) ** 2) / 0.02) * (1 - 0.35 * t)
        y = base - math.sin(x * 0.35) * 46 * env - random.uniform(-6, 6)
        pts.append((x, y))
    for i in range(len(pts) - 1):
        d.line([pts[i], pts[i + 1]], fill=(255, 120, 100), width=3)
    # 二次叠加一层淡波
    pts2 = [(x, base + 26 + math.sin(x * 0.18 + 1.2) * 22 * math.exp(-((x / W - 0.5) ** 2) / 0.05))
            for x in range(0, W + 8, 6)]
    for i in range(len(pts2) - 1):
        d.line([pts2[i], pts2[i + 1]], fill=(255, 90, 78, 90), width=2)
    d.line([(0, base), (W, base)], fill=(255, 255, 255, 24), width=1)

    brand(d, 60, 52)
    d.text((60, 150), EV["place"], font=font(78, True), fill=(255, 255, 255))
    d.text((62, 250), "M" + EV["mag"] + "　深 " + EV["depth"] + " km", font=font(52, True), fill=(255, 205, 120))
    chip(d, (62, 330), "最大震度 " + EV["inten"], (10, 14, 20), (255, 90, 78), size=34, pad=(18, 9))
    d.text((62, H - 92), "自动录制 · " + EV["src"], font=font(26), fill=(150, 168, 186))
    d.text((W - 300, H - 92), "震线 EEW 速报", font=font(26, True), fill=(230, 238, 246))
    return img.convert("RGB")


def main():
    os.makedirs(OUT, exist_ok=True)
    outs = []
    for name, fn in (("A_速报卡", cover_a), ("B_震中波纹", cover_b), ("C_波形条", cover_c)):
        p = os.path.join(OUT, "cover_%s.png" % name)
        fn().save(p, quality=95)
        outs.append(p)
        print("→", p)
    # 拼一张对比图，方便一眼看完
    sheet = Image.new("RGB", (W, H * 3 + 24), (0, 0, 0))
    for i, p in enumerate(outs):
        sheet.paste(Image.open(p), (0, i * (H + 12)))
    sg = os.path.join(OUT, "covers_preview.png")
    sheet.save(sg)
    print("→", sg)


if __name__ == "__main__":
    main()
