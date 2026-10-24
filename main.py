# -*- coding: utf-8 -*-
"""
🚀 HostBot Pro — Telegram Bot Hosting Bot (pyTelegramBotAPI / telebot)
Render Free Web Service ላይ እንዲሰራ የተዘጋጀ።

Render Environment Variables:
  BOT_TOKEN   = የዚህ host bot token
  ADMIN_IDS   = የእርስዎ Telegram ID (ብዙ ከሆነ በ comma: 123,456)
  PYTHON_VERSION = 3.11.9
Start Command : python bot.py
Build Command : pip install -r requirements.txt
"""
import os, sys, io, re, ast, time, json, html, shutil, sqlite3, zipfile, threading, subprocess, signal, urllib.request
from datetime import datetime
import telebot
from telebot import types
from flask import Flask

try:
    import resource
except ImportError:
    resource = None

# ───────────────────────── CONFIG ─────────────────────────
TOKEN = os.environ.get("BOT_TOKEN", "").strip()
ADMINS = [int(x) for x in re.findall(r"\d+", os.environ.get("ADMIN_IDS", ""))]
PORT = int(os.environ.get("PORT", "10000"))
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", "").strip()
DATA = os.path.abspath(os.environ.get("DATA_DIR", "hostdata"))
BOTS_DIR = os.path.join(DATA, "bots")
DB_PATH = os.path.join(DATA, "host.db")
os.makedirs(BOTS_DIR, exist_ok=True)
if not TOKEN:
    sys.exit("❌ BOT_TOKEN አልተሞላም (Render > Environment)")
if not ADMINS:
    print("⚠️ ADMIN_IDS አልተሞላም — Admin panel አይሰራም")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML", threaded=True, num_threads=10)
esc = html.escape
now = lambda: int(time.time())
fdate = lambda t: datetime.fromtimestamp(t).strftime("%Y-%m-%d")

DEFAULTS = {
    "limit_free": "1", "limit_premium": "5", "limit_vip": "15",
    "price_premium": "100 ብር / ወር", "price_vip": "250 ብር / ወር",
    "pay_info": "💳 CBE: 1000XXXXXXXXX\n📱 Telebirr: 09XXXXXXXX\n\nክፍያ ከፈጸሙ በኋላ screenshot ይላኩ።",
    "plan_days": "30", "ref_days": "1", "force_channel": "", "maintenance": "0",
    "scan": "1", "max_total": "25", "max_restarts": "5", "max_mem_mb": "300",
    "support": "@admin",
    "welcome": "👋 እንኳን ደህና መጡ!\n\nየ telebot .py ወይም .zip ፋይልዎን ይላኩልኝ — ዋናውን ፋይል ራሴ ለይቼ፣ የሚያስፈልጉ libraries ጭኜ 24/7 አስተናግድልዎታለሁ።",
}

# ───────────────────────── DATABASE ─────────────────────────
_lock = threading.RLock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript("""
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,username TEXT,joined INTEGER,banned INTEGER DEFAULT 0,plan TEXT DEFAULT 'free',until INTEGER DEFAULT 0,ref_by INTEGER DEFAULT 0,refs INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS bots(id INTEGER PRIMARY KEY AUTOINCREMENT,owner INTEGER,name TEXT,main TEXT DEFAULT '',status TEXT DEFAULT 'stopped',autorestart INTEGER DEFAULT 1,restarts INTEGER DEFAULT 0,env TEXT DEFAULT '{}',created INTEGER,last_start INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS files(bot_id INTEGER PRIMARY KEY,fname TEXT,data BLOB);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY,v TEXT);
CREATE TABLE IF NOT EXISTS payments(id INTEGER PRIMARY KEY AUTOINCREMENT,user INTEGER,plan TEXT,file_id TEXT,status TEXT DEFAULT 'pending',ts INTEGER);
""")
db.commit()
dirty = [False]
TABLES = ["users", "bots", "files", "settings", "payments"]


def q(sql, args=()):
    with _lock:
        return db.execute(sql, args).fetchall()


def q1(sql, args=()):
    r = q(sql, args)
    return r[0] if r else None


def ex(sql, args=()):
    with _lock:
        cur = db.execute(sql, args)
        db.commit()
        dirty[0] = True
        return cur.lastrowid


def S(k):
    r = q1("SELECT v FROM settings WHERE k=?", (k,))
    return r["v"] if r else DEFAULTS.get(k, "")


def setS(k, v):
    ex("INSERT OR REPLACE INTO settings VALUES(?,?)", (k, v))


# ───────────────────────── HELPERS ─────────────────────────
isadm = lambda uid: uid in ADMINS


def send(chat, text, kb=None, **kw):
    try:
        return bot.send_message(chat, text[:4090], reply_markup=kb, **kw)
    except Exception as e:
        print("send err", e)


def edit(c, text, kb=None):
    try:
        bot.edit_message_text(text[:4090], c.message.chat.id, c.message.message_id, reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e):
            send(c.message.chat.id, text, kb)


def ensure_user(user, ref=None):
    r = q1("SELECT * FROM users WHERE id=?", (user.id,))
    if r:
        return r
    ref_by = 0
    if ref and ref != user.id and q1("SELECT id FROM users WHERE id=?", (ref,)):
        ref_by = ref
    ex("INSERT INTO users(id,name,username,joined,ref_by) VALUES(?,?,?,?,?)",
       (user.id, user.first_name or "", user.username or "", now(), ref_by))
    if ref_by:
        ex("UPDATE users SET refs=refs+1 WHERE id=?", (ref_by,))
        days = int(S("ref_days") or 0)
        if days > 0:
            add_days(ref_by, days)
            send(ref_by, f"🎉 አዲስ ሰው በ link ዎ ገብቷል! +{days} ቀን Premium ተጨምሯል።")
    for a in ADMINS:
        send(a, f"🆕 አዲስ user: <a href='tg://user?id={user.id}'>{esc(user.first_name or 'user')}</a> (<code>{user.id}</code>)")
    return q1("SELECT * FROM users WHERE id=?", (user.id,))


