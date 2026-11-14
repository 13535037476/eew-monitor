# -*- coding: utf-8 -*-
"""本地账号体系：SQLite + PBKDF2 + 会话 Cookie

不依赖第三方库，随程序走。数据落在 data/users.db。
- 第一个注册的用户自动成为管理员；
- 密码只存 PBKDF2-HMAC-SHA256（20 万次）的哈希 + 每用户随机盐；
- 会话 token 只存 SHA-256，数据库泄露也拿不到可用 token；
- 注册可用环境变量控制：
    EEW_REGISTER=0     关闭注册（只能由管理员在服务器上建号）
    EEW_INVITE=xxxx    注册必须填邀请码
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager

DB_PATH = None
_LOCK = threading.RLock()
_NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5.-]{2,24}$")


def init(path):
    global DB_PATH
    DB_PATH = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            salt TEXT NOT NULL,
            pwd TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL,
            last_login REAL
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS sessions(
            token TEXT PRIMARY KEY,
            uid INTEGER NOT NULL,
            created REAL NOT NULL,
            expires REAL NOT NULL,
            ua TEXT
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_sess_uid ON sessions(uid)")
        # 设备管理要能看清「这是哪台机器」，老库平滑加列。
        cols = {r["name"] for r in c.execute("PRAGMA table_info(sessions)")}
        for col, ddl in (("ip", "TEXT"), ("last_seen", "REAL")):
            if col not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN %s %s" % (col, ddl))


@contextmanager
def _db():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _hash(pwd, salt):
    return hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"),
                               bytes.fromhex(salt), 200000).hex()


def _public(row):
    return {"name": row["username"], "admin": bool(row["is_admin"]),
            "created": row["created"], "last_login": row["last_login"]}


def register(username, password, invite=None):
    username = (username or "").strip()
    password = password or ""
    if not _NAME_RE.match(username):
        raise ValueError("用户名需 2~24 位，可用中英文、数字、下划线")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    if os.environ.get("EEW_REGISTER", "1") in ("0", "false", "False"):
        raise ValueError("服务器已关闭注册，请联系管理员开通账号")
    need = os.environ.get("EEW_INVITE")
    if need and not hmac.compare_digest(str(invite or ""), need):
        raise ValueError("邀请码不正确")
    with _LOCK, _db() as c:
        n = c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        salt = secrets.token_hex(16)
        try:
            c.execute("INSERT INTO users(username,salt,pwd,is_admin,created) "
                      "VALUES(?,?,?,?,?)",
                      (username, salt, _hash(password, salt), 1 if n == 0 else 0,
                       time.time()))
        except sqlite3.IntegrityError:
            raise ValueError("这个用户名已经有人用了")
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return _public(row)


def verify(username, password):
    username = (username or "").strip()
    with _LOCK, _db() as c:
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not row:
        return None
    if not hmac.compare_digest(row["pwd"], _hash(password or "", row["salt"])):
        return None
    with _LOCK, _db() as c:
        c.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
    return _public(row)


def new_session(user, ua="", ip=""):
    token = secrets.token_urlsafe(32)
    th = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _LOCK, _db() as c:
        uid = c.execute("SELECT id FROM users WHERE username=?", (user["name"],)).fetchone()["id"]
        c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        c.execute("INSERT INTO sessions(token,uid,created,expires,ua,ip,last_seen) "
                  "VALUES(?,?,?,?,?,?,?)",
                  (th, uid, now, now + 30 * 86400, (ua or "")[:200],
                   (ip or "")[:64], now))
    return token


def touch(token):
    """记录一次活跃。节流到 60 秒一次，别每个请求都写库。"""
    if not token:
        return
    th = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _LOCK, _db() as c:
        c.execute("UPDATE sessions SET last_seen=? "
                  "WHERE token=? AND (last_seen IS NULL OR last_seen < ?)",
                  (now, th, now - 60))


def devices(username):
    """某账号的所有有效会话（每台设备一条），给「人员管理」用。"""
    now = time.time()
    with _LOCK, _db() as c:
        rows = c.execute(
            """SELECT s.token, s.ua, s.ip, s.created, s.expires, s.last_seen
                 FROM sessions s JOIN users u ON u.id=s.uid
                WHERE u.username=? AND s.expires>? ORDER BY s.created DESC""",
            (username, now)).fetchall()
    out = []
    for r in rows:
        out.append({
            "id": (r["token"] or "")[:12],
            "ua": r["ua"] or "",
            "ip": r["ip"] or "",
            "created": r["created"],
            "expires": r["expires"],
            "last_seen": r["last_seen"] or r["created"],
        })
    return out


def revoke_device(username, sid):
    """踢掉某账号的某一台设备（会话 id 见 devices()）。返回是否命中。"""
    sid = (sid or "").strip()
    if not sid:
        return False
    with _LOCK, _db() as c:
        rows = c.execute(
            """SELECT s.token FROM sessions s JOIN users u ON u.id=s.uid
                WHERE u.username=?""", (username,)).fetchall()
        hit = [r["token"] for r in rows if (r["token"] or "").startswith(sid)]
        for th in hit:
            c.execute("DELETE FROM sessions WHERE token=?", (th,))
        return bool(hit)


def user_by_token(token):
    if not token:
        return None
    th = hashlib.sha256(token.encode()).hexdigest()
    now = time.time()
    with _LOCK, _db() as c:
        row = c.execute("""SELECT u.* FROM sessions s JOIN users u ON u.id=s.uid
                           WHERE s.token=? AND s.expires>?""", (th, now)).fetchone()
    return _public(row) if row else None


def revoke(token):
    if not token:
        return
    th = hashlib.sha256(token.encode()).hexdigest()
    with _LOCK, _db() as c:
        c.execute("DELETE FROM sessions WHERE token=?", (th,))


def set_password(username, password):
    if len(password or "") < 6:
        raise ValueError("密码至少 6 位")
    salt = secrets.token_hex(16)
    with _LOCK, _db() as c:
        cur = c.execute("UPDATE users SET salt=?,pwd=? WHERE username=?",
                        (salt, _hash(password, salt), username))
        if cur.rowcount == 0:
            raise ValueError("用户不存在")
        c.execute("DELETE FROM sessions WHERE uid=(SELECT id FROM users WHERE username=?)",
                  (username,))


def is_admin(username):
    with _LOCK, _db() as c:
        row = c.execute("SELECT is_admin FROM users WHERE username=?", (username,)).fetchone()
    return bool(row and row["is_admin"])


def count():
    with _LOCK, _db() as c:
        return c.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]


def usernames():
    """所有用户名。按账号加载各自的 tokens.json 时用。"""
    with _LOCK, _db() as c:
        return [r["username"] for r in
                c.execute("SELECT username FROM users ORDER BY id")]


# ---------------- 管理员用：账号管理 ----------------

def create(username, password, admin=False):
    """管理员建号。绕过 EEW_REGISTER=0 与邀请码——管理员本机建号不需要邀请。"""
    username = (username or "").strip()
    password = password or ""
    if not _NAME_RE.match(username):
        raise ValueError("用户名需 2~24 位，可用中英文、数字、下划线")
    if len(password) < 6:
        raise ValueError("密码至少 6 位")
    salt = secrets.token_hex(16)
    with _LOCK, _db() as c:
        try:
            c.execute("INSERT INTO users(username,salt,pwd,is_admin,created) "
                      "VALUES(?,?,?,?,?)",
                      (username, salt, _hash(password, salt), 1 if admin else 0,
                       time.time()))
        except sqlite3.IntegrityError:
            raise ValueError("这个用户名已经有人用了")
        row = c.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        return _public(row)


def list_all():
    """账号列表 + 每个账号当前有效会话数（管理员面板用）。"""
    now = time.time()
    with _LOCK, _db() as c:
        rows = c.execute("""SELECT u.*,
                              (SELECT COUNT(*) FROM sessions s
                                WHERE s.uid=u.id AND s.expires>?) AS n_sess
                            FROM users u ORDER BY u.id""", (now,)).fetchall()
    out = []
    for r in rows:
        d = _public(r)
        d["sessions"] = int(r["n_sess"] or 0)
        out.append(d)
    return out


def delete_user(username):
    """删号：连带会话一起清掉。调用方负责清理该账号的 data/users/<name>/ 目录。"""
    with _LOCK, _db() as c:
        row = c.execute("SELECT id,is_admin FROM users WHERE username=?",
                        (username,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if row["is_admin"]:
            n = c.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin=1").fetchone()["n"]
            if n <= 1:
                raise ValueError("这是最后一个管理员，删掉就没人能管了")
        c.execute("DELETE FROM sessions WHERE uid=?", (row["id"],))
        c.execute("DELETE FROM users WHERE id=?", (row["id"],))


def set_admin(username, flag):
    with _LOCK, _db() as c:
        row = c.execute("SELECT id,is_admin FROM users WHERE username=?",
                        (username,)).fetchone()
        if not row:
            raise ValueError("用户不存在")
        if not flag and row["is_admin"]:
            n = c.execute("SELECT COUNT(*) AS n FROM users WHERE is_admin=1").fetchone()["n"]
            if n <= 1:
                raise ValueError("至少要留一个管理员")
        c.execute("UPDATE users SET is_admin=? WHERE id=?",
                  (1 if flag else 0, row["id"]))


def kick(username):
    """把这个账号的所有会话清掉，等于强制下线全部设备。"""
    with _LOCK, _db() as c:
        cur = c.execute("DELETE FROM sessions WHERE uid="
                        "(SELECT id FROM users WHERE username=?)", (username,))
        return cur.rowcount


def exists(username):
    with _LOCK, _db() as c:
        return c.execute("SELECT 1 FROM users WHERE username=?",
                         (username,)).fetchone() is not None
