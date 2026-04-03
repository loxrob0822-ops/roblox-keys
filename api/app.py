"""
api/app.py
----------
Flask REST API for the Luarmor-style licensing system.

Endpoints:
    POST /generate  — Create a new license key        (requires API token)
    POST /revoke    — Revoke an existing key           (requires API token)
    POST /check     — Validate a key (Roblox client)  (rate-limited, public)

Run with:
    python api/app.py
or via Gunicorn in production:
    gunicorn -w 2 -b 0.0.0.0:5000 app:app
"""

import sys, os
# Allow importing from sibling packages
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import time
import threading
from functools import wraps

import requests
from flask import Flask, request, jsonify, Response
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from database.db import (
    initialize_db,
    create_key,
    get_key,
    revoke_key,
    validate_key,
    delete_expired_keys,
    list_keys,
)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("api/api.log"),
    ],
)
logger = logging.getLogger("flask.app")

# ─────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────
app = Flask(__name__)

# Rate limiting: 30 requests/minute per IP for public endpoints
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per hour"],
    storage_uri="memory://",
)

# ─────────────────────────────────────────────
# Config — read from environment variables or use a .env loader
# ─────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass  # python-dotenv not required in pure API mode

API_MASTER_TOKEN = os.environ.get("API_MASTER_TOKEN", "change_me_super_secret_token")
# A secondary token just for reading key-info from the bot (optional same token)
BOT_READ_TOKEN   = os.environ.get("BOT_READ_TOKEN", API_MASTER_TOKEN)
# The raw script URL that will be delivered upon successful validation
MAIN_SCRIPT_URL  = os.environ.get("MAIN_SCRIPT_URL", "")

# ─────────────────────────────────────────────
# Cache for the script payload
# ─────────────────────────────────────────────
_payload_cache = {"content": None, "expiry": 0}
PAYLOAD_CACHE_TTL = 300  # 5 minutes


# ─────────────────────────────────────────────
# Auth decorator
# ─────────────────────────────────────────────
def require_token(f):
    """Reject requests that don't carry the correct Authorization header."""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"error": "Missing or malformed Authorization header."}), 401
        token = auth[len("Bearer "):]
        if token not in (API_MASTER_TOKEN, BOT_READ_TOKEN):
            return jsonify({"error": "Invalid API token."}), 403
        return f(*args, **kwargs)
    return decorated


import traceback

def djb2_hash(secret, key, status, p_check):
    """Simple 32-bit hash matching the Lua client's signature logic."""
    raw = f"{secret}:{key}:{status}:{p_check}"
    h = 5381
    for char in raw:
        # (h * 33) + char
        h = ((h << 5) + h) + ord(char)
        h &= 0xFFFFFFFF  # Ensure it stays within 32-bit unsigned range
    return f"{h:08x}"  # 8 hex chars (32 bits)

@app.errorhandler(Exception)
def handle_exception(e):
    """Catch-all for any unhandled errors, returning the traceback to the user."""
    logger.exception("!!! UNHANDLED EXCEPTION !!!: %s", e)
    return jsonify({
        "error": "Internal Server Error",
        "details": str(e),
        "traceback": traceback.format_exc()
    }), 500

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _format_key_info(row: dict) -> dict:
    """Serialize a key row for JSON output."""
    if row is None:
        return {}
    now = time.time()
    if row["expires_at"] is None:
        ttl_str = "lifetime"
    elif row["expires_at"] > now:
        remaining = int(row["expires_at"] - now)
        h, r = divmod(remaining, 3600)
        m, s = divmod(r, 60)
        ttl_str = f"{h}h {m}m {s}s remaining"
    else:
        ttl_str = "expired"

    return {
        "key":        row["key"],
        "discord_id": row["discord_id"],
        "hwid":       row["hwid"],
        "expires_at": row["expires_at"],
        "ttl":        ttl_str,
        "status":     row["status"],
        "created_at": row["created_at"],
        "note":       row.get("note"),
    }


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    """Main homepage - prevents 404."""
    return jsonify({
        "name": "Luarmor-Style Licensing API",
        "status": "online",
        "version": "1.0.0",
        "documentation": "See Discord bot for commands."
    })


@app.route("/health", methods=["GET"])
def health():
    """Simple health-check — no auth required."""
    return jsonify({"status": "ok", "ts": time.time()})


