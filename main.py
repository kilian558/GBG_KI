import discord
from discord.ext import commands
import asyncio
import os
import re
import json
from dotenv import load_dotenv
import aiohttp
from datetime import datetime
from discord.ui import Button, View, Modal, TextInput

# Für Spracherkennung: pip install langdetect
from langdetect import detect, DetectorFactory
from langdetect.lang_detect_exception import LangDetectException

# Reproduzierbare Ergebnisse
DetectorFactory.seed = 0

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
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN', '').strip()

if not API_KEY:
    raise ValueError("API_KEY fehlt in .env!")
if not GROK_API_KEY:
    raise ValueError("GROK_API_KEY fehlt in .env!")
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN fehlt in .env!")

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

# === SPRACH-TEXTE ===
GREETINGS = {
    'de': "Hallo! Ich helfe dir gerne. Falls du deinen exakten Ingame-Namen oder Steam-ID hast, kannst du sie hier direkt eingeben:",
    'en': "Hello! I'm here to help you. If you have your exact in-game name (with clan tag) or Steam-ID, you can enter it here:"
}

MODAL_TITLES = {
    'de': "Exakten Ingame-Namen oder Steam-ID eingeben",
    'en': "Enter exact in-game name or Steam-ID"
}

MODAL_LABELS = {
    'de': "Name (mit Clan-Tag) ODER Steam-ID",
    'en': "Name (with clan tag) OR Steam-ID"
}

MODAL_PLACEHOLDERS = {
    'de': "z. B. ℧ | Narcotic ODER 76561198986670442",
    'en': "e.g. ℧ | Narcotic OR 76561198986670442"
}

BUTTON_LABELS = {
    'de': "Exakten Namen oder Steam-ID eingeben",
    'en': "Enter exact name or Steam-ID"
}

FAIL_MESSAGES = {
    'de': "❌ Leider keinen Player zu '{}' gefunden. Bitte exakten Namen (mit Clan-Tag) oder Steam-ID angeben:",
    'en': "❌ Unfortunately no player found for '{}'. Please enter exact name (with clan tag) or Steam-ID:"
}

SUCCESS_MESSAGES = {
    'de': "✅ Danke! Player-Info erfolgreich geladen.",
    'en': "✅ Thanks! Player info successfully loaded."
}

AUTO_DETECT_SUCCESS = {
    'de': "✅ Player-ID/Name erkannt und Info geladen!",
    'en': "✅ Player ID/name detected and info loaded!"
}

ONLY_OWNER_MESSAGES = {
    'de': "Nur der Ticket-Owner darf das ausfüllen!",
    'en': "Only the ticket owner can fill this out!"
}

PROCESSING_MESSAGES = {
    'de': "Danke! Verarbeite jetzt '{}'...",
    'en': "Thanks! Processing '{}' now..."
}

# === HILFSFUNKTIONEN ===
def has_admin_role(member: discord.Member) -> bool:
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)

def extract_player_id(text: str) -> str | None:
    match = re.search(r'(7656119\d{10}|[a-f0-9]{32})', text)
    return match.group(0) if match else None

def extract_ingame_name(text: str) -> str | None:
    keyword_pattern = r'(?:name|ingame|bin|heiße|mein name|spiele als|als |ich bin|Name ist|der Name|Name:)[\s:]*([^\n\r<@!&]{4,30})'
    match = re.search(keyword_pattern, text, re.IGNORECASE)
    if match:
        name = match.group(1).strip()
        if len(name) >= 4:
            return name
    fallback_pattern = r'\b([A-Za-z0-9_\-\.\[\]\(\){} ]{5,30})\b'
    matches = re.finditer(fallback_pattern, text)
    for m in matches:
        candidate = m.group(1).strip()
        if len(candidate) >= 5 and re.search(r'[A-Z0-9\[\]]', candidate) and not candidate.lower() in ["hallo", "hi", "hey", "hallooo", "moinc", "heyo"]:
            return candidate
    return None

