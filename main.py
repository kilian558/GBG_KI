import discord
from discord.ext import commands
import asyncio
import os
import re
import json
from dotenv import load_dotenv
from collections import defaultdict
import requests
import urllib3
from datetime import datetime
from discord.ui import Button, View
import signal  # Für graceful shutdown

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === KONFIG ===
API_BASE_URL = os.getenv('API_BASE_URL', 'https://gbg-hll.com:64302/api/').rstrip('/')
API_KEY = os.getenv('API_KEY', '').strip()
GROK_API_KEY = os.getenv('GROK_API_KEY', '').strip()

if not API_KEY:
    raise ValueError("API_KEY fehlt in .env!")
if not GROK_API_KEY:
    raise ValueError("GROK_API_KEY fehlt in .env!")

API_HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

GROK_HEADERS = {
    "Authorization": f"Bearer {GROK_API_KEY}",
    "Content-Type": "application/json"
}

ACTIVE_TICKET_CATEGORIES = ["Tickets", "Beanspruchte Tickets"]
ADMIN_SUMMARY_CHANNEL_ID = 1455199315713851686
DEBUG_CHANNEL_ID = 1455236964981670121
ADMIN_ROLE_NAME = "HLL Admin"

# Ticket-States
ticket_owner_cache = {}
ticket_history = defaultdict(list)
ticket_closed = defaultdict(bool)
ticket_player_id = defaultdict(str)
ticket_player_info_added = defaultdict(bool)
admin_active = defaultdict(bool)
ticket_escalation_message = defaultdict(lambda: None)

# === PROMPT AUS DATEI LADEN ===
PROMPT_FILE = 'prompts_de.json'

if not os.path.exists(PROMPT_FILE):
    raise FileNotFoundError(f"Die Datei '{PROMPT_FILE}' wurde nicht gefunden.")

try:
    with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
        data = f.read().strip()
        if not data:
            raise ValueError("Die Datei ist leer.")
        prompt_data = json.loads(data)

    if isinstance(prompt_data, str):
        INITIAL_HISTORY = [{"role": "system", "content": prompt_data}]
    elif isinstance(prompt_data, dict):
        INITIAL_HISTORY = [prompt_data]
    elif isinstance(prompt_data, list):
        INITIAL_HISTORY = prompt_data
    else:
        raise ValueError("Ungültiges Format in prompts_de.json")

    print(f"Prompt erfolgreich aus '{PROMPT_FILE}' geladen.")
except Exception as e:
    raise ValueError(f"Fehler beim Laden von '{PROMPT_FILE}': {e}")


