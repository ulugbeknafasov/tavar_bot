import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).resolve().parent / "products.db"


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            order_contact TEXT DEFAULT '',
            joined_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            price INTEGER,
            photo_file_id TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            raw_caption TEXT DEFAULT NULL
        );

        """)
        # Existing databases get the optional original-caption column without losing products.
        if "raw_caption" not in _columns(conn, "products"):
            conn.execute("ALTER TABLE products ADD COLUMN raw_caption TEXT DEFAULT NULL")
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id TEXT NOT NULL,
            channel_title TEXT DEFAULT '',
            channel_username TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, channel_id)
        );

        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            price INTEGER NOT NULL DEFAULT 0,
            duration_days INTEGER NOT NULL DEFAULT 30,
            channel_limit INTEGER NOT NULL DEFAULT 3,
            daily_post_limit INTEGER NOT NULL DEFAULT 10,
            is_active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            plan_name TEXT NOT NULL DEFAULT '',
            price_paid INTEGER NOT NULL DEFAULT 0,
            starts_at TEXT NOT NULL,
            ends_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active'
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            plan_id INTEGER NOT NULL,
            plan_name TEXT NOT NULL,
            amount INTEGER NOT NULL,
            receipt_file_id TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS post_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            channel_id TEXT,
            product_id INTEGER,
            status TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT DEFAULT '',
            message TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            admin_reply TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            replied_at TEXT
        );
        """)

        # Safe, additive migration for older database versions.
        migrations = {
            "users": {
                "order_contact": "TEXT DEFAULT ''",
            },
            "channels": {
                "channel_username": "TEXT DEFAULT ''",
            },
            "subscriptions": {
                "plan_name": "TEXT NOT NULL DEFAULT ''",
                "price_paid": "INTEGER NOT NULL DEFAULT 0",
            },
            "payments": {
                "admin_note": "TEXT DEFAULT ''",
                "reviewed_at": "TEXT",
            },
            "support_messages": {
                "admin_reply": "TEXT DEFAULT ''",
                "replied_at": "TEXT",
            },
        }
        for table, cols in migrations.items():
            existing = _columns(conn, table)
            for col, definition in cols.items():
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")

        plan_count = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
        if plan_count == 0:
            conn.executemany(
                """INSERT INTO plans
                (name, price, duration_days, channel_limit, daily_post_limit)
                VALUES (?, ?, ?, ?, ?)""",
                [
                    ("Start", 39000, 30, 3, 10),
                    ("Pro", 79000, 30, 15, 50),
                    ("Business", 149000, 30, 50, 200),
                ],
            )

        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('payment_card', '')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('payment_owner', '')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('payment_instructions', 'To''lovdan keyin chekni yuboring.')"
        )


def add_user(user):
    with connect() as conn:
        conn.execute(
            """INSERT INTO users(telegram_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username=excluded.username, first_name=excluded.first_name""",
            (user.id, user.username or "", user.first_name or ""),
        )


def get_user(user_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def set_order_contact(user_id, contact):
    with connect() as conn:
        conn.execute("UPDATE users SET order_contact=? WHERE telegram_id=?", (contact, user_id))


def get_setting(key, default=""):
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with connect() as conn:
        conn.execute(
            """INSERT INTO settings(key, value) VALUES(?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )


def list_plans(active_only=True):
    with connect() as conn:
        if active_only:
            rows = conn.execute("SELECT * FROM plans WHERE is_active=1 ORDER BY price").fetchall()
        else:
            rows = conn.execute("SELECT * FROM plans ORDER BY id").fetchall()
        return [dict(r) for r in rows]


def get_plan(plan_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        return dict(row) if row else None


def create_plan(name, price, days, channel_limit, daily_post_limit):
    with connect() as conn:
        conn.execute(
            """INSERT INTO plans(name, price, duration_days, channel_limit, daily_post_limit, is_active)
            VALUES(?, ?, ?, ?, ?, 1)
            ON CONFLICT(name) DO UPDATE SET price=excluded.price,
                duration_days=excluded.duration_days,
                channel_limit=excluded.channel_limit,
                daily_post_limit=excluded.daily_post_limit,
                is_active=1""",
            (name, price, days, channel_limit, daily_post_limit),
        )


def set_plan_active(plan_id, active):
    with connect() as conn:
        conn.execute("UPDATE plans SET is_active=? WHERE id=?", (1 if active else 0, plan_id))


def save_product(user_id, name, description, price, photo_file_id, raw_caption=None):
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO products(user_id, name, description, price, photo_file_id, raw_caption)
            VALUES(?, ?, ?, ?, ?, ?)""",
            (user_id, name, description, price, photo_file_id, raw_caption),
        )
        return cur.lastrowid


def get_product(product_id, user_id=None):
    with connect() as conn:
        if user_id is None:
            row = conn.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM products WHERE id=? AND user_id=?", (product_id, user_id)
            ).fetchone()
        return dict(row) if row else None


def add_channel(user_id, channel_id, title, username=""):
    with connect() as conn:
        conn.execute(
            """INSERT INTO channels(user_id, channel_id, channel_title, channel_username)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(user_id, channel_id) DO UPDATE SET
                channel_title=excluded.channel_title,
                channel_username=excluded.channel_username""",
            (user_id, str(channel_id), title or "", username or ""),
        )