def add_days(uid, days, plan=None):
    u = q1("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return
    base = max(u["until"], now())
    plan = plan or (u["plan"] if u["plan"] != "free" else "premium")
    ex("UPDATE users SET plan=?,until=? WHERE id=?", (plan, base + days * 86400, uid))


def limit_of(uid):
    if isadm(uid):
        return 999
    u = q1("SELECT plan FROM users WHERE id=?", (uid,))
    return int(S("limit_" + (u["plan"] if u else "free")) or 1)


def gate(uid, chat):
    if isadm(uid):
        return True
    u = q1("SELECT banned FROM users WHERE id=?", (uid,))
    if u and u["banned"]:
        send(chat, "🚫 መጠቀም ተከልክለዋል።")
        return False
    if S("maintenance") == "1":
        send(chat, "🔧 አገልግሎቱ ለጊዜው በጥገና ላይ ነው። በኋላ ይሞክሩ።")
        return False
    ch = S("force_channel").strip()
    if ch:
        try:
            if bot.get_chat_member(ch, uid).status in ("left", "kicked"):
                k = types.InlineKeyboardMarkup()
                k.add(types.InlineKeyboardButton("📢 ቻናሉን ተቀላቀል", url="https://t.me/" + ch.lstrip("@")))
                k.add(types.InlineKeyboardButton("✅ ተቀላቅያለሁ", callback_data="chk"))
                send(chat, "⚠️ bot ን ለመጠቀም መጀመሪያ ቻናላችንን ይቀላቀሉ።", k)
                return False
        except Exception:
            pass
    return True


# ───────────────────────── BOT PROCESS MANAGER ─────────────────────────
procs = {}
starts = {}
bdir = lambda bid: os.path.join(BOTS_DIR, str(bid))
getb = lambda bid: q1("SELECT * FROM bots WHERE id=?", (bid,))
is_alive = lambda bid: bid in procs and procs[bid].poll() is None

PIPMAP = {"telebot": "pyTelegramBotAPI", "telegram": "python-telegram-bot", "PIL": "pillow", "cv2": "opencv-python-headless",
          "yaml": "pyyaml", "bs4": "beautifulsoup4", "dotenv": "python-dotenv", "sklearn": "scikit-learn", "Crypto": "pycryptodome",
          "dateutil": "python-dateutil", "pyrogram": "pyrogram tgcrypto", "flask_cors": "flask-cors", "jwt": "pyjwt",
          "telethon": "telethon", "aiogram": "aiogram", "googletrans": "googletrans==4.0.0rc1", "serial": "pyserial"}
SKIP_PIP = {"telebot", "requests", "flask", "sqlite3"}
DANGER = ["/proc/", "/etc/passwd", "rm -rf /", "xmrig", "stratum+tcp", "cryptonight", "hostdata", "host.db", "fork bomb", ":(){"]


def find_main(root):
    best, score = None, -999
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "deps", "venv", ".venv", ".git", "__MACOSX", "node_modules")]
        for f in fn:
            if not f.endswith(".py") or f.startswith("._"):
                continue
            p = os.path.join(dp, f)
            try:
                s = open(p, encoding="utf-8", errors="ignore").read()
            except Exception:
                continue
            sc = 0
            if re.search(r"^\s*(import|from)\s+(telebot|telegram|aiogram|pyrogram|telethon)", s, re.M):
                sc += 10
            if re.search(r"infinity_polling|\.polling\(|run_polling|start_polling|run_until_disconnected|\.run\(", s):
                sc += 50
            if "__main__" in s:
                sc += 5
            if f.lower() in ("main.py", "bot.py", "app.py", "run.py", "start.py"):
                sc += 3
            sc += min(len(s) / 10000, 3)
            sc -= (dp.count(os.sep) - root.count(os.sep)) * 2
            if sc > score:
                best, score = p, sc
    return best


def detect_imports(root):
    local = set()
    for dp, dn, fn in os.walk(root):
        for d in dn:
            local.add(d)
        for f in fn:
            if f.endswith(".py"):
                local.add(f[:-3])
    std = getattr(sys, "stdlib_module_names", set())
    pk = set()
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in ("__pycache__", "deps", "venv", ".git", "__MACOSX")]
        for f in fn:
            if not f.endswith(".py"):
                continue
            try:
                tree = ast.parse(open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read())
            except Exception:
                continue
            for n in ast.walk(tree):
                names = []
                if isinstance(n, ast.Import):
                    names = [a.name.split(".")[0] for a in n.names]
                elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
                    names = [n.module.split(".")[0]]
                for m in names:
                    if m in std or m in local or m in SKIP_PIP or m == "__future__":
                        continue
                    pk.update(PIPMAP.get(m, m).split())
    return pk


def clean_env(extra=None, d="/tmp"):
    env = {"PATH": os.environ.get("PATH", ""), "HOME": d, "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "TZ": "Africa/Addis_Ababa"}
    if extra:
        env.update(extra)
    return env


def install_deps(bid, app, main):
    d = bdir(bid)
    target = os.path.join(d, "deps")
    os.makedirs(target, exist_ok=True)
    req = None
    for cand in (os.path.join(os.path.dirname(main), "requirements.txt"), os.path.join(app, "requirements.txt")):
        if os.path.exists(cand):
            req = cand
            break
    args = ["-r", req] if req else sorted(detect_imports(app))
    if not args:
        return True, ""
    cmd = [sys.executable, "-m", "pip", "install", "--no-input", "--disable-pip-version-check", "--target", target] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900, env=clean_env(d=d))
        return r.returncode == 0, (r.stderr or r.stdout)[-700:]
    except subprocess.TimeoutExpired:
        return False, "pip install ከ15 ደቂቃ በላይ ወሰደ"


def materialize(bid, upd=lambda t: None):
    row = q1("SELECT * FROM files WHERE bot_id=?", (bid,))
    if not row:
        return False, "ፋይል አልተገኘም"
    d = bdir(bid)
    app = os.path.join(d, "app")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(app, exist_ok=True)
    fname, data = row["fname"], row["data"]
    if fname.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                total = 0
                for i in z.infolist():
                    total += i.file_size
                    if total > 30 * 1024 * 1024:
                        return False, "ዚፕ ሲገለጥ ከ30MB በላይ ነው"
                    p = os.path.normpath(os.path.join(app, i.filename))
                    if not (p + os.sep).startswith(app + os.sep):
                        return False, "አደገኛ path በዚፕ ውስጥ ተገኝቷል"
                z.extractall(app)
        except zipfile.BadZipFile:
            return False, "ዚፕ ፋይሉ ተበላሽቷል"
    else:
        with open(os.path.join(app, os.path.basename(fname)), "wb") as f:
            f.write(data)
    main = find_main(app)
    if not main:
        return False, "ምንም .py ፋይል አልተገኘም"
    rel = os.path.relpath(main, app)
    upd(f"🔍 ዋናው ፋይል፦ <code>{esc(rel)}</code>\n⏳ syntax እየፈተሸሁ ነው...")
    try:
        compile(open(main, encoding="utf-8", errors="ignore").read(), main, "exec")
    except SyntaxError as e:
        return False, f"Syntax error — {rel} መስመር {e.lineno}: {e.msg}"
    if S("scan") == "1":
        for dp, dn, fn in os.walk(app):
            for f in fn:
                if f.endswith(".py"):
                    s = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read().lower()
                    for bad in DANGER:
                        if bad in s:
                            return False, f"የተከለከለ ይዘት ተገኝቷል ({f}) — ለደህንነት ሲባል ውድቅ ተደርጓል"
    upd(f"📦 libraries እየጫንሁ ነው... (ጥቂት ደቂቃ ሊወስድ ይችላል)\n📄 <code>{esc(rel)}</code>")
    ok, log = install_deps(bid, app, main)
    if not ok:
        return False, "Libraries መጫን አልተሳካም:\n" + log
    ex("UPDATE bots SET main=? WHERE id=?", (rel, bid))
    return True, rel