@app.route("/loader.lua", methods=["GET"])
def loader():
    """
    Serve the Roblox loader script.
    Roblox users run: loadstring(game:HttpGet(".../loader.lua"))()
    """
    try:
        loader_path = os.path.join(os.path.dirname(__file__), "..", "roblox", "client.lua")
        with open(loader_path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(content, mimetype="text/plain")
    except Exception as e:
        logger.error("Failed to serve loader.lua: %s", e)
        return "error: could not load loader script", 500


@app.route("/generate", methods=["POST"])
@require_token
def generate():
    """
    Create a new license key.

    JSON body:
        {
          "discord_id": "123456789",
          "duration":   "7d",        // 30m | 6h | 7d | lifetime
          "note":       "optional"
        }
    """
    data = request.get_json(silent=True) or {}
    discord_id = data.get("discord_id")
    duration   = data.get("duration", "lifetime")
    note       = data.get("note", "")

    if not discord_id:
        return jsonify({"error": "'discord_id' is required."}), 400

    try:
        row = create_key(discord_id=str(discord_id), duration=duration, note=note)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    logger.info("Generated key %s for discord_id=%s duration=%s", row["key"], discord_id, duration)
    return jsonify({"success": True, "key": _format_key_info(row)}), 201


@app.route("/revoke", methods=["POST"])
@require_token
def revoke():
    """
    Revoke an existing key.

    JSON body:
        {"key": "LIC-XXXX-XXXX-XXXX-XXXX"}
    """
    data = request.get_json(silent=True) or {}
    key  = data.get("key", "").strip()

    if not key:
        return jsonify({"error": "'key' is required."}), 400

    ok = revoke_key(key)
    if not ok:
        return jsonify({"error": "Key not found."}), 404

    logger.info("Key revoked via API: %s", key)
    return jsonify({"success": True, "message": f"Key {key} has been revoked."})


@app.route("/check", methods=["POST"])
@limiter.limit("30 per minute")   # Roblox servers can call at most 30 req/min per IP
def check():
    """
    Validate a license key — called by the Roblox Lua client.

    JSON body:
        {
          "key":  "LIC-XXXX-XXXX-XXXX-XXXX",
          "hwid": "optional-hardware-id"
        }

    Response:
        {"status": "valid"|"invalid"|"expired"|"revoked"|"hwid_mismatch", ...}
    """
    data = request.get_json(silent=True) or {}
    key  = data.get("key", "").strip()
    hwid = data.get("hwid", "").strip() or None

    if not key:
        return jsonify({"status": "invalid", "message": "'key' is required."}), 400

    result = validate_key(key, hwid=hwid)

    # If valid, include the script payload
    if result["status"] == "valid":
        content = None
        now = time.time()
        
        # 1. Try Cache
        if _payload_cache["content"] and _payload_cache["expiry"] > now:
            content = _payload_cache["content"]
        elif MAIN_SCRIPT_URL:
            # 2. Try Fetch
            try:
                resp = requests.get(MAIN_SCRIPT_URL, timeout=10)
                if resp.status_code == 200:
                    content = resp.text
                    _payload_cache["content"] = content
                    _payload_cache["expiry"]  = now + PAYLOAD_CACHE_TTL
                else:
                    logger.error("Failed to fetch payload: %d", resp.status_code)
            except Exception as e:
                logger.error("Exception while fetching payload: %s", e)

        # 3. Fallback to a default Success script if no payload found
        if not content:
            content = '-- [LICENSE] Default Success Script\nprint("✅ Script loaded successfully from the license server!")\ngame:GetService("StarterGui"):SetCore("SendNotification", {Title="License System", Text="✅ Access Granted! Your script is now running.", Duration=10})'
            logger.info("Delivering default fallback script.")
        
        result["payload"] = content

    # Attach a simple signature so the Lua client can verify the response.
    # We use a DJB2 hash matching the client's implementation.
    payload_check = "p+" if result.get("payload") else "p-"
    sig = djb2_hash(API_MASTER_TOKEN, key, result["status"], payload_check)
    result["sig"] = sig

    http_code = 200 if result["status"] == "valid" else 401
    logger.info("Key check: %s → %s (hwid=%s)", key, result["status"], hwid)
    return jsonify(result), http_code


@app.route("/keyinfo", methods=["POST"])
@require_token
def keyinfo():
    """
    Fetch detailed info about a key (used by !keyinfo bot command).

    JSON body: {"key": "LIC-XXXX-XXXX-XXXX-XXXX"}
    """
    data = request.get_json(silent=True) or {}
    key  = data.get("key", "").strip()

    if not key:
        return jsonify({"error": "'key' is required."}), 400

    row = get_key(key)
    if row is None:
        return jsonify({"error": "Key not found."}), 404

    return jsonify({"success": True, "key": _format_key_info(row)})


@app.route("/listkeys", methods=["POST"])
@require_token
def listkeys():
    """
    List all keys, optionally filtered by discord_id.

    JSON body: {"discord_id": "123"} (optional)
    """
    data = request.get_json(silent=True) or {}
    discord_id = data.get("discord_id")
    rows = list_keys(discord_id=discord_id)
    return jsonify({"success": True, "count": len(rows), "keys": [_format_key_info(r) for r in rows]})


# ─────────────────────────────────────────────
# Background cleanup job
# ─────────────────────────────────────────────
def _cleanup_loop(interval: int = 600):
    """Delete expired keys every `interval` seconds."""
    while True:
        time.sleep(interval)
        try:
            deleted = delete_expired_keys()
            if deleted:
                logger.info("[cleanup] Deleted %d expired key(s).", deleted)
        except Exception as exc:
            logger.exception("[cleanup] Error: %s", exc)


# ─────────────────────────────────────────────
# Initialization
# ─────────────────────────────────────────────

# Initialize the database (tables, etc.)
initialize_db()

# Start background cleanup thread (daemon so it dies with the process)
cleaner = threading.Thread(target=_cleanup_loop, args=(600,), daemon=True)
cleaner.start()
logger.info("Cleanup thread started.")

if __name__ == "__main__":
    # When running locally via 'python app.py'
    port = int(os.environ.get("PORT", 5000))
    logger.info("Starting Flask API on port %d [LOCAL MODE] …", port)
    app.run(host="0.0.0.0", port=port, debug=False)