# === LOGGING ===
async def log_debug(msg: str, channel_id: int = None):
    full_msg = f"[Ticket {channel_id or 'Global'}] {msg}"
    print(full_msg)
    channel = bot.get_channel(DEBUG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(f"[DEBUG] {full_msg}")
        except:
            pass


# === RCON API: BAN/BLACKLIST-CLEAR ===
async def api_clear_ban(player_id: str, channel_id: int):
    if not player_id:
        return False

    success = False
    endpoints = [
        "remove_temp_ban",
        "unban",
        "remove_perma_ban",
        "unblacklist_player"  # Blacklist-Remove
    ]
    for endpoint in endpoints:
        try:
            resp = requests.post(
                f"{API_BASE_URL}/{endpoint}",
                headers=API_HEADERS,
                json={"player_id": player_id},
                verify=False
            )
            await log_debug(f"Endpoint {endpoint} – Status {resp.status_code}", channel_id)
            if resp.status_code == 200:
                result = resp.json().get("result")
                if result in (True, None):
                    success = True
                    break
        except Exception as e:
            await log_debug(f"{endpoint} Fehler: {e}", channel_id)

    await log_debug(f"Ban/Blacklist-Clear für {player_id}: {'Erfolg' if success else 'ohne Effekt'}", channel_id)
    return success


# === ADMIN VIEW MIT UNBAN-BUTTON ===
class TicketAdminView(View):
    def __init__(self, player_id: str, ticket_channel: discord.TextChannel, channel_id: int):
        super().__init__(timeout=None)
        self.player_id = player_id
        self.ticket_channel = ticket_channel
        self.channel_id = channel_id

    @discord.ui.button(label="Alle Bans/Blacklists entfernen", style=discord.ButtonStyle.green)
    async def clear_ban(self, interaction: discord.Interaction, button: Button):
        if not self.player_id:
            await interaction.response.send_message("Keine ID gefunden – manuell prüfen.", ephemeral=True)
            return

        await interaction.response.send_message(f"Ban/Blacklist-Clear für {self.player_id} läuft...", ephemeral=True)
        success = await api_clear_ban(self.player_id, self.channel_id)
        status = "erfolgreich" if success else "ohne Effekt"
        await interaction.followup.send(f"Ban/Blacklist-Clear {status}.", ephemeral=True)

    @discord.ui.button(label="Ticket-Infos anzeigen", style=discord.ButtonStyle.primary)
    async def show_infos(self, interaction: discord.Interaction, button: Button):
        summary = "Ticket-Konversation (letzte 20):\n\n"
        for msg in ticket_history[self.channel_id][-20:]:
            role = msg["role"]
            content = msg["content"]
            prefix = "User" if role == "user" else "Bot"
            summary += f"{prefix}: {content}\n\n"
        try:
            await interaction.user.send(f"Infos zum Ticket {self.ticket_channel.mention}:\n{summary}")
            await interaction.response.send_message("Infos per DM gesendet!", ephemeral=True)
        except:
            await interaction.response.send_message("Konnte DM nicht senden.", ephemeral=True)


# === PLAYER-SUCHE (zuletzt aktiv) ===
async def search_and_set_best_player_id(channel_id: int, name: str = None):
    if not name:
        return

    try:
        resp = requests.get(
            f"{API_BASE_URL}/get_players_history",
            headers=API_HEADERS,
            params={
                "player_name": name,
                "exact_name_match": "False",
                "ignore_accent": "True",
                "page_size": 20
            },
            verify=False
        )
        if resp.status_code != 200:
            await log_debug(f"Name-Suche Status {resp.status_code}", channel_id)
            return

        data = resp.json()
        players = data.get("result", {}).get("players", [])
        if not isinstance(players, list) or not players:
            await log_debug("Keine Players gefunden", channel_id)
            return

        def get_max_last_seen(player):
            names = player.get("names", [])
            timestamps = []
            for n in names:
                ts_str = n.get("last_seen")
                if ts_str:
                    try:
                        timestamps.append(datetime.fromisoformat(ts_str).timestamp())
                    except:
                        pass
            return max(timestamps) if timestamps else 0

        players_sorted = sorted(players, key=get_max_last_seen, reverse=True)

        if players_sorted:
            best = players_sorted[0]
            best_id = best.get("player_id")
            if best_id:
                old_id = ticket_player_id[channel_id]
                if best_id != old_id:
                    ticket_player_id[channel_id] = best_id
                    await log_debug(f"Neue beste ID {best_id} (von Name '{name}') – vorher {old_id}", channel_id)
                    await update_escalation_embed(channel_id)

    except Exception as e:
        await log_debug(f"Player-Suche Exception: {e}", channel_id)


async def add_player_info_to_history(channel_id: int):
    player_id = ticket_player_id[channel_id]
    if not player_id or ticket_player_info_added[channel_id]:
        return

    try:
        resp = requests.get(
            f"{API_BASE_URL}/get_players_history",
            headers=API_HEADERS,
            params={"player_id": player_id, "page_size": 20},
            verify=False
        )
        if resp.status_code == 200:
            data = resp.json()
            info = data.get("result", [])
            if isinstance(info, list) and info:
                limited = info[:10]
                summary = f"Spieler-Info (ID {player_id}): Letzte Aktivitäten/Punishments: {json.dumps(limited, ensure_ascii=False, default=str)}"
            else:
                summary = f"Spieler-Info (ID {player_id}): Keine Daten verfügbar."
            ticket_history[channel_id].append({"role": "system", "content": summary})
            ticket_player_info_added[channel_id] = True
            await log_debug("Player-Info zur KI-History hinzugefügt", channel_id)
    except Exception as e:
        await log_debug(f"Player-Info Abruf Exception: {e}", channel_id)


# === EMBED AKTUALISIEREN ===
async def update_escalation_embed(channel_id: int, summary: str = None):
    admin_channel = bot.get_channel(ADMIN_SUMMARY_CHANNEL_ID)
    if not admin_channel:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    description = summary or "Warte auf Infos/ID vom User..."
    embed = discord.Embed(
        title="Ticket Eskalation – Alle Infos vorhanden",
        description=description,
        color=0xffa500
    )
    embed.add_field(name="Ticket", value=channel.mention)
    embed.add_field(name="Link", value=channel.jump_url)

    player_id = ticket_player_id[channel_id]
    view = None
    if player_id:
        embed.add_field(name="Player-ID", value=player_id, inline=False)
        view = TicketAdminView(player_id, channel, channel_id)
        try:
            resp = requests.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params={"player_id": player_id, "page_size": 10},
                verify=False
            )
            if resp.status_code == 200:
                data = resp.json()
                punishments = data.get("result", []) or []
                if isinstance(punishments, list) and punishments:
                    pun_str = "\n".join(
                        [f"{p.get('action', 'Unknown')} am {p.get('timestamp', 'N/A')}" for p in punishments[:5]])
                    embed.add_field(name="Letzte Punishments", value=pun_str or "Keine", inline=False)
        except Exception as e:
            await log_debug(f"Eskalation Player-Info Fehler: {e}", channel_id)

    msg = ticket_escalation_message[channel_id]
    if msg:
        await msg.edit(embed=embed, view=view)
    else:
        msg = await admin_channel.send(embed=embed, view=view)
        ticket_escalation_message[channel_id] = msg