def launch(bid):
    b = getb(bid)
    if not b:
        return False, "bot አልተገኘም"
    if is_alive(bid):
        return True, "ቀድሞ እየሰራ ነው"
    running = sum(1 for k in procs if is_alive(k))
    if running >= int(S("max_total")) and not isadm(b["owner"]):
        return False, "አገልግሎቱ ለጊዜው ሞልቷል (server capacity)። በኋላ ይሞክሩ።"
    mine = [x["id"] for x in q("SELECT id FROM bots WHERE owner=? ORDER BY id", (b["owner"],))]
    if bid in mine and mine.index(bid) >= limit_of(b["owner"]):
        return False, "ከ plan ገደብዎ በላይ ነው — Premium ይግዙ።"
    d = bdir(bid)
    app = os.path.join(d, "app")
    main = os.path.join(app, b["main"])
    if not os.path.exists(main):
        ok, msg = materialize(bid)
        if not ok:
            return False, msg
        main = os.path.join(app, getb(bid)["main"])
    try:
        extra = {k: v for k, v in json.loads(b["env"] or "{}").items() if k not in ("PATH", "PYTHONPATH", "HOME")}
    except Exception:
        extra = {}
    env = clean_env(extra, d)
    env["PYTHONPATH"] = os.path.join(d, "deps")
    logp = os.path.join(d, "run.log")
    mode = "wb" if os.path.exists(logp) and os.path.getsize(logp) > 1_000_000 else "ab"
    log = open(logp, mode)
    mem = int(S("max_mem_mb")) * 1024 * 1024

    def lim():
        os.setsid()
        if resource:
            try:
                resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
                resource.setrlimit(resource.RLIMIT_FSIZE, (60 * 1024 * 1024, 60 * 1024 * 1024))
            except Exception:
                pass

    try:
        p = subprocess.Popen([sys.executable, "-u", main], cwd=os.path.dirname(main), env=env, stdout=log, stderr=log,
                             stdin=subprocess.DEVNULL, preexec_fn=lim)
    except Exception as e:
        return False, str(e)
    procs[bid] = p
    starts[bid] = now()
    ex("UPDATE bots SET status='running',last_start=? WHERE id=?", (now(), bid))
    return True, "ok"


def halt(bid, status="stopped"):
    p = procs.pop(bid, None)
    if p and p.poll() is None:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=5)
        except Exception:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except Exception:
                pass
    ex("UPDATE bots SET status=? WHERE id=?", (status, bid))


def tail(bid, n=3000):
    p = os.path.join(bdir(bid), "run.log")
    if not os.path.exists(p):
        return "(log የለም)"
    with open(p, "rb") as f:
        f.seek(0, 2)
        f.seek(max(0, f.tell() - n))
        return f.read().decode("utf-8", "ignore") or "(log ባዶ ነው)"


def monitor():
    last_hour = 0
    while True:
        time.sleep(10)
        try:
            for bid, p in list(procs.items()):
                if p.poll() is None:
                    continue
                procs.pop(bid, None)
                b = getb(bid)
                if not b or b["status"] != "running":
                    continue
                if now() - starts.get(bid, 0) > 600:
                    ex("UPDATE bots SET restarts=0 WHERE id=?", (bid,))
                    b = getb(bid)
                if b["autorestart"] and b["restarts"] < int(S("max_restarts")):
                    ex("UPDATE bots SET restarts=restarts+1 WHERE id=?", (bid,))
                    time.sleep(2)
                    launch(bid)
                else:
                    ex("UPDATE bots SET status='crashed' WHERE id=?", (bid,))
                    send(b["owner"], f"🔴 <b>{esc(b['name'])}</b> ቆሟል/ተበላሽቷል።\n\n<pre>{esc(tail(bid, 1200))}</pre>")
            if time.time() - last_hour > 3600:
                last_hour = time.time()
                for u in q("SELECT * FROM users WHERE plan!='free' AND until<?", (now(),)):
                    ex("UPDATE users SET plan='free' WHERE id=?", (u["id"],))
                    send(u["id"], "⌛ Premium ዎ ጊዜው አልፏል። ከገደብ በላይ ያሉ bots ይቆማሉ። /start")
                    for b in q("SELECT id FROM bots WHERE owner=? ORDER BY id", (u["id"],))[limit_of(u["id"]):]:
                        halt(b["id"])
        except Exception as e:
            print("monitor err", e)


# ───────────────────────── BACKUP / RESTORE ─────────────────────────
def do_restore(data):
    tmp = os.path.join(DATA, "restore.tmp")
    open(tmp, "wb").write(data)
    with _lock:
        db.commit()
        db.execute("ATTACH DATABASE ? AS old", (tmp,))
        for t in TABLES:
            try:
                db.execute(f"INSERT OR REPLACE INTO main.{t} SELECT * FROM old.{t}")
            except Exception as e:
                print("restore", t, e)
        db.commit()
        db.execute("DETACH DATABASE old")
    os.remove(tmp)


def make_backup():
    if not ADMINS:
        return
    tmp = os.path.join(DATA, "backup.db")
    with _lock:
        dst = sqlite3.connect(tmp)
        db.backup(dst)
        dst.close()
    with open(tmp, "rb") as f:
        m = bot.send_document(ADMINS[0], f, visible_file_name=f"hostbot_{datetime.now():%Y%m%d_%H%M}.db",
                              caption="💾 Auto backup — Pin የተደረገውን አይሰርዙት (ከ redeploy በኋላ ራሱ ይመልሳል)", disable_notification=True)
    try:
        bot.pin_chat_message(ADMINS[0], m.message_id, disable_notification=True)
    except Exception:
        pass