def detect_language(text: str) -> str:
    if not text.strip():
        return 'de'
    try:
        lang = detect(text)
        if lang in ['de', 'en']:
            return lang
    except LangDetectException:
        pass
    return 'de'  # Default: Deutsch

# === RCON API FUNKTIONEN ===
async def api_clear_temp_ban(player_id: str, channel_id: int):
    if not player_id or not http_session:
        return False
    try:
        async with http_session.post(
                f"{API_BASE_URL}/remove_temp_ban",
                headers=API_HEADERS,
                json={"player_id": player_id}
        ) as resp:
            resp_text = await resp.text()
            await log_debug(f"Temp-Clear Endpoint remove_temp_ban – Status {resp.status} | Response: {resp_text[:200]}", channel_id)
            if resp.status == 200:
                result_json = await resp.json()
                result = result_json.get("result")
                success = result in (True, None) or "success" in str(result).lower()
                status = "erfolgreich" if success else "ohne Effekt (kein Temp-Ban)"
                await log_debug(f"Temp-Ban-Clear für {player_id}: {status}", channel_id)
                return success
    except Exception as e:
        await log_debug(f"Temp-Clear Exception: {e}", channel_id)
    return False

async def api_clear_full_bans(player_id: str, channel_id: int):
    if not player_id or not http_session:
        return False
    success = False
    endpoints = ["remove_temp_ban", "unban", "remove_perma_ban", "unblacklist_player"]
    for endpoint in endpoints:
        try:
            async with http_session.post(
                    f"{API_BASE_URL}/{endpoint}",
                    headers=API_HEADERS,
                    json={"player_id": player_id}
            ) as resp:
                resp_text = await resp.text()
                await log_debug(f"Full-Clear Endpoint {endpoint} – Status {resp.status} | Response: {resp_text[:200]}", channel_id)
                if resp.status == 200:
                    result_json = await resp.json()
                    result = result_json.get("result")
                    if result in (True, None) or "success" in str(result).lower():
                        success = True
        except Exception as e:
            await log_debug(f"Full-Clear {endpoint} Exception: {e}", channel_id)
    status = "erfolgreich (mind. ein Ban/Blacklist entfernt)" if success else "ohne Effekt"
    await log_debug(f"Full Ban/Blacklist-Clear für {player_id}: {status}", channel_id)
    return success

