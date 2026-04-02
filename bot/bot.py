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
import requests
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

from typing import Optional

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
DISCORD_TOKEN   = os.environ["DISCORD_BOT_TOKEN"]
API_BASE_URL    = os.environ.get("API_BASE_URL", "https://roblox-keys-production.up.railway.app")
API_MASTER_TOKEN= os.environ["API_MASTER_TOKEN"]
LOG_CHANNEL_ID  = int(os.environ.get("LOG_CHANNEL_ID", 0))
GUILD_ID        = int(os.environ.get("GUILD_ID", "1488928422943264819")) # User provided Server ID
ALLOWED_ROLES   = set(
    r.strip() for r in os.environ.get("ALLOWED_ROLES", "Admin,Staff,Moderator,ASX").split(",")
)

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
# Bot Class with Slash Sync
# ─────────────────────────────────────────────
class LicenseBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self):
        # This copies global commands to your specific guild for instant updates
        guild = discord.Object(id=GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        
        # Register the persistent view so the button works after bot restart
        self.add_view(ControlPanelView())
        
        logger.info("[SUCCESS] Slash commands & Persistent Views synced to Server ID: %s", GUILD_ID)

bot = LicenseBot()

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
        if resp.status_code in (200, 201):
            return resp.json()
        elif resp.status_code in (503, 502):
            return {"error": "The API is still waking up... Please try again in 10-20 seconds."}
        else:
            return {"error": f"API Error {resp.status_code}: {resp.text[:100]}"}
    except requests.RequestException as exc:
        logger.error("API request failed: %s", exc)
        return {"error": "Connection timed out. The server is likely waking up, please try once more."}


def has_allowed_role(member: discord.Member) -> bool:
    if member.guild.owner_id == member.id: return True
    if member.guild_permissions.administrator: return True
    return any(role.name in ALLOWED_ROLES for role in member.roles)

def is_staff():
    """Slash command check for staff roles."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if has_allowed_role(interaction.user):
            return True
        await interaction.response.send_message(
            "[ERROR] You do not have permission to use this command.", 
            ephemeral=True
        )
        return False
    return app_commands.check(predicate)


def unix_to_readable(ts: float | None) -> str:
    if ts is None:
        return "Lifetime [INF]"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


async def log_action(interaction: discord.Interaction, description: str):
    """Send an embed to the audit-log channel."""
    if not LOG_CHANNEL_ID: return
    channel = bot.get_channel(LOG_CHANNEL_ID)
    if not channel: return
    embed = discord.Embed(title="🔐 License Action", description=description, 
                          color=discord.Color.blurple(), timestamp=datetime.now(tz=timezone.utc))
    embed.set_footer(text=f"By {interaction.user} (#{interaction.channel})")
    await channel.send(embed=embed)

# ─────────────────────────────────────────────
# UI Components (Views/Buttons)
# ─────────────────────────────────────────────

class ControlPanelView(discord.ui.View):
    """
    Persistent view for the Control Panel.
    Contains a 'Get Script' button that checks whitelist status.
    """
    def __init__(self):
        super().__init__(timeout=None) # timeout=None makes it persistent if added to setup_hook

    @discord.ui.button(label="📜 Get Script", style=discord.ButtonStyle.primary, custom_id="btn_get_script")
    async def get_script(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        
        # 1. Check if user has an active key
        result = api_post("listkeys", {"discord_id": str(interaction.user.id)})
        
        if "error" in result:
            await interaction.followup.send(f"❌ **API Error**: {result['error']}", ephemeral=True)
            return

        # Filter for active keys
        active_keys = [k for k in result.get("keys", []) if k["status"] == "active"]
        
        if not active_keys:
            await interaction.followup.send(
                "❌ **Not Whitelisted**\n"
                "You do not have an active license. Please contact an Admin to purchase access.",
                ephemeral=True
            )
            return

        # 2. Get the most recent active key
        key_data = active_keys[0]
        key_str  = key_data["key"]
        expires  = unix_to_readable(key_data["expires_at"])
        
        loader_str = f'script_key="{key_str}";\nloadstring(game:HttpGet("{API_BASE_URL}/loader.lua"))()'
        
        # 3. Send the private response
        embed = discord.Embed(title="🛡️ Your License Information", color=discord.Color.green())
        embed.add_field(name="Current Key", value=f"`{key_str}`", inline=False)
        embed.add_field(name="1-Line Loader", value=f"```lua\n{loader_str}```", inline=False)
        embed.add_field(name="Expires", value=expires, inline=True)
        embed.set_footer(text="Keep this key private! Never share it with others.")
        
        await interaction.followup.send(embed=embed, ephemeral=True)


# ─────────────────────────────────────────────
# Slash Commands
# ─────────────────────────────────────────────

@bot.tree.command(name="genkey", description="Generate a NEW license key (e.g., 30m, 12h, 7d, 1mo, lifetime)")
@is_staff()
async def genkey(interaction: discord.Interaction, member: discord.Member, duration: str, note: Optional[str] = None):
    # Set ephemeral=False so the customer in the ticket can see the key!
    await interaction.response.defer(ephemeral=False)
    
    result = api_post("generate", {
        "discord_id": str(member.id),
        "duration": duration,
        "note": note or f"Created by {interaction.user} via /genkey",
    })

    if "error" in result:
        await interaction.followup.send(f"[ERROR] {result['error']}", ephemeral=False)
        return

    key_data = result["key"]
    key_str  = key_data["key"]
    expires  = unix_to_readable(key_data["expires_at"])
    
    loader_str = f'script_key="{key_str}";\nloadstring(game:HttpGet("{API_BASE_URL}/loader.lua"))()'
    
    embed = discord.Embed(title="✅ License Key Generated", color=discord.Color.green(), timestamp=datetime.now(tz=timezone.utc))
    embed.add_field(name="Copy & Paste as 1-Line", value=f"```lua\n{loader_str}```", inline=False)
    embed.add_field(name="User",    value=member.mention, inline=True)
    embed.add_field(name="Expires", value=expires, inline=True)
    embed.add_field(name="Duration", value=duration, inline=True)
    
    await interaction.followup.send(embed=embed, ephemeral=False)
    await log_action(interaction, f"Generated key `{key_str}` for {member}")

@bot.tree.command(name="keyinfo", description="Lookup license key details (Private)")
@is_staff()
async def keyinfo(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    result = api_post("keyinfo", {"key": key.strip()})
    
    if "error" in result:
        await interaction.followup.send(f"[ERROR] {result['error']}", ephemeral=True)
        return

    kd = result["key"]
    embed = discord.Embed(title="🔍 Key Info", color=discord.Color.blue(), timestamp=datetime.now(tz=timezone.utc))
    embed.add_field(name="Key",        value=f"```{kd['key']}```", inline=False)
    embed.add_field(name="Status",     value=kd["status"].upper(),  inline=True)
    embed.add_field(name="User",       value=f"<@{kd['discord_id']}>", inline=True)
    embed.add_field(name="Expires",    value=unix_to_readable(kd["expires_at"]), inline=True)
    embed.add_field(name="HWID",       value=kd["hwid"] or "Not bound", inline=True)
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="revoke", description="Immediately revoke a license key (Private)")
@is_staff()
async def revoke(interaction: discord.Interaction, key: str):
    await interaction.response.defer(ephemeral=True)
    result = api_post("revoke", {"key": key.strip()})
    
    if "error" in result:
        await interaction.followup.send(f"[ERROR] {result['error']}", ephemeral=True)
        return
        
    await interaction.followup.send(f"✅ Key `{key}` has been successfully revoked.", ephemeral=True)
    await log_action(interaction, f"Revoked key `{key}`")

@bot.tree.command(name="listkeys", description="List active licenses (Private)")
@is_staff()
async def listkeys(interaction: discord.Interaction, member: Optional[discord.Member] = None):
    await interaction.response.defer(ephemeral=True)
    payload = {"discord_id": str(member.id)} if member else {}
    result = api_post("listkeys", payload)
    
    if "error" in result:
        await interaction.followup.send(f"[ERROR] {result['error']}", ephemeral=True)
        return

    keys = result["keys"]
    if not keys:
        await interaction.followup.send("No license keys found.", ephemeral=True)
        return

    lines = [f"{'🟢' if k['status']=='active' else '🔴'} `{k['key']}` | <@{k['discord_id']}>" for k in keys[:15]]
    embed = discord.Embed(title=f"🗝️ License Keys ({len(keys)})", description="\n".join(lines), color=discord.Color.blurple())
    
    await interaction.followup.send(embed=embed, ephemeral=True)

@bot.tree.command(name="setup_panel", description="Deploy the Control Panel to the current channel (Admin)")
@is_staff()
async def setup_panel(interaction: discord.Interaction):
    """Post the persistent Control Panel embed."""
    embed = discord.Embed(
        title="💠 Astrix Hub | Control Panel", 
        description=(
            "Welcome to the **Astrix Pls Donate** access panel.\n\n"
            "If you have already purchased a license, click the button below to retrieve your script and key instantly.\n\n"
            "**Note:** Your key must be active and whitelisted by an Admin."
        ),
        color=discord.Color.blue()
    )
    embed.add_field(name="📜 Get Script", value="Retrieves your unique loader and active license key.", inline=True)
    embed.set_footer(text=f"Sent by {interaction.user}", icon_url=interaction.user.display_avatar.url)
    
    view = ControlPanelView()
    await interaction.response.send_message("✅ Control Panel deployed.", ephemeral=True)
    await interaction.channel.send(embed=embed, view=view)
    await log_action(interaction, "Deployed Control Panel")

# ─────────────────────────────────────────────
# Events
# ─────────────────────────────────────────────

@bot.event
async def on_ready():
    logger.info("Bot logged in as %s", bot.user.name)
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="license keys"))

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