def auto_restore():
    try:
        if not ADMINS:
            return False
        pm = getattr(bot.get_chat(ADMINS[0]), "pinned_message", None)
        if pm and pm.document and (pm.document.file_name or "").endswith(".db"):
            data = bot.download_file(bot.get_file(pm.document.file_id).file_path)
            do_restore(data)
            print("✅ DB restored from pinned backup")
            return True
    except Exception as e:
        print("auto_restore err", e)
    return False


def backup_loop():
    last = 0
    while True:
        time.sleep(60)
        try:
            if dirty[0] and time.time() - last > 180:
                dirty[0] = False
                last = time.time()
                make_backup()
        except Exception as e:
            print("backup err", e)


def boot_all():
    for b in q("SELECT id FROM bots WHERE status='running' ORDER BY id"):
        try:
            launch(b["id"])
        except Exception as e:
            print("boot err", e)
        time.sleep(1.5)


# ───────────────────────── KEYBOARDS ─────────────────────────
B_NEW, B_MY, B_PREM, B_PROF, B_REF, B_HELP, B_ADMIN = "➕ አዲስ Bot አስተናግድ", "🤖 የእኔ Bots", "💎 Premium", "👤 መገለጫ", "🎁 ጋብዝ & ሽልማት", "ℹ️ እርዳታ", "🛠 Admin Panel"
MENU = [B_NEW, B_MY, B_PREM, B_PROF, B_REF, B_HELP, B_ADMIN]


def main_kb(uid):
    k = types.ReplyKeyboardMarkup(resize_keyboard=True)
    k.row(B_NEW, B_MY)
    k.row(B_PREM, B_PROF)
    k.row(B_REF, B_HELP)
    if isadm(uid):
        k.row(B_ADMIN)
    return k


IB = types.InlineKeyboardButton


def kb_bot(bid):
    b = getb(bid)
    alive = is_alive(bid)
    k = types.InlineKeyboardMarkup(row_width=2)
    k.add(IB("⏹ አቁም" if alive else "▶️ አስነሳ", callback_data=f"a:{'stop' if alive else 'start'}:{bid}"),
          IB("🔄 Restart", callback_data=f"a:restart:{bid}"))
    k.add(IB("📜 Logs", callback_data=f"a:log:{bid}"), IB("⚙️ Env Vars", callback_data=f"a:env:{bid}"))
    k.add(IB(f"🔁 Auto-restart: {'ON' if b['autorestart'] else 'OFF'}", callback_data=f"a:auto:{bid}"),
          IB("📥 ፋይል አውርድ", callback_data=f"a:dl:{bid}"))
    k.add(IB("🗑 ሰርዝ", callback_data=f"a:del:{bid}"), IB("🔙 ዝርዝር", callback_data="a:list:0"))
    return k


def card(bid):
    b = getb(bid)
    alive = is_alive(bid)
    st = "🟢 እየሰራ ነው" if alive else {"crashed": "🔴 ተበላሽቷል"}.get(b["status"], "⚪️ ቆሟል")
    up = ""
    if alive:
        s = now() - starts.get(bid, now())
        up = f"\n⏱ Uptime: {s // 3600}h {s % 3600 // 60}m"
    return (f"🤖 <b>{esc(b['name'])}</b>  <code>#{bid}</code>\n{st}{up}\n📄 ዋና ፋይል: <code>{esc(b['main'] or '-')}</code>\n"
            f"🔁 Restarts: {b['restarts']}\n📅 ተፈጥሯል: {fdate(b['created'])}")


def show_my(chat, uid, c=None):
    bs = q("SELECT * FROM bots WHERE owner=? ORDER BY id DESC", (uid,))
    if not bs:
        t, k = "📭 እስካሁን ምንም bot የለዎትም።\n.py ወይም .zip ፋይል ይላኩ።", None
    else:
        k = types.InlineKeyboardMarkup(row_width=1)
        for b in bs:
            ic = "🟢" if is_alive(b["id"]) else ("🔴" if b["status"] == "crashed" else "⚪️")
            k.add(IB(f"{ic} {b['name']} · #{b['id']}", callback_data=f"a:show:{b['id']}"))
        t = f"🤖 <b>የእርስዎ Bots</b> ({len(bs)}/{limit_of(uid) if not isadm(uid) else '∞'})"
    edit(c, t, k) if c else send(chat, t, k)


# ───────────────────────── USER HANDLERS ─────────────────────────
def cancelled(m):
    return (not m.text) or m.text.startswith("/") or m.text in MENU


@bot.message_handler(commands=["start"])
def h_start(m):
    ref = None
    parts = (m.text or "").split()
    if len(parts) > 1 and parts[1].startswith("ref"):
        try:
            ref = int(parts[1][3:])
        except ValueError:
            pass
    ensure_user(m.from_user, ref)
    if not gate(m.from_user.id, m.chat.id):
        return
    send(m.chat.id, S("welcome"), main_kb(m.from_user.id))


@bot.message_handler(commands=["id"])
def h_id(m):
    send(m.chat.id, f"🆔 <code>{m.from_user.id}</code>")


@bot.message_handler(commands=["admin"])
def h_admin_cmd(m):
    if isadm(m.from_user.id):
        admin_panel(m.chat.id)


@bot.callback_query_handler(func=lambda c: c.data == "chk")
def cb_chk(c):
    if gate(c.from_user.id, c.message.chat.id):
        bot.answer_callback_query(c.id, "✅ እሺ!")
        send(c.message.chat.id, S("welcome"), main_kb(c.from_user.id))


@bot.message_handler(func=lambda m: m.text == B_NEW)
def h_new(m):
    ensure_user(m.from_user)
    if gate(m.from_user.id, m.chat.id):
        send(m.chat.id, "📤 የ bot ዎን <b>.py</b> ወይም <b>.zip</b> ፋይል (ከ5MB በታች) ይላኩልኝ።\n\n"
                        "• ብዙ ፋይል ካለው .zip ያድርጉት — ዋናውን ራሴ እለያለሁ\n• requirements.txt ካለ እጠቀማለሁ፣ ከሌለ imports ተመልክቼ እጭናለሁ\n"
                        "• Token በ ፋይሉ ውስጥ ያስገቡ ወይም በ ⚙️ Env Vars ያቅርቡ")


@bot.message_handler(func=lambda m: m.text == B_MY)
def h_my(m):
    ensure_user(m.from_user)
    if gate(m.from_user.id, m.chat.id):
        show_my(m.chat.id, m.from_user.id)


