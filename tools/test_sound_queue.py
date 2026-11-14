"""声音调度回归测试（不需要真的放声音）

用假的 MCI 命令接口记录播放通道状态，验证：
  1) 事件语音和台站震度可以同时在播（不同别名，互不打断）
  2) 新的台站震度顶掉旧的（同一别名），但不动事件通道
  3) 最终报之后 _stop_station() 只停台站通道
  4) 事件消息里多段语音是顺序播的
"""
import os
import sys
import time
import wave
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server as S  # noqa: E402

# ---- 假 MCI：记录命令 + 跟踪每个别名是否在播 ----
WORLD = {"playing": {}, "log": []}


def fake_mci(cmd, buf, n, hwnd):
    WORLD["log"].append(cmd)
    parts = cmd.split()
    if not parts:
        return 0
    if parts[0] == "open":
        WORLD["playing"].setdefault(parts[-1], None)
    elif parts[0] == "play":
        WORLD["playing"][parts[-1]] = "playing"
    elif parts[0] in ("stop", "close"):
        WORLD["playing"][parts[1]] = None
    return 0


S._MCI = fake_mci
S.HAVE_MCI = True
S.HAVE_WINSOUND = True


class _W:  # MCI 不可用时的兜底用
    SND_FILENAME = 1
    SND_ASYNC = 2

    def PlaySound(self, p, f=0):
        pass


S.winsound = _W()

D = tempfile.mkdtemp()


def mk(name, dur):
    p = os.path.join(D, name)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * int(8000 * dur))
    return p


a, b = mk("ev_a.wav", 0.35), mk("ev_b.wav", 0.35)
c, d = mk("st_c.wav", 0.35), mk("st_d.wav", 0.35)
ok = True


def check(label, cond):
    global ok
    print(("  PASS " if cond else "  FAIL ") + label)
    if not cond:
        ok = False


print("1) 事件语音与台站震度应当同时在播")
S._snd_enqueue([a, b], 0, "event", 5)
time.sleep(0.08)
S._snd_enqueue([c], 1, "st", 3)
time.sleep(0.08)
check("evt 在播", WORLD["playing"].get("eew_evt") == "playing")
check("st 在播", WORLD["playing"].get("eew_st") == "playing")

print("2) 新台站震度顶掉旧台站，但不动事件通道")
before = len(WORLD["log"])
S._snd_enqueue([d], 1, "st", 3)
time.sleep(0.08)
tail = WORLD["log"][before:]
check("st 通道被 stop 后重开", "stop eew_st" in tail and any(
    x.startswith("open") and "eew_st" in x for x in tail))
# 只允许 setaudio 调音量（台站说话时压低事件语音），不允许 stop/close/open 事件通道
check("没有碰 eew_evt（只允许调音量）", not any(
    "eew_evt" in x and not x.startswith("setaudio") for x in tail))
check("evt 仍在播", WORLD["playing"].get("eew_evt") == "playing")

print("3) 最终报后只停台站通道")
S._stop_station()
time.sleep(0.05)
check("st 已停", WORLD["playing"].get("eew_st") is None)
check("evt 不受影响", WORLD["playing"].get("eew_evt") == "playing")

print("4) 一条消息里的多段语音顺序播")
time.sleep(1.7)
WORLD["log"].clear()
S._snd_enqueue([a, b], 0, "event", 5)
time.sleep(1.0)
plays = [x for x in WORLD["log"] if x.startswith("play eew_evt")]
check("eew_evt 播了 2 次（a、b 各一次）", len(plays) == 2)

print("5) 同一组语音 1.5 秒内重复请求要被丢掉")
time.sleep(1.7)
WORLD["log"].clear()
n1 = S._snd_enqueue([a, b], 0, "event", 5)
time.sleep(0.05)
n2 = S._snd_enqueue([a, b], 0, "event", 5)
check("第一次入队成功", n1 is True)
check("第二次被去重", n2 is False)

print("\nALL PASS" if ok else "\nHAS FAILURES")
sys.exit(0 if ok else 1)