# === ID & NAME ERKENNEN ===
def extract_player_id(text: str) -> str | None:
    match = re.search(r'(7656119\d{10}|[a-f0-9]{32})', text)
    return match.group(0) if match else None


def extract_ingame_name(text: str) -> str | None:
    patterns = [
        r'(?:name|ingame|bin|heiße|mein name|spiele als|als |ich bin|Name ist|der Name|Name:)[\s:]*([^\n\r<@!&]+)',
        r'([℧\w\.\-\_|\[\](){} ]{4,30})'
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            name = match.group(1).strip()
            if len(name) >= 4:
                return name
    return None


def has_admin_role(member: discord.Member) -> bool:
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)


# === KI-ANTWORT ===
async def send_ki_response(channel: discord.TextChannel, channel_id: int):
    if ticket_closed[channel_id] or admin_active[channel_id]:
        return

    history = ticket_history[channel_id]

    try:
        payload = {
            "model": "grok-4-1-fast-reasoning",
            "messages": history,
            "max_tokens": 200,
            "temperature": 0.8
        }
        response = requests.post("https://api.x.ai/v1/chat/completions", json=payload, headers=GROK_HEADERS, timeout=30)
        if response.status_code != 200:
            await log_debug(f"KI-API Fehler: {response.status_code}", channel_id)
            return

        bot_reply = response.json()["choices"][0]["message"]["content"].strip()

        user_reply = bot_reply
        admin_summary = ""
        do_temp_unban = False

        if "**AUTO_UNBAN:**" in bot_reply:
            parts = bot_reply.split("**AUTO_UNBAN:**", 1)
            user_reply = parts[0].strip()
            do_temp_unban = True

        if "**CLOSE TICKET:**" in bot_reply:
            parts = bot_reply.split("**CLOSE TICKET:**", 1)
            user_reply = parts[0].strip()
            ticket_closed[channel_id] = True

        if "**ZUSAMMENFASSUNG FÜR ADMINS:**" in bot_reply:
            parts = bot_reply.split("**ZUSAMMENFASSUNG FÜR ADMINS:**", 1)
            user_reply = parts[0].strip()
            admin_summary = parts[1].strip() if len(parts) > 1 else ""

        if user_reply:
            await channel.send(user_reply)

        if do_temp_unban:
            player_id = ticket_player_id[channel_id]
            if player_id:
                await api_clear_temp_ban(player_id, channel_id)

        if admin_summary:
            await update_escalation_embed(channel_id, summary=admin_summary)

        ticket_history[channel_id].append({"role": "assistant", "content": bot_reply})

    except Exception as e:
        await log_debug(f"KI-Exception: {e}", channel_id)