@bot.message_handler(func=lambda m: m.text == B_PROF)
def h_prof(m):
    u = ensure_user(m.from_user)
    if not gate(m.from_user.id, m.chat.id):
        return
    n = q1("SELECT COUNT(*) c FROM bots WHERE owner=?", (m.from_user.id,))["c"]
    pl = "👑 Admin" if isadm(m.from_user.id) else u["plan"].upper()
    exp = f"\n⏳ ያበቃል: {fdate(u['until'])}" if u["plan"] != "free" and not isadm(m.from_user.id) else ""
    send(m.chat.id, f"👤 <b>መገለጫ</b>\n🆔 <code>{u['id']}</code>\n💎 Plan: <b>{pl}</b>{exp}\n🤖 Bots: {n}/{limit_of(u['id']) if not isadm(u['id']) else '∞'}\n👥 የጋበዟቸው: {u['refs']}")


@bot.message_handler(func=lambda m: m.text == B_REF)
def h_ref(m):
    u = ensure_user(m.from_user)
    if not gate(m.from_user.id, m.chat.id):
        return
    me = bot.get_me().username
    send(m.chat.id, f"🎁 <b>ጋብዝ እና Premium ቀን አግኝ</b>\n\nለእያንዳንዱ የጋበዙት ሰው <b>+{S('ref_days')} ቀን</b> Premium ያገኛሉ!\n\n"
                    f"🔗 <code>https://t.me/{me}?start=ref{u['id']}</code>\n\n👥 እስካሁን: {u['refs']}")


@bot.message_handler(func=lambda m: m.text == B_HELP)
def h_help(m):
    ensure_user(m.from_user)
    send(m.chat.id, "ℹ️ <b>እርዳታ</b>\n\n1️⃣ .py ወይም .zip ይላኩ\n2️⃣ ዋናውን ፋይል ራሴ ለይቼ libraries ጭኜ አስነሳለሁ\n3️⃣ 🤖 የእኔ Bots ውስጥ Start/Stop/Logs ያስተዳድሩ\n\n"
                    "⚠️ bot ዎ ሲበላሽ Logs ይመልከቱ።\n⚠️ webhook ሳይሆን polling ይጠቀሙ።\n\n"
                    f"📞 Support: {esc(S('support'))}")


# ── Deploy
@bot.message_handler(content_types=["document"], func=lambda m: m.chat.type == "private")
def h_doc(m):
    uid = m.from_user.id
    ensure_user(m.from_user)
    name = m.document.file_name or "file"
    if isadm(uid) and name.endswith(".db"):
        try:
            do_restore(bot.download_file(bot.get_file(m.document.file_id).file_path))
            send(m.chat.id, "✅ Database ተመልሷል። Bots እየተነሱ ነው...")
            threading.Thread(target=boot_all, daemon=True).start()
        except Exception as e:
            send(m.chat.id, f"❌ {esc(str(e))}")
        return
    if not gate(uid, m.chat.id):
        return
    if not name.lower().endswith((".py", ".zip")):
        return send(m.chat.id, "❌ .py ወይም .zip ብቻ ይላኩ።")
    if m.document.file_size > 5 * 1024 * 1024:
        return send(m.chat.id, "❌ ፋይሉ ከ5MB በላይ ነው።")
    n = q1("SELECT COUNT(*) c FROM bots WHERE owner=?", (uid,))["c"]
    if n >= limit_of(uid):
        return send(m.chat.id, f"⚠️ የ plan ገደብዎ ({limit_of(uid)} bot) ደርሷል።\n💎 ተጨማሪ ለማስተናገድ Premium ይግዙ ወይም አንድ bot ይሰርዙ።")
    msg = send(m.chat.id, "⏳ ፋይሉን እየተቀበልኩ ነው...")
    data = bot.download_file(bot.get_file(m.document.file_id).file_path)
    threading.Thread(target=deploy, args=(uid, name, data, msg), daemon=True).start()


def deploy(uid, fname, data, msg):
    def upd(t):
        try:
            bot.edit_message_text(t, msg.chat.id, msg.message_id)
        except Exception:
            pass

    bid = ex("INSERT INTO bots(owner,name,created) VALUES(?,?,?)", (uid, os.path.splitext(fname)[0][:40], now()))
    ex("INSERT INTO files VALUES(?,?,?)", (bid, fname, data))
    try:
        ok, res = materialize(bid, upd)
        if ok:
            ok, res = launch(bid)
            if ok:
                time.sleep(4)
                if not is_alive(bid):
                    ok, res = False, "ተነስቶ ወዲያው ቆመ — Logs:\n" + tail(bid, 1000)
                    ex("UPDATE bots SET status='crashed' WHERE id=?", (bid,))
                    upd(f"⚠️ <b>ተጭኗል ግን ወዲያው ቆመ</b>\n\n<pre>{esc(tail(bid, 1500))}</pre>")
                    return send(msg.chat.id, "ችግሩን አስተካክለው እንደገና ይላኩ ወይም Env Vars ይሙሉ።", kb_bot(bid))
        if not ok:
            halt(bid)
            ex("DELETE FROM bots WHERE id=?", (bid,))
            ex("DELETE FROM files WHERE bot_id=?", (bid,))
            shutil.rmtree(bdir(bid), ignore_errors=True)
            return upd(f"❌ <b>Deploy አልተሳካም</b>\n\n{esc(res)}")
        upd("✅ <b>Deploy ተሳክቷል!</b>")
        send(msg.chat.id, card(bid), kb_bot(bid))
        for a in ADMINS:
            if a != uid:
                send(a, f"🚀 አዲስ deploy: #{bid} · <code>{uid}</code> · {esc(fname)}")
    except Exception as e:
        upd(f"❌ ስህተት: {esc(str(e))}")


# ── Bot management callbacks
def pending_env(m, bid):
    if cancelled(m):
        return send(m.chat.id, "ተሰርዟል።")
    b = getb(bid)
    env = json.loads(b["env"] or "{}")
    for line in m.text.splitlines():
        line = line.strip()
        if line.startswith("-"):
            env.pop(line[1:].strip(), None)
        elif "=" in line:
            k, v = line.split("=", 1)
            k = k.strip()
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
                env[k] = v.strip()
    ex("UPDATE bots SET env=? WHERE id=?", (json.dumps(env), bid))
    try:
        bot.delete_message(m.chat.id, m.message_id)
    except Exception:
        pass
    send(m.chat.id, f"✅ Env ተቀምጧል ({len(env)} variables)። Restart ያድርጉ።", kb_bot(bid))


