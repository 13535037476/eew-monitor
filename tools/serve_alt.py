"""测试用：把 GQ「主服务器」换成一个连不上的地址，验证切换与「备」角标。

    python tools/serve_alt.py [port]

只在本进程里改 CLIENT.servers，不动源码默认值。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import gq_client  # noqa: E402

gq_client.CLIENT.servers = [("127.0.0.1", 1, "主服务器"),
                            ("server-backup.globalquake.net", 38000, "备用服务器")]
gq_client.CLIENT.hosts = [{"host": h, "port": p, "name": n, "ok": None, "fails": 0,
                           "connects": 0, "last_ok_ts": 0, "last_ok_str": "",
                           "last_err": "", "last_err_ts": 0}
                          for h, p, n in gq_client.CLIENT.servers]

import server  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    srv = server.boot("127.0.0.1", port)
    print("alt server on %d" % port)
    srv.serve_forever()
