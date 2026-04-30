"""
database.py ─ SQLite 기반 서버 백업/복구 DB 관리
모든 백업 데이터는 data/backup.db 에 저장됩니다.
"""

import sqlite3
import json
import logging
import os
from datetime import datetime
from config import DB_PATH

log = logging.getLogger(__name__)

# data/ 폴더 자동 생성
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """테이블 초기화 (봇 시작 시 1회 호출)"""
    with get_conn() as conn:
        conn.executescript("""
        -- 백업 메타 정보
        CREATE TABLE IF NOT EXISTS backups (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id    TEXT    NOT NULL,
            guild_name  TEXT    NOT NULL,
            created_at  TEXT    NOT NULL,
            label       TEXT    DEFAULT ''
        );

        -- 카테고리
        CREATE TABLE IF NOT EXISTS categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id   INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
            cat_id      TEXT,
            name        TEXT    NOT NULL,
            position    INTEGER DEFAULT 0,
            overwrites  TEXT    DEFAULT '{}'
        );

        -- 채널
        CREATE TABLE IF NOT EXISTS channels (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id   INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
            channel_id  TEXT,
            name        TEXT    NOT NULL,
            type        TEXT    NOT NULL,
            position    INTEGER DEFAULT 0,
            topic       TEXT    DEFAULT '',
            nsfw        INTEGER DEFAULT 0,
            slowmode    INTEGER DEFAULT 0,
            bitrate     INTEGER DEFAULT 64000,
            user_limit  INTEGER DEFAULT 0,
            category_id TEXT    DEFAULT NULL,
            overwrites  TEXT    DEFAULT '{}'
        );

        -- 역할
        CREATE TABLE IF NOT EXISTS roles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id   INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
            role_id     TEXT,
            name        TEXT    NOT NULL,
            color       INTEGER DEFAULT 0,
            hoist       INTEGER DEFAULT 0,
            mentionable INTEGER DEFAULT 0,
            position    INTEGER DEFAULT 0,
            permissions INTEGER DEFAULT 0
        );

        -- 멤버-역할 매핑
        CREATE TABLE IF NOT EXISTS member_roles (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            backup_id   INTEGER NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
            member_id   TEXT    NOT NULL,
            member_name TEXT    NOT NULL,
            role_ids    TEXT    DEFAULT '[]'
        );

        -- OAuth2 토큰 저장 (멤버 자동 참가용)
        CREATE TABLE IF NOT EXISTS oauth_tokens (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       TEXT    NOT NULL UNIQUE,
            username      TEXT    NOT NULL,
            access_token  TEXT    NOT NULL,
            refresh_token TEXT    DEFAULT '',
            expires_in    INTEGER DEFAULT 604800,
            guild_id      TEXT    DEFAULT '',
            saved_at      TEXT    NOT NULL
        );

        -- 자판기: 상품 목록
        CREATE TABLE IF NOT EXISTS products (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id  TEXT    NOT NULL,
            name      TEXT    NOT NULL,
            price     INTEGER NOT NULL,
            role_id   TEXT    NOT NULL,
            created_at TEXT   NOT NULL
        );

        -- 자판기: 잔액
        CREATE TABLE IF NOT EXISTS balances (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   TEXT    NOT NULL,
            guild_id  TEXT    NOT NULL,
            balance   INTEGER DEFAULT 0,
            UNIQUE(user_id, guild_id)
        );

        -- 자판기: 충전 신청
        CREATE TABLE IF NOT EXISTS charge_requests (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            username   TEXT    NOT NULL,
            guild_id   TEXT    NOT NULL,
            pin        TEXT    NOT NULL,
            amount     INTEGER DEFAULT 0,
            status     TEXT    DEFAULT 'pending',
            created_at TEXT    NOT NULL
        );

        -- 자판기: 구매 내역
        CREATE TABLE IF NOT EXISTS purchases (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    TEXT    NOT NULL,
            username   TEXT    NOT NULL,
            guild_id   TEXT    NOT NULL,
            product_id INTEGER NOT NULL,
            price      INTEGER NOT NULL,
            created_at TEXT    NOT NULL
        );

        -- 인바이트 로그
        CREATE TABLE IF NOT EXISTS invite_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id     TEXT NOT NULL,
            inviter_id   TEXT NOT NULL,
            inviter_name TEXT NOT NULL,
            invitee_id   TEXT NOT NULL,
            invitee_name TEXT NOT NULL,
            invite_code  TEXT NOT NULL,
            created_at   TEXT NOT NULL
        );

        PRAGMA foreign_keys = ON;
        """)
    log.info("DB 초기화 완료 → %s", DB_PATH)