@bot.callback_query_handler(func=lambda c: c.data.startswith("a:"))
def cb_act(c):
    uid = c.from_user.id
    if not gate(uid, c.message.chat.id):
        return bot.answer_callback_query(c.id)
    _, act, arg = c.data.split(":")
    arg = int(arg)
    if act == "list":
        bot.answer_callback_query(c.id)
        return show_my(c.message.chat.id, uid, c)
    b = getb(arg)
    if not b or (b["owner"] != uid and not isadm(uid)):
        return bot.answer_callback_query(c.id, "❌ አልተገኘም", show_alert=True)
    bid = arg
    if act == "show":
        edit(c, card(bid), kb_bot(bid))
    elif act == "start":
        ok, msg = launch(bid)
        bot.answer_callback_query(c.id, "▶️ ተነስቷል" if ok else msg, show_alert=not ok)
        time.sleep(1.5)
        edit(c, card(bid), kb_bot(bid))
    elif act == "stop":
        halt(bid)
        bot.answer_callback_query(c.id, "⏹ ቆሟል")
        edit(c, card(bid), kb_bot(bid))
    elif act == "restart":
        halt(bid)
        ex("UPDATE bots SET restarts=0 WHERE id=?", (bid,))
        ok, msg = launch(bid)
        bot.answer_callback_query(c.id, "🔄 እንደገና ተነስቷል" if ok else msg, show_alert=not ok)
        time.sleep(1.5)
        edit(c, card(bid), kb_bot(bid))
    elif act == "log":
        bot.answer_callback_query(c.id)
        k = types.InlineKeyboardMarkup()
        k.add(IB("🔃 Refresh", callback_data=f"a:log:{bid}"), IB("🔙 ተመለስ", callback_data=f"a:show:{bid}"))
        edit(c, f"📜 <b>Logs #{bid}</b>\n<pre>{esc(tail(bid))}</pre>", k)
    elif act == "auto":
        ex("UPDATE bots SET autorestart=1-autorestart WHERE id=?", (bid,))
        bot.answer_callback_query(c.id, "✅")
        edit(c, card(bid), kb_bot(bid))
    elif act == "dl":
        bot.answer_callback_query(c.id)
        f = q1("SELECT * FROM files WHERE bot_id=?", (bid,))
        if f:
            bot.send_document(c.message.chat.id, io.BytesIO(f["data"]), visible_file_name=f["fname"])
    elif act == "env":
        bot.answer_callback_query(c.id)
        env = json.loads(b["env"] or "{}")
        keys = "\n".join(f"• <code>{esc(k)}</code>" for k in env) or "(ባዶ)"
        m = send(c.message.chat.id, f"⚙️ <b>Environment Variables</b>\n{keys}\n\nበ መስመር <code>KEY=VALUE</code> ይላኩ\nለማጥፋት <code>-KEY</code>\n"
                                    "(ምሳሌ: <code>BOT_TOKEN=123:ABC</code> — bot ዎ <code>os.environ['BOT_TOKEN']</code> ያንብብ)\nለመሰረዝ /cancel")
        bot.register_next_step_handler(m, pending_env, bid)
    elif act == "del":
        bot.answer_callback_query(c.id)
        k = types.InlineKeyboardMarkup()
        k.add(IB("✅ አዎ ሰርዝ", callback_data=f"a:delok:{bid}"), IB("❌ አይ", callback_data=f"a:show:{bid}"))
        edit(c, f"⚠️ <b>{esc(b['name'])}</b> ን በእርግጥ ይሰርዙ?", k)
    elif act == "delok":
        halt(bid)
        shutil.rmtree(bdir(bid), ignore_errors=True)
        ex("DELETE FROM bots WHERE id=?", (bid,))
        ex("DELETE FROM files WHERE bot_id=?", (bid,))
        bot.answer_callback_query(c.id, "🗑 ተሰርዟል")
        show_my(c.message.chat.id, uid, c)


# ── Premium / Payments
@bot.message_handler(func=lambda m: m.text == B_PREM)
def h_prem(m):
    ensure_user(m.from_user)
    if not gate(m.from_user.id, m.chat.id):
        return
    k = types.InlineKeyboardMarkup(row_width=1)
    k.add(IB(f"💎 Premium — {S('price_premium')} ({S('limit_premium')} bots)", callback_data="buy:premium"))
    k.add(IB(f"👑 VIP — {S('price_vip')} ({S('limit_vip')} bots)", callback_data="buy:vip"))
    send(m.chat.id, f"💎 <b>Plans</b>\n\n🆓 Free — {S('limit_free')} bot\n💎 Premium — {S('limit_premium')} bots · {esc(S('price_premium'))}\n"
                    f"👑 VIP — {S('limit_vip')} bots · {esc(S('price_vip'))}\n\nእያንዳንዱ plan {S('plan_days')} ቀን ነው።", k)


def pay_receipt(m, plan):
    if m.content_type == "photo":
        fid = "p:" + m.photo[-1].file_id
    elif m.content_type == "document":
        fid = "d:" + m.document.file_id
    else:
        return send(m.chat.id, "❌ screenshot (ፎቶ) መላክ ነበረበት። እንደገና 💎 Premium ይጫኑ።")
    pid = ex("INSERT INTO payments(user,plan,file_id,ts) VALUES(?,?,?,?)", (m.from_user.id, plan, fid, now()))
    send(m.chat.id, "✅ ደርሶናል! Admin ሲያረጋግጥ ወዲያውኑ ያሳውቅዎታል።")
    k = types.InlineKeyboardMarkup()
    k.add(IB("✅ አጽድቅ", callback_data=f"pay:ok:{pid}"), IB("❌ ውድቅ", callback_data=f"pay:no:{pid}"))
    cap = f"💳 ክፍያ #{pid}\n👤 <a href='tg://user?id={m.from_user.id}'>{esc(m.from_user.first_name or '')}</a> (<code>{m.from_user.id}</code>)\n📦 {plan.upper()}"
    for a in ADMINS:
        try:
            (bot.send_photo if fid[0] == "p" else bot.send_document)(a, fid[2:], caption=cap, reply_markup=k)
        except Exception as e:
            print(e)


@bot.callback_query_handler(func=lambda c: c.data.startswith("buy:"))
def cb_buy(c):
    plan = c.data.split(":")[1]
    bot.answer_callback_query(c.id)
    m = send(c.message.chat.id, f"💳 <b>{plan.upper()}</b> — {esc(S('price_' + plan))}\n\n{esc(S('pay_info'))}\n\n📸 ክፍያውን ከፈጸሙ በኋላ <b>screenshot</b> እዚህ ይላኩ።")
    bot.register_next_step_handler(m, pay_receipt, plan)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pay:") and isadm(c.from_user.id))
