"""
database/db.py
--------------
Lightweight SQLite wrapper for the licensing key system.
Handles schema initialization and all CRUD operations on keys.
"""

import sqlite3
import os
import time
import secrets
import string
import logging

logger = logging.getLogger(__name__)

# Path to the SQLite database file (relative to this file's location)
DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")

# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────
CREATE_KEYS_TABLE = """
CREATE TABLE IF NOT EXISTS keys (
    key         TEXT PRIMARY KEY,
    discord_id  TEXT NOT NULL,
    hwid        TEXT,
    expires_at  REAL,           -- Unix timestamp; NULL = lifetime
    status      TEXT NOT NULL DEFAULT 'active',   -- active | revoked
    created_at  REAL NOT NULL,
    note        TEXT            -- optional memo/label
);
"""

CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS usage_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    event       TEXT NOT NULL,  -- check | generate | revoke
    hwid        TEXT,
    ip          TEXT,
    ts          REAL NOT NULL
);
"""


def get_connection() -> sqlite3.Connection:
    """Return a thread-safe SQLite connection with row_factory set."""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def initialize_db():
    """Create tables if they don't exist yet. Call once at startup."""
    with get_connection() as conn:
        conn.execute(CREATE_KEYS_TABLE)
        conn.execute(CREATE_LOGS_TABLE)
        conn.commit()
    logger.info("Database initialized at %s", DB_PATH)


# ─────────────────────────────────────────────
# Key generation helpers
# ─────────────────────────────────────────────

def _generate_key(prefix: str = "LIC") -> str:
    """
    Generate a cryptographically random license key.
    Format: LIC-XXXX-XXXX-XXXX-XXXX  (uppercase alphanumeric segments)
    """
    charset = string.ascii_uppercase + string.digits
    segments = ["".join(secrets.choice(charset) for _ in range(4)) for _ in range(4)]
    return f"{prefix}-" + "-".join(segments)


def _duration_to_seconds(duration: str) -> float | None:
    """
    Parse a human-readable duration string to seconds.
    Supported formats:
        '1h'   → 3600
        '24h'  → 86400
        '7d'   → 604800
        '30m'  → 1800
        'lifetime' | 'perm' → None (no expiry)
    """
    duration = duration.strip().lower()
    if duration in ("lifetime", "perm", "permanent"):
        return None
    
    # Check for multi-character units first (like 'mo' for month)
    if duration.endswith("mo"):
        unit = "mo"
        value_str = duration[:-2]
    else:
        unit = duration[-1]
        value_str = duration[:-1]

    try:
        value = float(value_str)
    except ValueError:
        raise ValueError(f"Invalid duration format: '{duration}'. Use e.g. '30m', '12h', '7d', '1mo', 'lifetime'.")

    multipliers = {
        "m": 60,            # Minutes
        "h": 3600,          # Hours
        "d": 86400,         # Days
        "mo": 2592000,      # Months (30 Days)
    }
    
    if unit not in multipliers:
        raise ValueError(f"Unknown time unit '{unit}'. Use m, h, d, or mo (for months).")
    
    return value * multipliers[unit]


# ─────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────

def create_key(discord_id: str, duration: str = "lifetime", note: str = "") -> dict:
    """
    Insert a new license key into the database.

    Args:
        discord_id: The Discord snowflake ID of the owner.
        duration:   e.g. '1h', '24h', '7d', 'lifetime'
        note:       Optional label/memo.

    Returns:
        A dict with the key data.
    """
    secs = _duration_to_seconds(duration)
    now = time.time()
    key = _generate_key()
    expires_at = (now + secs) if secs is not None else None

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO keys (key, discord_id, hwid, expires_at, status, created_at, note) "
            "VALUES (?, ?, NULL, ?, 'active', ?, ?)",
            (key, str(discord_id), expires_at, now, note),
        )
        conn.commit()

    log_event(key, "generate", ip=None, hwid=None)
    logger.info("Key created: %s | discord_id=%s | duration=%s", key, discord_id, duration)
    return get_key(key)


def get_key(key: str) -> dict | None:
    """Fetch a single key row. Returns None if not found."""
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM keys WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def revoke_key(key: str) -> bool:
    """
    Mark a key as revoked.

    Returns:
        True if a row was updated, False if the key didn't exist.
    """
    with get_connection() as conn:
        cur = conn.execute("UPDATE keys SET status = 'revoked' WHERE key = ?", (key,))
        conn.commit()
    if cur.rowcount:
        log_event(key, "revoke")
        logger.info("Key revoked: %s", key)
    return bool(cur.rowcount)


def bind_hwid(key: str, hwid: str) -> bool:
    """
    Bind an HWID to a key on first use.
    Only binds if the key currently has no HWID (hwid IS NULL).

    Returns:
        True if bound, False if already bound to a different HWID.
    """
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE keys SET hwid = ? WHERE key = ? AND hwid IS NULL",
            (hwid, key),
        )
        conn.commit()
    return bool(cur.rowcount)


def validate_key(key: str, hwid: str | None = None) -> dict:
    """
    Core validation logic.

    Returns a dict:
        {"status": "valid"|"invalid"|"expired"|"revoked"|"hwid_mismatch", ...}
    """
    row = get_key(key)

    if row is None:
        return {"status": "invalid", "message": "Key not found."}

    if row["status"] == "revoked":
        return {"status": "revoked", "message": "This key has been revoked."}

    now = time.time()
    if row["expires_at"] is not None and now > row["expires_at"]:
        # Auto-mark expired in DB
        with get_connection() as conn:
            conn.execute("UPDATE keys SET status = 'revoked' WHERE key = ?", (key,))
            conn.commit()
        return {"status": "expired", "message": "This key has expired."}

    # HWID check
    if hwid:
        if row["hwid"] is None:
            # First use — bind HWID
            bind_hwid(key, hwid)
        elif row["hwid"] != hwid:
            log_event(key, "hwid_mismatch", hwid=hwid)
            return {
                "status": "hwid_mismatch",
                "message": "HWID does not match the registered device.",
            }

    log_event(key, "check", hwid=hwid)
    return {
        "status": "valid",
        "message": "Key is valid.",
        "discord_id": row["discord_id"],
        "expires_at": row["expires_at"],
        "hwid": row["hwid"] or hwid,
    }


def delete_expired_keys():
    """Hard-delete rows that are past expiry. Call periodically."""
    now = time.time()
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM keys WHERE expires_at IS NOT NULL AND expires_at < ?", (now,)
        )
        conn.commit()
    if cur.rowcount:
        logger.info("Auto-deleted %d expired key(s).", cur.rowcount)
    return cur.rowcount


def log_event(key: str, event: str, hwid: str | None = None, ip: str | None = None):
    """Append a row to usage_logs."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO usage_logs (key, event, hwid, ip, ts) VALUES (?, ?, ?, ?, ?)",
            (key, event, hwid, ip, time.time()),
        )
        conn.commit()


def list_keys(discord_id: str | None = None) -> list[dict]:
    """Return all keys, optionally filtered by Discord ID."""
    sql = "SELECT * FROM keys"
    params = ()
    if discord_id:
        sql += " WHERE discord_id = ?"
        params = (str(discord_id),)
    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
