# -*- coding: utf-8 -*-
"""语音包体检 / 补空槽（严格不覆盖手工标注）

规则（用户定）：
  1. index.json 里已有的条目 = 用户手工标注，一个字段都不改；
  2. 只有「某个 key 缺 plain 或 缺 voice」时，才尝试用**没被 index 引用过的 wav** 去补；
  3. 补进来的条目带 "auto": true，便于区分；
  4. 没有未标注文件就什么都不做——绝不借用别的 key 的音频，避免串音。

用法：
  python tools/voice_pack.py audit
  python tools/voice_pack.py fill --write
"""
import json, os, sys, wave, struct, math

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web", "sounds", "voice")
IDX = os.path.join(ROOT, "index.json")

# key -> 语义（用于给自动补的文件写 label；只影响新增条目）
LABELS = {
    "quake": "地震发生", "update": "更新报", "final": "最终报", "cancel": "JMA 取消",
    "exp2": "预想最大震度 2", "exp3": "预想最大震度 3", "exp4": "预想最大震度 4",
    "exp5m": "预想最大震度 5弱",
    "st0": "震度 0 · 检测到测站反应", "st1": "震度 1", "st2": "震度 2", "st3": "震度 3",
    "st4": "震度 4", "st5m": "震度 5弱", "st5p": "震度 5強", "st6m": "震度 6弱",
    "st6p": "震度 6強", "st7": "震度 7",
    # v1.30：海啸与震度速報也进了语音包
    "tsunSiren": "津波警报 · 警报前奏",
    "tsunWatch": "津波注意报", "tsunWarn": "津波警报", "tsunMajor": "大津波警报",
    "sokuho": "震度速報", "sokuho6": "震度速報（6弱以上）", "sokuhoResult": "震度結果",
}
GROUPS = {"jma_eew": "event"}


def group_of(key):
    if key.startswith("st"):
        return "station"
    if key.startswith("exp"):
        return "exp"
    return "event"


def probe(path):
    """返回 (时长, 是否人声, 过零率)"""
    try:
        w = wave.open(path, "rb")
        n, sr, ch, sw = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(n)
        w.close()
        a = struct.unpack("<%dh" % (len(raw) // 2), raw) if sw == 2 else [b - 128 for b in raw]
        if ch == 2:
            a = a[::2]
        if not a:
            return 0, False, 0
        zc = sum(1 for i in range(1, len(a)) if (a[i - 1] < 0) != (a[i] < 0)) / max(1, len(a) - 1)
        return n / sr, zc > 0.12, zc
    except Exception:
        return 0, False, 0


def load():
    with open(IDX, "r", encoding="utf-8") as f:
        return json.load(f)


def audit():
    d = load()
    items = d.get("items") or []
    used = {it["file"] for it in items}
    allw = sorted(f for f in os.listdir(ROOT) if f.lower().endswith(".wav"))
    unused = [f for f in allw if f not in used]
    slots = {}
    for it in items:
        k = it["key"]
        s = slots.setdefault(k, {"plain": None, "voice": None})
        s["voice" if it.get("voice") else "plain"] = it
    print("index 条目 %d，wav 文件 %d，未标注文件 %d" % (len(items), len(allw), len(unused)))
    if unused:
        print("未标注（可用来补空槽）：")
        for f in unused:
            dur, human, zc = probe(os.path.join(ROOT, f))
            print("  %-16s %5.2fs  %s  zc=%.3f" % (f, dur, "人声" if human else "提示音", zc))
    print("\n空槽：")
    miss = 0
    for k in sorted(slots):
        for v in ("plain", "voice"):
            if not slots[k][v]:
                miss += 1
                print("  %-7s 缺 %s" % (k, "提示音" if v == "plain" else "人声"))
    if not miss:
        print("  （无）")
    return unused, slots


def fill(write=False):
    d = load()
    items = d.get("items") or []
    used = {it["file"] for it in items}
    allw = sorted(f for f in os.listdir(ROOT) if f.lower().endswith(".wav"))
    unused = [f for f in allw if f not in used]
    slots = {}
    for it in items:
        slots.setdefault(it["key"], {"plain": None, "voice": None})[
            "voice" if it.get("voice") else "plain"] = it
    added = []
    for f in unused:
        dur, human, zc = probe(os.path.join(ROOT, f))
        base = os.path.splitext(f)[0]
        # 文件名必须是已知语义 key 或 key-v；不做语义猜测，避免乱配
        key = base[:-2] if base.endswith("-v") else base
        if key not in LABELS:
            continue
        # 命名优先：xxx-v.wav 一律当人声槽；没有 -v 才靠波形判断
        want_voice = True if base.endswith("-v") else human
        slot = "voice" if want_voice else "plain"
        if slots.get(key, {}).get(slot):
            continue                      # 用户已标注，绝不覆盖
        item = {"key": key, "label": LABELS[key], "group": group_of(key),
                "file": f, "dur": round(dur, 2), "voice": want_voice, "auto": True}
        items.append(item)
        slots.setdefault(key, {"plain": None, "voice": None})[slot] = item
        added.append(item)
    print("可补 %d 条" % len(added))
    for it in added:
        print("  + %-7s %-6s %s %.2fs" % (it["key"], "人声" if it["voice"] else "提示音", it["file"], it["dur"]))
    if write and added:
        d["items"] = items
        with open(IDX, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        print("已写入 index.json（原有条目未改动）")
    elif added:
        print("（dry-run，加 --write 才写入）")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "audit"
    if cmd == "audit":
        audit()
    elif cmd == "fill":
        fill("--write" in sys.argv)
    else:
        print(__doc__)
