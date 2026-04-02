"""
bot/bot.py
----------
Discord bot for the Luarmor-style licensing system.

Commands (admin/staff only):
    !genkey @user <duration>   — Generate a key and DM it to the user
    !keyinfo <key>             — Show status and details of a key
    !revoke <key>              — Revoke a key
    !listkeys [@user]          — List all keys (optionally for a specific user)

All actions are logged in a dedicated channel (set LOG_CHANNEL_ID in .env).

Run with:
    python bot/bot.py
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import logging
import time
from datetime import datetime, timezone

import discord
from discord.ext import commands
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
DISCORD_TOKEN   = os.environ["DISCORD_BOT_TOKEN"]        # Your bot token
API_BASE_URL    = os.environ.get("API_BASE_URL", "http://127.0.0.1:5000")
API_MASTER_TOKEN= os.environ["API_MASTER_TOKEN"]         # Same token as the API uses
LOG_CHANNEL_ID  = int(os.environ.get("LOG_CHANNEL_ID", 0))  # Channel for audit logs
ALLOWED_ROLES   = set(
    r.strip() for r in os.environ.get("ALLOWED_ROLES", "Admin,Staff,Moderator").split(",")
)  # Role names that can use commands (case-sensitive)

# ─────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot/bot.log"),
    ],
)
logger = logging.getLogger("discord_bot")

# ─────────────────────────────────────────────
# Bot setup
# ─────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
API_HEADERS = {"Authorization": f"Bearer {API_MASTER_TOKEN}", "Content-Type": "application/json"}


def api_post(endpoint: str, payload: dict) -> dict:
    """POST to the Flask API and return the JSON response dict."""
    try:
        resp = requests.post(
            f"{API_BASE_URL}/{endpoint.lstrip('/')}",
            json=payload,
            headers=API_HEADERS,
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()
        elif resp.status_code == 503 or resp.status_code == 502:
            return {"error": "The API is still waking up... Please try again in 10-20 seconds."}
        else:
            return {"error": f"API Error {resp.status_code}: {resp.text[:100]}"}
    except requests.RequestException as exc:
        logger.error("API request failed: %s", exc)
        return {"error": "Connection timed out. The server is likely waking up, please try once more."}


def has_allowed_role(member: discord.Member) -> bool:
    """Return True if the member is owner, has admin perms, or holds an allowed role."""
    # 1. Check if server owner
    if member.guild.owner_id == member.id:
        return True
    # 2. Check for Administrator permission
    if member.guild_permissions.administrator:
        return True
    # 3. Check for specific role names
    return any(role.name in ALLOWED_ROLES for role in member.roles)


def unix_to_readable(ts: float | None) -> str:
    if ts is None:
        return "Lifetime [INF]"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def log_action(ctx: commands.Context, description: str):
    """Send an embed to the audit-log channel."""
    if not LOG_CHANNEL_ID:
        return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if channel is None:
        return

    embed = discord.Embed(
        title="🔐 License Action",
        description=description,
        color=discord.Color.blurple(),
        timestamp=datetime.now(tz=timezone.utc),
    )
    embed.set_footer(text=f"By {ctx.author} ({ctx.author.id}) in #{ctx.channel}")
    await channel.send(embed=embed)


# ─────────────────────────────────────────────
# Permission check decorator
# ─────────────────────────────────────────────
def staff_only():
    """Command check: only members with an allowed role can run the command."""
    async def predicate(ctx: commands.Context) -> bool:
        if isinstance(ctx.author, discord.Member) and has_allowed_role(ctx.author):
            return True
        await ctx.send(
            embed=discord.Embed(
                description="[ERROR] You don't have permission to use this command.",
                color=discord.Color.red(),
            )
        )
        return False
    return commands.check(predicate)


# ─────────────────────────────────────────────
# Commands
# ─────────────────────────────────────────────

@bot.command(name="genkey", aliases=["gk", "createkey"])
@staff_only()
async def genkey(ctx: commands.Context, member: discord.Member = None, duration: str = "lifetime", *, note: str = ""):
    """
    !genkey @user <duration> [note]
    Duration: 30m | 6h | 7d | lifetime
    """
    if member is None:
        await ctx.send(
            embed=discord.Embed(
                description="❌ Usage: `!genkey @user <duration> [note]`\n"
                            "Duration examples: `30m`, `6h`, `7d`, `lifetime`",
                color=discord.Color.red(),
            )
        )
        return

    async with ctx.typing():
        result = api_post("generate", {
            "discord_id": str(member.id),
            "duration": duration,
            "note": note or f"Generated by {ctx.author} via Discord",
        })

    if "error" in result:
        await ctx.send(
            embed=discord.Embed(description=f"❌ API Error: {result['error']}", color=discord.Color.red())
        )
        return

    key_data = result["key"]
    key_str  = key_data["key"]
    expires  = unix_to_readable(key_data["expires_at"])

    # Build the 1-Line Loader format
    loader_str = (
        f'script_key="{key_str}";\n'
        f'loadstring(game:HttpGet("{API_BASE_URL}/loader.lua"))()'
    )

    # Build the embed
    embed = discord.Embed(
        title="✅ License Key Generated",
        color=discord.Color.green(),
        timestamp=datetime.now(tz=timezone.utc),
    )
    embed.add_field(name="Copy & Paste as 1-Line", value=f"```lua\n{loader_str}```", inline=False)
    embed.add_field(name="User",    value=member.mention, inline=True)
    embed.add_field(name="Expires", value=expires, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    if note:
        embed.add_field(name="Note", value=note, inline=False)

    await ctx.send(embed=embed)
    await log_action(ctx, f"Generated key `{key_str}` ({duration}) for {member} (`{member.id}`)")


@bot.command(name="keyinfo", aliases=["ki"])
@staff_only()
async def keyinfo(ctx: commands.Context, key: str = None):
    """!keyinfo <key>  — Show details about a license key."""
    if not key:
        await ctx.send(
            embed=discord.Embed(description="❌ Usage: `!keyinfo <key>`", color=discord.Color.red())
        )
        return

    async with ctx.typing():
        result = api_post("keyinfo", {"key": key.strip()})

    if "error" in result:
        await ctx.send(
            embed=discord.Embed(description=f"❌ {result['error']}", color=discord.Color.red())
        )
        return

    kd = result["key"]
    status_colors = {
        "active":  discord.Color.green(),
        "revoked": discord.Color.red(),
    }
    color = status_colors.get(kd["status"], discord.Color.orange())

    embed = discord.Embed(title="🔍 Key Info", color=color, timestamp=datetime.now(tz=timezone.utc))
    embed.add_field(name="Key",        value=f"```{kd['key']}```",         inline=False)
    embed.add_field(name="Status",     value=kd["status"].upper(),          inline=True)
    embed.add_field(name="Discord ID", value=f"`{kd['discord_id']}`",       inline=True)
    embed.add_field(name="Expires",    value=unix_to_readable(kd["expires_at"]), inline=True)
    embed.add_field(name="TTL",        value=kd["ttl"],                     inline=True)
    embed.add_field(name="HWID",       value=kd["hwid"] or "Not bound yet", inline=True)
    if kd.get("note"):
        embed.add_field(name="Note",   value=kd["note"],                    inline=False)

    await ctx.send(embed=embed)


@bot.command(name="revoke", aliases=["rk"])
@staff_only()
async def revoke(ctx: commands.Context, key: str = None):
    """!revoke <key>  — Revoke a license key immediately."""
    if not key:
        await ctx.send(
            embed=discord.Embed(description="❌ Usage: `!revoke <key>`", color=discord.Color.red())
        )
        return

    async with ctx.typing():
        result = api_post("revoke", {"key": key.strip()})

    if "error" in result:
        await ctx.send(
            embed=discord.Embed(description=f"❌ {result['error']}", color=discord.Color.red())
        )
        return

    embed = discord.Embed(
        title="🚫 Key Revoked",
        description=f"Key `{key}` has been successfully revoked.",
        color=discord.Color.red(),
        timestamp=datetime.now(tz=timezone.utc),
    )
    await ctx.send(embed=embed)
    await log_action(ctx, f"Revoked key `{key}`")


@bot.command(name="listkeys", aliases=["lk", "keys"])
@staff_only()
async def listkeys(ctx: commands.Context, member: discord.Member = None):
    """!listkeys [@user]  — List all keys, or only those belonging to the mentioned user."""
    payload = {}
    if member:
        payload["discord_id"] = str(member.id)

    async with ctx.typing():
        result = api_post("listkeys", payload)

    if "error" in result:
        await ctx.send(
            embed=discord.Embed(description=f"❌ {result['error']}", color=discord.Color.red())
        )
        return

    keys = result["keys"]
    if not keys:
        await ctx.send(
            embed=discord.Embed(description="No keys found.", color=discord.Color.orange())
        )
        return

    # Paginate: show up to 10 keys per message
    chunks = [keys[i:i+10] for i in range(0, len(keys), 10)]
    for page, chunk in enumerate(chunks, 1):
        lines = []
        for k in chunk:
            status_icon = "🟢" if k["status"] == "active" else "🔴"
            ttl = k["ttl"] if k["ttl"] != "lifetime" else "♾️"
            lines.append(f"{status_icon} `{k['key']}` | <@{k['discord_id']}> | {ttl}")

        embed = discord.Embed(
            title=f"🗝️ License Keys (Page {page}/{len(chunks)}) — {result['count']} total",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed)


@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    """Show available commands."""
    embed = discord.Embed(
        title="🔐 License Bot Commands",
        color=discord.Color.blurple(),
        description="All commands require Admin/Staff role.",
    )
    embed.add_field(
        name="!genkey @user <duration> [note]",
        value="Generate and DM a license key.\nDuration: `30m`, `6h`, `7d`, `lifetime`",
        inline=False,
    )
    embed.add_field(name="!keyinfo <key>",     value="Show key details and status.", inline=False)
    embed.add_field(name="!revoke <key>",      value="Revoke a key immediately.",    inline=False)
    embed.add_field(name="!listkeys [@user]",  value="List all keys (or for a user).", inline=False)
    await ctx.send(embed=embed)


# ─────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info("Bot logged in as %s (%s)", bot.user.name, bot.user.id)
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching, name="license keys"
        )
    )


@bot.event
async def on_command_error(ctx: commands.Context, error):
    if isinstance(error, commands.CheckFailure):
        return  # Already handled inside the check
    
    try:
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(
                embed=discord.Embed(description="[ERROR] Member not found. Mention a valid server member.", color=discord.Color.red())
            )
            return
        logger.error("Unhandled command error: %s", error, exc_info=True)
        await ctx.send(
            embed=discord.Embed(description=f"[ERROR] Unexpected error: `{error}`", color=discord.Color.red())
        )
    except discord.Forbidden:
        logger.error("Missing permissions to send message in channel %s", ctx.channel.id)
    except Exception as e:
        logger.error("Error in on_command_error handler: %s", e)


# ─────────────────────────────────────────────
# Run
# ─────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