# === ADMIN VIEW ===
class TicketAdminView(View):
    def __init__(self, player_id: str, ticket_channel: discord.TextChannel, channel_id: int):
        super().__init__(timeout=None)
        self.player_id = player_id
        self.ticket_channel = ticket_channel
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not any(role.name == ADMIN_ROLE_NAME for role in interaction.user.roles):
            await interaction.response.send_message("Nur Admins dürfen diese Buttons benutzen!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Alle Bans/Blacklists entfernen (inkl. Perma)", style=discord.ButtonStyle.green)
    async def clear_ban(self, interaction: discord.Interaction, button: Button):
        if not self.player_id:
            await interaction.response.send_message("Keine ID gefunden – manuell prüfen.", ephemeral=True)
            return
        await interaction.response.send_message(f"Full Ban/Blacklist-Clear für {self.player_id} läuft...", ephemeral=True)
        success = await api_clear_full_bans(self.player_id, self.channel_id)
        status = "erfolgreich" if success else "ohne Effekt"
        await interaction.followup.send(f"Full Ban/Blacklist-Clear {status}.", ephemeral=True)

    @discord.ui.button(label="Ticket-Infos anzeigen", style=discord.ButtonStyle.primary)
    async def show_infos(self, interaction: discord.Interaction, button: Button):
        ticket = tickets.get(self.channel_id)
        if not ticket:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        summary = "Ticket-Konversation (letzte 30 Nachrichten):\n\n"
        history = ticket.history[-30:]
        for msg in history:
            role = msg["role"]
            content = msg["content"] if isinstance(msg["content"], str) else "[Nachricht mit Bild/Anhang]"
            prefix = "User" if role == "user" else "Bot" if role == "assistant" else "System"
            summary += f"{prefix}: {content}\n\n"
        try:
            await interaction.user.send(f"Infos zum Ticket {self.ticket_channel.mention}:\n{summary}")
            await interaction.response.send_message("Infos per DM gesendet!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"DM blockiert – Infos hier (nur du siehst's):\n{summary}", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Fehler beim Senden der Infos: {str(e)}", ephemeral=True)

# === PLAYER-SUCHE & INFO ===
async def search_and_set_best_player_id(channel_id: int, name: str = None) -> bool:
    ticket = tickets.get(channel_id)
    if not ticket or not name or not http_session:
        return False
    try:
        async with http_session.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params={
                    "player_name": name,
                    "exact_name_match": "False",
                    "ignore_accent": "True",
                    "page_size": 20
                }
        ) as resp:
            if resp.status != 200:
                await log_debug(f"Name-Suche Status {resp.status}", channel_id)
                return False
            data = await resp.json()
            result = data.get("result")
            players = result.get("players", []) if isinstance(result, dict) else []
            if not players:
                await log_debug("Keine Players gefunden", channel_id)
                ticket.history.append({
                    "role": "system",
                    "content": f"Name-Suche für '{name}' hat keinen passenden Player gefunden."
                })
                return False
            def get_max_last_seen(player):
                names = player.get("names", [])
                timestamps = [datetime.fromisoformat(n.get("last_seen")).timestamp() for n in names if n.get("last_seen")]
                return max(timestamps) if timestamps else 0
            players_sorted = sorted(players, key=get_max_last_seen, reverse=True)
            best_id = players_sorted[0].get("player_id")
            if best_id and best_id != ticket.player_id:
                ticket.player_id = best_id
                await log_debug(f"Neue beste ID {best_id} (von Name '{name}')", channel_id)
                await update_escalation_embed(channel_id)
                ticket.history.append({"role": "system", "content": f"Beste Player-ID zu Name '{name}' gefunden: {best_id}"})
                return True
    except Exception as e:
        await log_debug(f"Player-Suche Exception: {e}", channel_id)
    return False

async def add_player_info_to_history(channel_id: int):
    ticket = tickets.get(channel_id)
    if not ticket or not ticket.player_id or ticket.player_info_added or not http_session:
        return
    try:
        async with http_session.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params={"player_id": ticket.player_id, "page_size": 30}
        ) as resp:
            if resp.status != 200:
                await log_debug(f"Player-Info Abruf Status {resp.status}", channel_id)
                return
            data = await resp.json()
            raw_result = data.get("result")
            punishments = raw_result if isinstance(raw_result, list) else raw_result.get("punishments", []) or []
            limited = punishments[:15]
            full_summary = f"Spieler-Info für ID {ticket.player_id}: {json.dumps(limited, ensure_ascii=False, default=str)}"
            ticket.history.append({"role": "system", "content": full_summary})
            ticket.player_info_added = True
    except Exception as e:
        await log_debug(f"Player-Info Exception: {e}", channel_id)

# === ESCALATION EMBED ===
async def update_escalation_embed(channel_id: int, summary: str = None):
    ticket = tickets.get(channel_id)
    if not ticket:
        return
    admin_channel = bot.get_channel(ADMIN_SUMMARY_CHANNEL_ID)
    if not admin_channel:
        return
    channel = bot.get_channel(channel_id)
    if not channel:
        return
    embed = discord.Embed(title="Ticket Eskalation", description=summary or "Warte auf Infos...", color=0xffa500)
    embed.add_field(name="Ticket", value=channel.mention)
    embed.add_field(name="Link", value=channel.jump_url)
    view = None
    if ticket.player_id:
        embed.add_field(name="Player-ID", value=ticket.player_id, inline=False)
        view = TicketAdminView(ticket.player_id, channel, channel_id)
    if ticket.escalation_message:
        await ticket.escalation_message.edit(embed=embed, view=view)
    else:
        msg = await admin_channel.send(embed=embed, view=view)
        ticket.escalation_message = msg

