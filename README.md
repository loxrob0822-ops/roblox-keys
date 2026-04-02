# 🔐 License Key System — Luarmor-Style

A full-featured software licensing and key validation system built with **Flask**, **discord.py**, **SQLite**, and a **Roblox Lua client**.

---

## 📁 Project Structure

```
project/
├── api/
│   ├── app.py              ← Flask REST API
│   ├── requirements.txt
│   ├── .env.example        ← Copy to .env and fill in values
│   └── api.log             ← Auto-created on first run
├── bot/
│   ├── bot.py              ← Discord bot
│   ├── requirements.txt
│   ├── .env.example        ← Copy to .env and fill in values
│   └── bot.log             ← Auto-created on first run
├── database/
│   ├── db.py               ← SQLite wrapper (shared by API)
│   ├── __init__.py
│   └── data.db             ← Auto-created on first run
├── roblox/
│   └── client.lua          ← Roblox Lua loader / key validator
└── README.md
```

---

## ⚙️ Setup

### 1. Prerequisites

- Python 3.11+
- A Discord bot token (from [Discord Dev Portal](https://discord.com/developers/applications))
- Optional: a public domain / ngrok for testing with Roblox

---

### 2. Install API Dependencies

```bash
cd api
pip install -r requirements.txt
```

Copy the env file and fill in your values:

```bash
copy .env.example .env   # Windows
# or
cp .env.example .env     # Linux/macOS
```

Edit `api/.env`:
```
API_MASTER_TOKEN=your_very_secret_token_here
PORT=5000
```

Generate a strong token with:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

### 3. Install Bot Dependencies

```bash
cd bot
pip install -r requirements.txt
```

Copy and edit the env file:

```bash
copy .env.example .env
```

Edit `bot/.env`:
```
DISCORD_BOT_TOKEN=your_discord_bot_token
API_MASTER_TOKEN=your_very_secret_token_here   ← must match api/.env
API_BASE_URL=http://127.0.0.1:5000
LOG_CHANNEL_ID=123456789012345678              ← your log channel ID
ALLOWED_ROLES=Admin,Staff                       ← comma-separated role names
```

---

### 4. Run the Flask API

```bash
cd api
python app.py
```

The API starts on `http://0.0.0.0:5000` by default.

---

### 5. Run the Discord Bot

```bash
cd bot
python bot.py
```

---

### 6. Configure the Roblox Client

Open `roblox/client.lua` and change:

```lua
local API_URL    = "https://your-api-domain.com/check"
local API_SECRET = "your_very_secret_token_here"  -- same as API_MASTER_TOKEN
```

If you want the protected script to be hosted remotely (most secure):
```lua
local MAIN_SCRIPT_URL  = "https://your-cdn.com/main.lua"
local USE_REMOTE_PAYLOAD = true
```

Or embed your script directly inside `runMainScript()` and set `USE_REMOTE_PAYLOAD = false`.

---

## 🔌 API Endpoints

All authenticated endpoints require:
```
Authorization: Bearer <API_MASTER_TOKEN>
Content-Type: application/json
```

| Method | Endpoint     | Auth | Description                                 |
|--------|-------------|------|---------------------------------------------|
| GET    | /health     | No   | Health check                                |
| POST   | /generate   | Yes  | Create a new license key                    |
| POST   | /revoke     | Yes  | Revoke an existing key                      |
| POST   | /check      | No   | Validate a key (Roblox client endpoint)     |
| POST   | /keyinfo    | Yes  | Fetch full details about a key              |
| POST   | /listkeys   | Yes  | List all keys (optionally by discord_id)    |

### POST /generate

```json
{
  "discord_id": "123456789",
  "duration": "7d",
  "note": "optional label"
}
```

Duration formats: `30m` `6h` `7d` `lifetime`

### POST /check (Roblox client)

```json
{
  "key": "LIC-XXXX-XXXX-XXXX-XXXX",
  "hwid": "rbx_123456789_SomeExecutor"
}
```

Response statuses: `valid` `invalid` `expired` `revoked` `hwid_mismatch`

### POST /revoke

```json
{ "key": "LIC-XXXX-XXXX-XXXX-XXXX" }
```

---

## 🤖 Discord Bot Commands

> All commands require a role listed in `ALLOWED_ROLES`.

| Command                          | Description                                     |
|----------------------------------|-------------------------------------------------|
| `!genkey @user <duration> [note]`| Generate a key and DM it to the user           |
| `!keyinfo <key>`                 | Show key status, expiry, HWID, and Discord ID   |
| `!revoke <key>`                  | Revoke a key immediately                        |
| `!listkeys [@user]`              | List all keys, or only those for a user         |
| `!help`                          | Show command list                               |

---

## 🔒 Security Features

| Feature              | Implementation                                                    |
|---------------------|--------------------------------------------------------------------|
| API Token Auth       | `Authorization: Bearer <token>` header on all write endpoints     |
| Rate Limiting        | 30 req/min per IP on `/check` (via Flask-Limiter)                 |
| HWID Locking         | Key binds to first device/HWID on use; rejects mismatches         |
| Lifetime Keys        | `expires_at = NULL` in DB; never expire                           |
| Response Signature   | API returns a 16-char HMAC `sig` field; Lua client verifies it    |
| Auto-Expiry          | Background thread marks/deletes keys every 10 minutes             |
| Audit Logging        | All actions logged to `usage_logs` table and a Discord channel    |

---

## 🛡️ Anti-Bypass Notes (Roblox Side)

1. **Response signature** — the Lua client verifies the `sig` field. A local proxy that fakes `"status": "valid"` won't know the correct HMAC.
2. **Main script on CDN** — with `USE_REMOTE_PAYLOAD = true`, the actual script code is never in the client file. An attacker who bypasses validation still gets nothing useful.
3. **HWID binding** — keys are single-device after first use; sharing a key doesn't help.
4. **Rate limiting** — brute-force key guessing is blocked server-side.

---

## 🗂️ Database Schema

```sql
-- keys table
key         TEXT PRIMARY KEY
discord_id  TEXT NOT NULL
hwid        TEXT            -- NULL until first use
expires_at  REAL            -- Unix timestamp, NULL = lifetime
status      TEXT            -- 'active' | 'revoked'
created_at  REAL
note        TEXT

-- usage_logs table  
id          INTEGER PK AUTOINCREMENT
key         TEXT
event       TEXT            -- 'check' | 'generate' | 'revoke' | 'hwid_mismatch'
hwid        TEXT
ip          TEXT
ts          REAL
```

---

## 🚀 Production Tips

- Deploy the Flask API with **Gunicorn** behind **Nginx** with HTTPS.
- Store `.env` files securely and never commit them.
- Set `debug=False` in production (already the default in `app.py`).
- Use environment variables for all secrets (e.g., via a secrets manager).
- Point `MAIN_SCRIPT_URL` to a private CDN or signed URL for maximum security.