def cb_pay(c):
    _, act, pid = c.data.split(":")
    p = q1("SELECT * FROM payments WHERE id=?", (int(pid),))
    if not p or p["status"] != "pending":
        return bot.answer_callback_query(c.id, "ቀድሞ ተስተናግዷል", show_alert=True)
    if act == "ok":
        add_days(p["user"], int(S("plan_days")), p["plan"])
        ex("UPDATE payments SET status='ok' WHERE id=?", (p["id"],))
        send(p["user"], f"🎉 ክፍያዎ ጸድቋል! <b>{p['plan'].upper()}</b> ለ {S('plan_days')} ቀን ተከፍቷል።")
    else:
        ex("UPDATE payments SET status='no' WHERE id=?", (p["id"],))
        send(p["user"], f"❌ ክፍያዎ ውድቅ ተደርጓል። ጥያቄ ካለ {esc(S('support'))} ያግኙ።")
    bot.answer_callback_query(c.id, "✅ ተከናውኗል")
    try:
        bot.edit_message_reply_markup(c.message.chat.id, c.message.message_id)
        send(c.message.chat.id, f"ክፍያ #{pid}: {'✅ ጸድቋል' if act == 'ok' else '❌ ውድቅ'}")
    except Exception:
        pass


# ───────────────────────── ADMIN PANEL ─────────────────────────
def admin_panel(chat, c=None):
    k = types.InlineKeyboardMarkup(row_width=2)
    k.add(IB("📊 Stats", callback_data="ad:stats"), IB("📢 Broadcast", callback_data="ad:bc"))
    k.add(IB("👥 User ፈልግ", callback_data="ad:user"), IB("🤖 ሁሉም Bots", callback_data="ad:bots"))
    k.add(IB("💳 Pending ክፍያ", callback_data="ad:pay"), IB("⚙️ Settings", callback_data="ad:set"))
    k.add(IB(f"🔧 Maintenance: {'ON' if S('maintenance') == '1' else 'OFF'}", callback_data="ad:maint"), IB("💾 Backup", callback_data="ad:backup"))
    k.add(IB("⛔️ ሁሉንም Bots አቁም", callback_data="ad:stopall"), IB("▶️ ሁሉንም አስነሳ", callback_data="ad:startall"))
    t = "🛠 <b>Admin Panel</b>"
    edit(c, t, k) if c else send(chat, t, k)


@bot.message_handler(func=lambda m: m.text == B_ADMIN and isadm(m.from_user.id))
def h_admin(m):
    admin_panel(m.chat.id)