def list_channels(user_id):
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM channels WHERE user_id=? ORDER BY id DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def delete_channel(user_id, channel_row_id):
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM channels WHERE id=? AND user_id=?", (channel_row_id, user_id)
        )
        return cur.rowcount > 0


def log_post(user_id, channel_id, product_id, status):
    with connect() as conn:
        conn.execute(
            "INSERT INTO post_logs(user_id, channel_id, product_id, status) VALUES(?,?,?,?)",
            (user_id, str(channel_id), product_id, status),
        )


def create_payment(user_id, plan, receipt_file_id):
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO payments(user_id, plan_id, plan_name, amount, receipt_file_id)
            VALUES(?,?,?,?,?)""",
            (user_id, plan["id"], plan["name"], plan["price"], receipt_file_id),
        )
        return cur.lastrowid


def get_payment(payment_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM payments WHERE id=?", (payment_id,)).fetchone()
        return dict(row) if row else None


def review_payment(payment_id, approve, note=""):
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM payments WHERE id=? AND status='pending'", (payment_id,)
        ).fetchone()
        if not row:
            return None
        status = "approved" if approve else "rejected"
        conn.execute(
            "UPDATE payments SET status=?, admin_note=?, reviewed_at=CURRENT_TIMESTAMP WHERE id=?",
            (status, note, payment_id),
        )
        if approve:
            now = datetime.now()
            current = conn.execute(
                """SELECT ends_at FROM subscriptions
                WHERE user_id=? AND status='active'
                ORDER BY datetime(ends_at) DESC LIMIT 1""",
                (row["user_id"],),
            ).fetchone()
            start = now
            if current:
                try:
                    old_end = datetime.fromisoformat(current["ends_at"])
                    if old_end > now:
                        start = old_end
                except (TypeError, ValueError):
                    pass
            plan = conn.execute("SELECT duration_days FROM plans WHERE id=?", (row["plan_id"],)).fetchone()
            duration = plan["duration_days"] if plan else 30
            end = start + timedelta(days=duration)
            conn.execute(
                """INSERT INTO subscriptions
                (user_id, plan_id, plan_name, price_paid, starts_at, ends_at, status)
                VALUES(?,?,?,?,?,?, 'active')""",
                (
                    row["user_id"], row["plan_id"], row["plan_name"], row["amount"],
                    start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"),
                ),
            )
        return dict(row)


def active_subscription(user_id):
    with connect() as conn:
        row = conn.execute(
            """SELECT * FROM subscriptions
            WHERE user_id=? AND status='active' AND datetime(ends_at)>datetime('now')
            ORDER BY datetime(ends_at) DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def stats():
    with connect() as conn:
        return {
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "products": conn.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "channels": conn.execute("SELECT COUNT(*) FROM channels").fetchone()[0],
            "posts": conn.execute("SELECT COUNT(*) FROM post_logs WHERE status='sent'").fetchone()[0],
            "active_subscriptions": conn.execute(
                """SELECT COUNT(*) FROM subscriptions
                WHERE status='active' AND datetime(ends_at)>datetime('now')"""
            ).fetchone()[0],
            "pending_payments": conn.execute(
                "SELECT COUNT(*) FROM payments WHERE status='pending'"
            ).fetchone()[0],
            "revenue": conn.execute(
                "SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='approved'"
            ).fetchone()[0],
            "open_support": conn.execute(
                "SELECT COUNT(*) FROM support_messages WHERE status='open'"
            ).fetchone()[0],
        }


def create_support_message(user_id, username, message):
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO support_messages(user_id, username, message) VALUES(?,?,?)",
            (user_id, username or "", message),
        )
        return cur.lastrowid


def get_support_message(message_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM support_messages WHERE id=?", (message_id,)).fetchone()
        return dict(row) if row else None


def reply_support(message_id, reply):
    with connect() as conn:
        cur = conn.execute(
            """UPDATE support_messages SET admin_reply=?, status='answered',
            replied_at=CURRENT_TIMESTAMP WHERE id=? AND status='open'""",
            (reply, message_id),
        )
        return cur.rowcount > 0


def posting_access(user_id, owner_id=None):
    """Return (allowed, reason, trial_days_left). Bot owner has unlimited access; others get 7-day trial or paid Pro/Business."""
    if owner_id is not None and int(user_id) == int(owner_id):
        return True, "owner", 0
    user = get_user(user_id)
    if not user:
        return False, "trial_expired", 0
    sub = active_subscription(user_id)
    if sub and (sub.get("plan_name", "").strip().lower() in {"pro", "business"}):
        return True, "paid", 0
    try:
        joined = datetime.fromisoformat(str(user.get("joined_at", "")).replace(" ", "T"))
    except (TypeError, ValueError):
        joined = datetime.now()
    now = datetime.now()
    elapsed = now - joined
    remaining_seconds = 7 * 86400 - int(elapsed.total_seconds())
    if remaining_seconds > 0:
        days_left = (remaining_seconds + 86399) // 86400
        return True, "trial", days_left
    return False, "trial_expired", 0