# === TICKET-KLASSE ===
class Ticket:
    def __init__(self, channel_id: int, owner: discord.Member):
        self.channel_id = channel_id
        self.owner = owner
        self.history = INITIAL_HISTORY.copy()
        self.closed = False
        self.player_id = ""
        self.player_info_added = False
        self.admin_active = False
        self.language = 'de'  # Default
        self.pending_response_task: asyncio.Task | None = None
        self.admin_timeout_task: asyncio.Task | None = None
        self.name_request_message: discord.Message | None = None
        self.escalation_message: discord.Message | None = None

tickets: dict[int, Ticket] = {}

# === PROMPT LADEN ===
PROMPT_FILE = 'prompts_de.json'
if not os.path.exists(PROMPT_FILE):
    raise FileNotFoundError(f"Die Datei '{PROMPT_FILE}' wurde nicht gefunden.")
with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
    prompt_data = json.load(f)
INITIAL_HISTORY = [{"role": "system", "content": prompt_data}] if isinstance(prompt_data, str) else prompt_data

# === LOGGING ===
async def log_debug(msg: str, channel_id: int = None):
    full_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Ticket {channel_id or 'Global'}] {msg}"
    print(full_msg)
    channel = bot.get_channel(DEBUG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(f"[DEBUG] {full_msg}")
        except:
            pass

# === HTTP SESSION ===
http_session: aiohttp.ClientSession | None = None
async def create_http_session():
    global http_session
    connector = aiohttp.TCPConnector(ssl=False)
    timeout = aiohttp.ClientTimeout(total=90)
    http_session = aiohttp.ClientSession(connector=connector, timeout=timeout)

async def close_http_session():
    global http_session
    if http_session:
        await http_session.close()
        http_session = None

# === ADMIN TIMEOUT ===
async def reset_admin_active(ticket: Ticket, delay: int = 1800):
    await asyncio.sleep(delay)
    ticket.admin_active = False
    await log_debug("Admin-active Timeout abgelaufen", ticket.channel_id)

# === HISTORY TRIM ===
MAX_NON_SYSTEM_MESSAGES = 30
def trim_history(ticket: Ticket):
    system_msgs = [m for m in ticket.history if m["role"] == "system"]
    other_msgs = [m for m in ticket.history if m["role"] != "system"][-MAX_NON_SYSTEM_MESSAGES:]
    ticket.history = system_msgs + other_msgs

# === DEBOUNCE ===
async def debounced_ki_response(channel: discord.TextChannel, ticket: Ticket):
    await asyncio.sleep(5)
    ticket.pending_response_task = None
    await send_ki_response(channel, ticket)

# === MODAL & VIEW (multilingual) ===
class IngameNameOrIdModal(Modal):
    def __init__(self, channel_id: int, language: str):
        super().__init__(title=MODAL_TITLES.get(language, MODAL_TITLES['de']))
        self.channel_id = channel_id
        self.language = language
        self.input = TextInput(
            label=MODAL_LABELS.get(language, MODAL_LABELS['de']),
            placeholder=MODAL_PLACEHOLDERS.get(language, MODAL_PLACEHOLDERS['de']),
            style=discord.TextStyle.short,
            min_length=4,
            max_length=50
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        ticket = tickets.get(self.channel_id)
        if not ticket or interaction.user != ticket.owner:
            await interaction.response.send_message(ONLY_OWNER_MESSAGES.get(self.language, ONLY_OWNER_MESSAGES['de']), ephemeral=True)
            return

        user_input = self.input.value.strip()
        await interaction.response.send_message(PROCESSING_MESSAGES.get(self.language, PROCESSING_MESSAGES['de']).format(user_input), ephemeral=False)

        found = False
        possible_id = extract_player_id(user_input)
        if possible_id and possible_id != ticket.player_id:
            ticket.player_id = possible_id
            await update_escalation_embed(self.channel_id)
            await add_player_info_to_history(self.channel_id)
            found = True
            if ticket.name_request_message:
                await ticket.name_request_message.edit(content=SUCCESS_MESSAGES.get(self.language, SUCCESS_MESSAGES['de']), view=None)

        if not found:
            found = await search_and_set_best_player_id(self.channel_id, name=user_input)
            if found:
                await add_player_info_to_history(self.channel_id)
                if ticket.name_request_message:
                    await ticket.name_request_message.edit(content=SUCCESS_MESSAGES.get(self.language, SUCCESS_MESSAGES['de']), view=None)

        if not found:
            content = FAIL_MESSAGES.get(self.language, FAIL_MESSAGES['de']).format(user_input)
            view = NameRequestView(self.channel_id, self.language)
            if ticket.name_request_message:
                await ticket.name_request_message.edit(content=content, view=view)
            else:
                msg = await interaction.channel.send(content, view=view)
                ticket.name_request_message = msg

        if ticket.pending_response_task:
            ticket.pending_response_task.cancel()
        ticket.pending_response_task = asyncio.create_task(debounced_ki_response(interaction.channel, ticket))

class NameRequestView(View):
    def __init__(self, channel_id: int, language: str):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.language = language

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        ticket = tickets.get(self.channel_id)
        return ticket and interaction.user == ticket.owner

    @discord.ui.button(label=lambda self: BUTTON_LABELS.get(self.language, BUTTON_LABELS['de']), style=discord.ButtonStyle.primary)
    async def request_input(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(IngameNameOrIdModal(self.channel_id, self.language))

# === FEEDBACK ===
async def send_feedback_message(channel: discord.TextChannel):
    try:
        msg = await channel.send("Danke für dein Ticket! 😊 War alles okay mit dem Support?")
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    except Exception as e:
        await log_debug(f"Feedback-Nachricht Fehler: {e}", channel.id)

@bot.event
async def on_reaction_add(reaction, user):
    if user.bot or reaction.message.author != bot.user or "War alles okay mit dem Support?" not in reaction.message.content:
        return
    ticket = tickets.get(reaction.message.channel.id)
    if ticket and ticket.closed:
        feedback = "👍" if str(reaction.emoji) == "👍" else "👎"
        await log_debug(f"Feedback von {user}: {feedback}", reaction.message.channel.id)

# === KI-ANTWORT ===
async def send_ki_response(channel: discord.TextChannel, ticket: Ticket):
    if ticket.closed or ticket.admin_active or not http_session:
        return
    trim_history(ticket)
    messages_for_api = [{"role": m["role"], "content": m["content"]} for m in ticket.history]
    payload = {"model": "grok-4", "messages": messages_for_api, "max_tokens": 1024, "temperature": 0.8}
    bot_reply = None
    for attempt in range(3):
        try:
            async with http_session.post("https://api.x.ai/v1/chat/completions", json=payload, headers=GROK_HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bot_reply = data["choices"][0]["message"]["content"].strip()
                    break
                elif resp.status == 429:
                    await asyncio.sleep(10 * (attempt + 1))
        except Exception as e:
            await log_debug(f"KI-Exception: {e}", ticket.channel_id)
    if not bot_reply:
        await channel.send("Technische Probleme mit der KI – Admin schaut drauf. Erzähl weiter!")
        return
    await channel.send(bot_reply)
    if bot_reply.strip() == "CLOSE TICKET:":
        ticket.closed = True
        await channel.send("Das Ticket wird nun geschlossen.")
        await send_feedback_message(channel)
        if ticket.name_request_message:
            await ticket.name_request_message.edit(content="Ticket geschlossen.", view=None)
        if ticket.pending_response_task:
            ticket.pending_response_task.cancel()
        if ticket.admin_timeout_task:
            ticket.admin_timeout_task.cancel()
        del tickets[ticket.channel_id]
        return
    if ticket.player_id and any(word in bot_reply.lower() for word in ["temp", "tk", "votekick", "teamkill", "unban", "clear"]):
        await api_clear_temp_ban(ticket.player_id, ticket.channel_id)
        await channel.send("Temp-Ban-Clear versucht!")
    if any(word in bot_reply.lower() for word in ["perma", "blacklist", "cheat", "admin"]):
        await update_escalation_embed(ticket.channel_id, summary="Komplexer Fall")
    ticket.history.append({"role": "assistant", "content": bot_reply})

# === EVENTS ===
@bot.event
async def on_ready():
    await create_http_session()
    bot.add_view(NameRequestView(0, 'de'))
    bot.add_view(NameRequestView(0, 'en'))
    bot.add_view(TicketAdminView("", None, 0))
    await log_debug("Bot online – Multilingual + Warte auf erste Nachricht")

@bot.event
async def on_disconnect():
    await close_http_session()

@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.category and channel.category.name.lower() in [c.lower() for c in ACTIVE_TICKET_CATEGORIES]:
        await asyncio.sleep(8)
        overwrites_members = [t for t in channel.overwrites if isinstance(t, discord.Member) and not t.bot]
        if overwrites_members and channel.permissions_for(overwrites_members[0]).view_channel:
            owner = overwrites_members[0]
            tickets[channel.id] = Ticket(channel.id, owner)
            await log_debug(f"Neues Ticket {channel.id} – Warte auf erste Nachricht", channel.id)

@bot.event
async def on_message(message):
    if message.author.bot or not isinstance(message.channel, discord.TextChannel):
        return
    if message.channel.category and message.channel.category.name.lower() in [c.lower() for c in ACTIVE_TICKET_CATEGORIES]:
        channel_id = message.channel.id
        ticket = tickets.get(channel_id)
        if not ticket:
            ticket = Ticket(channel_id, message.author)
            tickets[channel_id] = ticket
            await log_debug(f"Ticket {channel_id} – Owner aus erster Nachricht", channel_id)

        if isinstance(message.author, discord.Member) and has_admin_role(message.author):
            ticket.admin_active = True
            if ticket.admin_timeout_task:
                ticket.admin_timeout_task.cancel()
            ticket.admin_timeout_task = asyncio.create_task(reset_admin_active(ticket))
            ticket.history.append({"role": "user", "content": f"[Admin {message.author}]: {message.content}"})
            return

        if message.author != ticket.owner:
            return

        # Erste User-Nachricht?
        is_first_message = len([m for m in ticket.history if m["role"] == "user"]) == 0

        user_content = []
        if message.content:
            user_content.append({"type": "text", "text": message.content})
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                user_content.append({"type": "image_url", "image_url": {"url": att.url}})
        if not message.content and user_content:
            user_content.insert(0, {"type": "text", "text": "Screenshot hochgeladen:"})
        ticket.history.append({"role": "user", "content": user_content or message.content})

        if is_first_message:
            ticket.language = detect_language(message.content or "")
            await log_debug(f"Sprache erkannt: {ticket.language}", channel_id)
            ticket.history.append({
                "role": "system",
                "content": f"User spricht {'Deutsch' if ticket.language == 'de' else 'English'}. Antworte immer auf dieser Sprache."
            })
            view = NameRequestView(channel_id, ticket.language)
            msg = await message.channel.send(GREETINGS.get(ticket.language, GREETINGS['de']), view=view)
            ticket.name_request_message = msg

        id_changed = False
        direct_id = extract_player_id(message.content or "")
        if direct_id and direct_id != ticket.player_id:
            ticket.player_id = direct_id
            id_changed = True

        name = extract_ingame_name(message.content or "")
        if name and await search_and_set_best_player_id(channel_id, name):
            id_changed = True

        if id_changed:
            await update_escalation_embed(channel_id)
            ticket.player_info_added = False
            await add_player_info_to_history(channel_id)
            if ticket.name_request_message:
                await ticket.name_request_message.edit(content=AUTO_DETECT_SUCCESS.get(ticket.language, AUTO_DETECT_SUCCESS['de']), view=None)

        if not ticket.player_id and not ticket.name_request_message and not is_first_message:
            view = NameRequestView(channel_id, ticket.language)
            msg = await message.channel.send(GREETINGS.get(ticket.language, GREETINGS['de']), view=view)
            ticket.name_request_message = msg

        if ticket.pending_response_task:
            ticket.pending_response_task.cancel()
        ticket.pending_response_task = asyncio.create_task(debounced_ki_response(message.channel, ticket))

    await bot.process_commands(message)

bot.run(DISCORD_TOKEN)