# ══════════════════════════════════════════════════
#  저장 (SAVE)
# ══════════════════════════════════════════════════

def save_backup(guild, label: str = "") -> int:
    """
    guild 객체를 받아 채널·역할·멤버 정보를 DB에 저장하고
    생성된 backup_id 를 반환합니다.
    """
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO backups (guild_id, guild_name, created_at, label) VALUES (?,?,?,?)",
            (str(guild.id), guild.name, now, label)
        )
        backup_id = cur.lastrowid

        # ── 역할 저장 ──────────────────────────────
        for role in guild.roles:
            if role.is_default():   # @everyone 은 삭제 불가라 위치만 저장
                continue
            conn.execute(
                """INSERT INTO roles
                   (backup_id, role_id, name, color, hoist, mentionable, position, permissions)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (backup_id, str(role.id), role.name,
                 role.color.value, int(role.hoist), int(role.mentionable),
                 role.position, role.permissions.value)
            )

        # ── 카테고리 저장 ──────────────────────────
        for cat in guild.categories:
            ow = _serialize_overwrites(cat.overwrites)
            conn.execute(
                """INSERT INTO categories
                   (backup_id, cat_id, name, position, overwrites)
                   VALUES (?,?,?,?,?)""",
                (backup_id, str(cat.id), cat.name, cat.position, ow)
            )

        # ── 채널 저장 ──────────────────────────────
        for ch in guild.channels:
            import discord
            if isinstance(ch, discord.CategoryChannel):
                continue   # 카테고리는 위에서 저장

            ow      = _serialize_overwrites(ch.overwrites)
            cat_id  = str(ch.category_id) if ch.category_id else None
            topic   = getattr(ch, "topic", "") or ""
            nsfw    = int(getattr(ch, "nsfw", False))
            slow    = getattr(ch, "slowmode_delay", 0)
            bitrate = getattr(ch, "bitrate", 64000)
            ulimit  = getattr(ch, "user_limit", 0)

            conn.execute(
                """INSERT INTO channels
                   (backup_id, channel_id, name, type, position, topic,
                    nsfw, slowmode, bitrate, user_limit, category_id, overwrites)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (backup_id, str(ch.id), ch.name, str(ch.type),
                 ch.position, topic, nsfw, slow, bitrate, ulimit, cat_id, ow)
            )

        # ── 멤버-역할 매핑 저장 ────────────────────
        for member in guild.members:
            role_ids = json.dumps([str(r.id) for r in member.roles if not r.is_default()])
            conn.execute(
                """INSERT INTO member_roles
                   (backup_id, member_id, member_name, role_ids)
                   VALUES (?,?,?,?)""",
                (backup_id, str(member.id), str(member), role_ids)
            )

        conn.commit()

    log.info("[백업] guild=%s  backup_id=%s  label=%s", guild.name, backup_id, label or "없음")
    return backup_id


# ══════════════════════════════════════════════════
#  불러오기 (LOAD)
# ══════════════════════════════════════════════════

def load_backup(backup_id: int) -> dict:
    """
    backup_id 로 저장된 전체 백업 데이터를 dict 로 반환합니다.
    반환 형식:
    {
      "meta":         { id, guild_id, guild_name, created_at, label },
      "roles":        [ {...}, ... ],
      "categories":   [ {...}, ... ],
      "channels":     [ {...}, ... ],
      "member_roles": [ {...}, ... ]
    }
    """
    with get_conn() as conn:
        meta = conn.execute(
            "SELECT * FROM backups WHERE id=?", (backup_id,)
        ).fetchone()
        if not meta:
            raise ValueError(f"backup_id={backup_id} 를 찾을 수 없습니다.")

        roles   = conn.execute("SELECT * FROM roles   WHERE backup_id=? ORDER BY position", (backup_id,)).fetchall()
        cats    = conn.execute("SELECT * FROM categories WHERE backup_id=? ORDER BY position", (backup_id,)).fetchall()
        chans   = conn.execute("SELECT * FROM channels WHERE backup_id=? ORDER BY position", (backup_id,)).fetchall()
        members = conn.execute("SELECT * FROM member_roles WHERE backup_id=?", (backup_id,)).fetchall()

    return {
        "meta":         dict(meta),
        "roles":        [dict(r) for r in roles],
        "categories":   [dict(c) for c in cats],
        "channels":     [dict(c) for c in chans],
        "member_roles": [dict(m) for m in members],
    }