def ucard(uid):
    u = q1("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        return None, None
    n = q1("SELECT COUNT(*) c FROM bots WHERE owner=?", (uid,))["c"]
    t = (f"👤 <a href='tg://user?id={uid}'>{esc(u['name'])}</a> @{esc(u['username'] or '-')}\n🆔 <code>{uid}</code>\n💎 {u['plan'].upper()}"
         f"{' → ' + fdate(u['until']) if u['plan'] != 'free' else ''}\n🤖 Bots: {n}\n👥 Refs: {u['refs']}\n🚫 Banned: {'አዎ' if u['banned'] else 'አይ'}\n📅 {fdate(u['joined'])}")
    k = types.InlineKeyboardMarkup(row_width=3)
    k.add(IB("✅ Unban" if u["banned"] else "🚫 Ban", callback_data=f"u:{'unban' if u['banned'] else 'ban'}:{uid}"), IB("✉️ መልእክት", callback_data=f"u:msg:{uid}"))
    k.add(IB("💎 +7d", callback_data=f"u:p7:{uid}"), IB("💎 +30d", callback_data=f"u:p30:{uid}"), IB("👑 VIP +30d", callback_data=f"u:v30:{uid}"))
    k.add(IB("🆓 ወደ Free", callback_data=f"u:free:{uid}"), IB("🤖 Bots", callback_data=f"u:bots:{uid}"))
    return t, k


def do_bc(m):
    if cancelled(m) and not (m.content_type != "text"):
        return send(m.chat.id, "ተሰርዟል።")
    users = [r["id"] for r in q("SELECT id FROM users WHERE banned=0")]
    send(m.chat.id, f"📢 ወደ {len(users)} users እየተላከ ነው...")

    def run():
        ok = bad = 0
        for uid in users:
            try:
                bot.copy_message(uid, m.chat.id, m.message_id)
                ok += 1
            except Exception:
                bad += 1
            time.sleep(0.05)
        send(m.chat.id, f"✅ Broadcast ተጠናቋል\n✔️ {ok}\n✖️ {bad}")

    threading.Thread(target=run, daemon=True).start()


def do_find(m):
    if cancelled(m):
        return send(m.chat.id, "ተሰርዟል።")
    key = m.text.strip().lstrip("@")
    r = q1("SELECT id FROM users WHERE id=? OR username=?", (int(key) if key.isdigit() else -1, key))
    if not r:
        return send(m.chat.id, "❌ አልተገኘም")
    t, k = ucard(r["id"])
    send(m.chat.id, t, k)


def do_set(m, key):
    if cancelled(m):
        return send(m.chat.id, "ተሰርዟል።")
    v = "" if m.text.strip() == "-" else m.text.strip()
    setS(key, v)
    send(m.chat.id, f"✅ <code>{key}</code> ተቀይሯል።")


def do_umsg(m, uid):
    if cancelled(m):
        return send(m.chat.id, "ተሰርዟል።")
    send(uid, f"📩 <b>ከ Admin:</b>\n\n{esc(m.text)}")
    send(m.chat.id, "✅ ተልኳል")


@bot.callback_query_handler(func=lambda c: c.data.startswith("ad:") and isadm(c.from_user.id))
def cb_admin(c):
    act = c.data.split(":")[1]
    cid = c.message.chat.id
    bot.answer_callback_query(c.id)
    if act == "home":
        admin_panel(cid, c)
    elif act == "stats":
        tu = q1("SELECT COUNT(*) c FROM users")["c"]
        today = q1("SELECT COUNT(*) c FROM users WHERE joined>?", (now() - 86400,))["c"]
        pr = q1("SELECT COUNT(*) c FROM users WHERE plan!='free'")["c"]
        tb = q1("SELECT COUNT(*) c FROM bots")["c"]
        run = sum(1 for k in procs if is_alive(k))
        rev = q1("SELECT COUNT(*) c FROM payments WHERE status='ok'")["c"]
        pend = q1("SELECT COUNT(*) c FROM payments WHERE status='pending'")["c"]
        k = types.InlineKeyboardMarkup()
        k.add(IB("🔙", callback_data="ad:home"))
        edit(c, f"📊 <b>Stats</b>\n\n👥 Users: {tu} (ዛሬ +{today})\n💎 Paid: {pr}\n🤖 Bots: {tb} (🟢 {run} እየሰራ)\n💰 የጸደቁ ክፍያዎች: {rev}\n⏳ Pending: {pend}\n"
                f"🕒 {datetime.now():%Y-%m-%d %H:%M}", k)
    elif act == "bc":
        m = send(cid, "📢 የሚላከውን መልእክት ይላኩ (text/ፎቶ/ቪዲዮ ማንኛውም)። ለመሰረዝ /cancel")
        bot.register_next_step_handler(m, do_bc)
    elif act == "user":
        m = send(cid, "👥 User ID ወይም @username ይላኩ")
        bot.register_next_step_handler(m, do_find)
    elif act == "bots":
        rows = q("SELECT b.*,u.name un FROM bots b LEFT JOIN users u ON u.id=b.owner ORDER BY b.id DESC LIMIT 40")
        k = types.InlineKeyboardMarkup(row_width=1)
        for b in rows:
            ic = "🟢" if is_alive(b["id"]) else ("🔴" if b["status"] == "crashed" else "⚪️")
            k.add(IB(f"{ic} #{b['id']} {b['name']} · {b['un'] or b['owner']}", callback_data=f"a:show:{b['id']}"))
        k.add(IB("🔙", callback_data="ad:home"))
        edit(c, "🤖 <b>ሁሉም Bots</b> (የመጨረሻ 40)", k)
    elif act == "pay":
        rows = q("SELECT * FROM payments WHERE status='pending' ORDER BY id")
        if not rows:
            return send(cid, "✅ pending ክፍያ የለም")
        for p in rows:
            k = types.InlineKeyboardMarkup()
            k.add(IB("✅ አጽድቅ", callback_data=f"pay:ok:{p['id']}"), IB("❌ ውድቅ", callback_data=f"pay:no:{p['id']}"))
            try:
                (bot.send_photo if p["file_id"][0] == "p" else bot.send_document)(cid, p["file_id"][2:], caption=f"💳 #{p['id']} · <code>{p['user']}</code> · {p['plan'].upper()}", reply_markup=k)
            except Exception:
                send(cid, f"💳 #{p['id']} · {p['user']} · {p['plan']}", k)
    elif act == "set":
        k = types.InlineKeyboardMarkup(row_width=2)
        txt = "⚙️ <b>Settings</b> (ለመቀየር ይጫኑ፤ ባዶ ለማድረግ <code>-</code> ይላኩ)\n\n"
        for key in DEFAULTS:
            v = S(key).replace("\n", " ")
            txt += f"• <code>{key}</code> = {esc(v[:45])}\n"
            k.add(IB(key, callback_data=f"st:{key}"))
        k.add(IB("🔙", callback_data="ad:home"))
        edit(c, txt, k)
    elif act == "maint":
        setS("maintenance", "0" if S("maintenance") == "1" else "1")
        admin_panel(cid, c)
    elif act == "backup":
        try:
            make_backup()
        except Exception as e:
            send(cid, f"❌ {esc(str(e))}")
    elif act == "stopall":
        for bid in list(procs):
            halt(bid, "stopped")
        send(cid, "⛔️ ሁሉም bots ቆመዋል")
    elif act == "startall":
        ids = [r["id"] for r in q("SELECT id FROM bots WHERE status!='running' OR 1")]
        threading.Thread(target=lambda: [launch(i) or time.sleep(1.5) for i in ids], daemon=True).start()
        send(cid, "▶️ ሁሉም እየተነሱ ነው...")


@bot.callback_query_handler(func=lambda c: c.data.startswith("st:") and isadm(c.from_user.id))
def cb_st(c):
    key = c.data[3:]
    bot.answer_callback_query(c.id)
    m = send(c.message.chat.id, f"✏️ <code>{key}</code>\nአሁን: {esc(S(key))}\n\nአዲስ ዋጋ ይላኩ (ባዶ ለማድረግ <code>-</code>)")
    bot.register_next_step_handler(m, do_set, key)


@bot.callback_query_handler(func=lambda c: c.data.startswith("u:") and isadm(c.from_user.id))
def cb_user(c):
    _, act, uid = c.data.split(":")
    uid = int(uid)
    cid = c.message.chat.id
    bot.answer_callback_query(c.id, "✅")
    if act == "ban":
        ex("UPDATE users SET banned=1 WHERE id=?", (uid,))
        for b in q("SELECT id FROM bots WHERE owner=?", (uid,)):
            halt(b["id"])
    elif act == "unban":
        ex("UPDATE users SET banned=0 WHERE id=?", (uid,))
    elif act == "p7":
        add_days(uid, 7, "premium")
    elif act == "p30":
        add_days(uid, 30, "premium")
    elif act == "v30":
        add_days(uid, 30, "vip")
    elif act == "free":
        ex("UPDATE users SET plan='free',until=0 WHERE id=?", (uid,))
        for b in q("SELECT id FROM bots WHERE owner=? ORDER BY id", (uid,))[limit_of(uid):]:
            halt(b["id"])
    elif act == "msg":
        m = send(cid, "✉️ መልእክቱን ይጻፉ")
        return bot.register_next_step_handler(m, do_umsg, uid)
    elif act == "bots":
        k = types.InlineKeyboardMarkup(row_width=1)
        for b in q("SELECT * FROM bots WHERE owner=?", (uid,)):
            k.add(IB(f"{'🟢' if is_alive(b['id']) else '⚪️'} #{b['id']} {b['name']}", callback_data=f"a:show:{b['id']}"))
        return send(cid, "🤖 የ user bots", k)
    t, k = ucard(uid)
    edit(c, t, k)


# ───────────────────────── WEB SERVER (Render) ─────────────────────────
app = Flask(__name__)


@app.route("/")
def home():
    return f"HostBot Pro ✅ running bots: {sum(1 for k in procs if is_alive(k))}"


@app.route("/health")
def health():
    return "ok"


def keepalive():
    while True:
        time.sleep(540)
        if SELF_URL:
            try:
                urllib.request.urlopen(SELF_URL + "/health", timeout=20)
            except Exception:
                pass


# ───────────────────────── MAIN ─────────────────────────
if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT, use_reloader=False), daemon=True).start()
    try:
        bot.remove_webhook()
    except Exception:
        pass
    if q1("SELECT COUNT(*) c FROM users")["c"] == 0:
        auto_restore()
    threading.Thread(target=boot_all, daemon=True).start()
    threading.Thread(target=monitor, daemon=True).start()
    threading.Thread(target=backup_loop, daemon=True).start()
    threading.Thread(target=keepalive, daemon=True).start()
    print("🚀 HostBot Pro started")
    for a in ADMINS:
        send(a, "🚀 HostBot Pro ተነስቷል!")
    bot.infinity_polling(skip_pending=True, timeout=30, long_polling_timeout=30)