# === FEEDBACK NACH CLOSE ===
async def send_feedback_message(channel: discord.TextChannel):
    try:
        msg = await channel.send("Danke für dein Ticket! 😊 War alles okay mit dem Support?")
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    except Exception as e:
        await log_debug(f"Feedback-Nachricht Fehler: {e}", channel.id)


@bot.event
async def on_reaction_add(reaction, user):
    if user.bot:
        return

    msg = reaction.message
    if msg.author == bot.user and "War alles okay mit dem Support?" in msg.content:
        channel_id = msg.channel.id
        if ticket_closed[channel_id]:
            feedback = "👍" if str(reaction.emoji) == "👍" else "👎"
            await log_debug(f"Feedback von {user} in Ticket {channel_id}: {feedback}", channel_id)


# === GRACEFUL SHUTDOWN für Render ===
async def shutdown():
    await log_debug("Bot shutdown – Graceful exit")
    await bot.close()


def handle_sigterm(*args):
    asyncio.create_task(shutdown())


signal.signal(signal.SIGTERM, handle_sigterm)


@bot.event
async def on_ready():
    await log_debug("Bot online – bereit für Tickets")


@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.category and channel.category.name.lower() in [c.lower() for
                                                                                                           c in
                                                                                                           ACTIVE_TICKET_CATEGORIES]:
        await asyncio.sleep(5)
        owner = next((o for o in channel.overwrites if
                      isinstance(o, discord.Member) and channel.permissions_for(o).view_channel), None)
        if owner:
            ticket_owner_cache[channel.id] = owner
            ticket_history[channel.id] = INITIAL_HISTORY.copy()
            ticket_closed[channel.id] = False
            ticket_player_id[channel.id] = ""
            ticket_player_info_added[channel.id] = False
            admin_active[channel.id] = False
            await log_debug(f"Neues Ticket {channel.id} – Owner: {owner}")
        else:
            await log_debug(f"Neues Ticket {channel.id} – Kein Owner gefunden")


@bot.event
async def on_message(message):
    if message.author.bot:
        return

    if not isinstance(message.channel, discord.TextChannel):
        return

    if message.channel.category and message.channel.category.name.lower() in [c.lower() for c in
                                                                              ACTIVE_TICKET_CATEGORIES]:
        channel_id = message.channel.id
        owner = ticket_owner_cache.get(channel_id)

        if isinstance(message.author, discord.Member) and has_admin_role(message.author):
            admin_active[channel_id] = True
            await log_debug(f"Admin {message.author} interveniert – KI pausiert", channel_id)
            return

        if not owner or message.author != owner:
            return

        await log_debug(f"Owner-Nachricht in Ticket {channel_id}: {message.content[:100]}", channel_id)

        direct_id = extract_player_id(message.content)
        ingame_name = extract_ingame_name(message.content)

        id_changed = False
        if direct_id and direct_id != ticket_player_id[channel_id]:
            ticket_player_id[channel_id] = direct_id
            id_changed = True

        if ingame_name:
            await search_and_set_best_player_id(channel_id, name=ingame_name)
            if ticket_player_id[channel_id]:
                id_changed = True

        if id_changed:
            await update_escalation_embed(channel_id)

        await add_player_info_to_history(channel_id)

        ticket_history[channel_id].append({"role": "user", "content": message.content})
        await send_ki_response(message.channel, channel_id)

        if ticket_closed[channel_id]:
            await send_feedback_message(message.channel)

    await bot.process_commands(message)


bot.run(os.getenv("DISCORD_TOKEN"))