def load_latest_backup(guild_id: str) -> dict | None:
    """특정 서버의 가장 최근 백업을 반환 (없으면 None)"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM backups WHERE guild_id=? ORDER BY id DESC LIMIT 1",
            (guild_id,)
        ).fetchone()
    if not row:
        return None
    return load_backup(row["id"])


def list_backups(guild_id: str) -> list[dict]:
    """특정 서버의 백업 목록을 최신순으로 반환"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, guild_name, created_at, label FROM backups WHERE guild_id=? ORDER BY id DESC",
            (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ══════════════════════════════════════════════════
#  OAuth2 토큰 저장/불러오기
# ══════════════════════════════════════════════════

def save_token(user_id: str, username: str, access_token: str,
               refresh_token: str, expires_in: int, guild_id: str):
    """OAuth2 access_token DB 저장 (있으면 업데이트)"""
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO oauth_tokens
                (user_id, username, access_token, refresh_token, expires_in, guild_id, saved_at)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(user_id) DO UPDATE SET
                username      = excluded.username,
                access_token  = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_in    = excluded.expires_in,
                guild_id      = excluded.guild_id,
                saved_at      = excluded.saved_at
            """,
            (user_id, username, access_token, refresh_token, expires_in, guild_id, now)
        )
        conn.commit()
    log.info("토큰 저장: user=%s guild=%s", username, guild_id)


def get_all_tokens(guild_id: str) -> list[dict]:
    """특정 서버의 모든 저장된 토큰 반환 (복구 시 멤버 자동 참가용)"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM oauth_tokens WHERE guild_id=?",
            (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_token_count(guild_id: str) -> int:
    """저장된 토큰 수 반환"""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM oauth_tokens WHERE guild_id=?",
            (guild_id,)
        ).fetchone()
    return row["cnt"] if row else 0


def delete_backup(backup_id: int):
    """백업 삭제 (CASCADE 로 하위 데이터도 삭제)"""
    with get_conn() as conn:
        conn.execute("DELETE FROM backups WHERE id=?", (backup_id,))
        conn.commit()
    log.info("백업 삭제 완료: id=%s", backup_id)


# ══════════════════════════════════════════════════
#  자판기 - 상품
# ══════════════════════════════════════════════════

def get_products(guild_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM products WHERE guild_id=? ORDER BY price ASC", (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def add_product(guild_id: str, name: str, price: int, role_id: str) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO products (guild_id, name, price, role_id, created_at) VALUES (?,?,?,?,?)",
            (guild_id, name, price, role_id, now)
        )
        conn.commit()
    return cur.lastrowid

def delete_product(product_id: int):
    with get_conn() as conn:
        conn.execute("DELETE FROM products WHERE id=?", (product_id,))
        conn.commit()

def update_product(product_id: int, name: str, price: int, role_id: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE products SET name=?, price=?, role_id=? WHERE id=?",
            (name, price, role_id, product_id)
        )
        conn.commit()

# ══════════════════════════════════════════════════
#  자판기 - 잔액
# ══════════════════════════════════════════════════

def get_balance(user_id: str, guild_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance FROM balances WHERE user_id=? AND guild_id=?",
            (user_id, guild_id)
        ).fetchone()
    return row["balance"] if row else 0

def update_balance(user_id: str, guild_id: str, amount: int):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO balances (user_id, guild_id, balance)
               VALUES (?,?,MAX(0,?))
               ON CONFLICT(user_id, guild_id) DO UPDATE SET
               balance = MAX(0, balance + ?)""",
            (user_id, guild_id, max(0, amount), amount)
        )
        conn.commit()

def get_all_balances(guild_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM balances WHERE guild_id=? ORDER BY balance DESC",
            (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]

# ══════════════════════════════════════════════════
#  자판기 - 충전 신청
# ══════════════════════════════════════════════════

def check_pin_used(pin: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM charge_requests WHERE pin=? AND status='approved'",
            (pin,)
        ).fetchone()
    return row is not None

def save_charge_request(user_id: str, username: str, guild_id: str, pin: str) -> int:
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO charge_requests (user_id, username, guild_id, pin, status, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, username, guild_id, pin, "pending", now)
        )
        conn.commit()
    return cur.lastrowid

def get_charge_requests(guild_id: str, status: str = "pending") -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM charge_requests WHERE guild_id=? AND status=? ORDER BY id DESC",
            (guild_id, status)
        ).fetchall()
    return [dict(r) for r in rows]

def get_all_charge_requests(guild_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM charge_requests WHERE guild_id=? ORDER BY id DESC LIMIT 50",
            (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def approve_charge(charge_id: int, amount: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM charge_requests WHERE id=?", (charge_id,)
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE charge_requests SET status='approved', amount=? WHERE id=?",
            (amount, charge_id)
        )
        conn.commit()
    req = dict(row)
    update_balance(req["user_id"], req["guild_id"], amount)
    return req

def reject_charge(charge_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE charge_requests SET status='rejected' WHERE id=?", (charge_id,)
        )
        conn.commit()

# ══════════════════════════════════════════════════
#  자판기 - 구매 내역
# ══════════════════════════════════════════════════

def save_purchase(user_id: str, username: str, guild_id: str, product_id: int, price: int):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO purchases (user_id, username, guild_id, product_id, price, created_at) VALUES (?,?,?,?,?,?)",
            (user_id, username, guild_id, product_id, price, now)
        )
        conn.commit()

def get_purchases(guild_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT p.*, pr.name as product_name FROM purchases p "
            "LEFT JOIN products pr ON p.product_id = pr.id "
            "WHERE p.guild_id=? ORDER BY p.id DESC LIMIT 50",
            (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_total_revenue(guild_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) as total FROM charge_requests WHERE guild_id=? AND status='approved'",
            (guild_id,)
        ).fetchone()
    return row["total"] if row else 0

# ══════════════════════════════════════════════════
#  인바이트 로그
# ══════════════════════════════════════════════════

def save_invite_log(guild_id: str, inviter_id: str, inviter_name: str,
                    invitee_id: str, invitee_name: str, invite_code: str):
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO invite_logs
               (guild_id, inviter_id, inviter_name, invitee_id, invitee_name, invite_code, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (guild_id, inviter_id, inviter_name, invitee_id, invitee_name, invite_code, now)
        )
        conn.commit()

def get_invite_top(guild_id: str, limit: int = 10) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT inviter_id, inviter_name, COUNT(*) as cnt
               FROM invite_logs WHERE guild_id=?
               GROUP BY inviter_id ORDER BY cnt DESC LIMIT ?""",
            (guild_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]

def get_invite_count(guild_id: str, user_id: str) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM invite_logs WHERE guild_id=? AND inviter_id=?",
            (guild_id, user_id)
        ).fetchone()
    return row["cnt"] if row else 0

def get_my_invitees(guild_id: str, user_id: str, limit: int = 5) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM invite_logs WHERE guild_id=? AND inviter_id=? ORDER BY id DESC LIMIT ?",
            (guild_id, user_id, limit)
        ).fetchall()
    return [dict(r) for r in rows]

def get_invite_logs(guild_id: str) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM invite_logs WHERE guild_id=? ORDER BY id DESC LIMIT 50",
            (guild_id,)
        ).fetchall()
    return [dict(r) for r in rows]

# ══════════════════════════════════════════════════
#  내부 유틸
# ══════════════════════════════════════════════════

def _serialize_overwrites(overwrites: dict) -> str:
    """discord PermissionOverwrite 를 JSON 문자열로 직렬화"""
    data = {}
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        data[str(target.id)] = {
            "type":  "role" if hasattr(target, "permissions") else "member",
            "allow": allow.value,
            "deny":  deny.value,
        }
    return json.dumps(data)
