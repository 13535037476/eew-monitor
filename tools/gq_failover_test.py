"""GQ 主备切换自测：把「主服务器」换成一个必然连不上的端口，
看客户端会不会在两次失败后自动跳到备用机并真的收到数据。

    python tools/gq_failover_test.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gq_client as G  # noqa: E402


def main():
    c = G.GQClient()
    # 假主服务器：本机 1 号端口（必然 connection refused）
    c.servers = [("127.0.0.1", 1, "假主服务器"),
                 ("server-backup.globalquake.net", 38000, "备用服务器")]
    c.hosts = [{"host": h, "port": p, "name": n, "ok": None, "fails": 0,
                "connects": 0, "last_ok_ts": 0, "last_ok_str": "",
                "last_err": "", "last_err_ts": 0} for h, p, n in c.servers]
    c.status.update(host="127.0.0.1", port=1, role="假主服务器", host_idx=0)

    c.start(lambda: None)
    t0 = time.time()
    last = None
    for _ in range(40):
        time.sleep(1)
        st = c.status_snapshot()
        key = (st["host"], st["ok"], st["switch_count"], st["packets"])
        if key != last:
            print("[%2ds] host=%s role=%s ok=%s switches=%d pkts=%d stations=%d events=%d"
                  % (time.time() - t0, st["host"], st["role"], st["ok"],
                     st["switch_count"], st["packets"],
                     len(c.stations_snapshot()), len(c.recent_events(1.0, 200))))
            last = key
        if st["host"] == "server-backup.globalquake.net" and st["ok"] and st["packets"] > 50:
            print("\n✓ 已切到备用机并收到 %d 个包（%.1fs）" % (st["packets"], time.time() - t0))
            print("  体检表：")
            for h in st["hosts"]:
                print("   ", h["name"], h["host"], "ok=%s fails=%d err=%s"
                      % (h["ok"], h["fails"], (h["last_err"] or "")[:50]))
            print("  切换原因：", st["last_switch_reason"])
            return 0
    print("\n✗ 超时：没有完成切换")
    return 1


if __name__ == "__main__":
    sys.exit(main())
