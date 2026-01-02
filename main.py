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

# === EINFACHE SPRACHERKENNUNG (keine externe Lib) ===
def detect_language(text: str) -> str:
    if not text.strip():
        return 'de'
    text_lower = text.lower()
    english_words = ["hello", "hi", "hey", "help", "please", "thanks", "thank you", "sorry", "ban", "kick", "unban", "teamkill", "votekick", "problem", "issue", "was", "banned", "why", "kicked"]
    german_words = ["hallo", "hi", "hey", "hilfe", "bitte", "danke", "entschuldigung", "gebannt", "kick", "warum", "teamkill"]

    en_count = sum(word in text_lower for word in english_words)
    de_count = sum(word in text_lower for word in german_words)

    if en_count > de_count and en_count > 0:
        return 'en'
    return 'de'

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
        if len(candidate) >= 5 and re.search(r'[A-Z0-9\[\]]', candidate) and candidate.lower() not in ["hallo", "hi", "hey", "hallooo", "moinc", "heyo"]:
            return candidate
    return None

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
            await log_debug(f"Temp-Clear Status {resp.status} | Response: {resp_text[:200]}", channel_id)
            if resp.status == 200:
                result_json = await resp.json()
                result = result_json.get("result")
                success = result in (True, None) or "success" in str(result).lower()
                await log_debug(f"Temp-Ban-Clear für {player_id}: {'erfolgreich' if success else 'ohne Effekt'}", channel_id)
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
                if resp.status == 200:
                    result_json = await resp.json()
                    if result_json.get("result") in (True, None) or "success" in str(result_json.get("result", "")).lower():
                        success = True
        except Exception as e:
            await log_debug(f"Full-Clear {endpoint} Exception: {e}", channel_id)
    await log_debug(f"Full Ban/Blacklist-Clear für {player_id}: {'erfolgreich' if success else 'ohne Effekt'}", channel_id)
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
        await interaction.response.send_message(f"Full Clear läuft für {self.player_id}...", ephemeral=True)
        success = await api_clear_full_bans(self.player_id, self.channel_id)
        await interaction.followup.send(f"Full Clear {'erfolgreich' if success else 'ohne Effekt'}.", ephemeral=True)

    @discord.ui.button(label="Ticket-Infos anzeigen", style=discord.ButtonStyle.primary)
    async def show_infos(self, interaction: discord.Interaction, button: Button):
        ticket = tickets.get(self.channel_id)
        if not ticket:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        summary = "Letzte 30 Nachrichten:\n\n"
        for msg in ticket.history[-30:]:
            prefix = "User" if msg["role"] == "user" else "Bot" if msg["role"] == "assistant" else "System"
            content = msg["content"] if isinstance(msg["content"], str) else "[Bild/Anhang]"
            summary += f"{prefix}: {content}\n\n"
        try:
            await interaction.user.send(f"Infos Ticket {self.ticket_channel.mention}:\n{summary}")
            await interaction.response.send_message("Infos per DM gesendet!", ephemeral=True)
        except:
            await interaction.response.send_message(f"Infos:\n{summary}", ephemeral=True)

# === PLAYER FUNKTIONEN ===
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
                return False
            data = await resp.json()
            players = data.get("result", {}).get("players", []) if isinstance(data.get("result"), dict) else []
            if not players:
                ticket.history.append({"role": "system", "content": f"Kein Player zu '{name}' gefunden."})
                return False
            players_sorted = sorted(players, key=lambda p: max([datetime.fromisoformat(n.get("last_seen", "1970-01-01")).timestamp() for n in p.get("names", [])], default=0), reverse=True)
            best_id = players_sorted[0].get("player_id")
            if best_id and best_id != ticket.player_id:
                ticket.player_id = best_id
                await update_escalation_embed(channel_id)
                ticket.history.append({"role": "system", "content": f"Player-ID {best_id} gefunden."})
                return True
    except Exception as e:
        await log_debug(f"Suche Exception: {e}", channel_id)
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
                return
            data = await resp.json()
            punishments = data.get("result", []) if isinstance(data.get("result"), list) else data.get("result", {}).get("punishments", []) or []
            summary = f"Player-Info ID {ticket.player_id}: {json.dumps(punishments[:15], ensure_ascii=False)}"
            ticket.history.append({"role": "system", "content": summary})
            ticket.player_info_added = True
    except Exception as e:
        await log_debug(f"Info Exception: {e}", channel_id)

# === ESCALATION EMBED ===
async def update_escalation_embed(channel_id: int, summary: str = None):
    ticket = tickets.get(channel_id)
    if not ticket:
        return
    admin_channel = bot.get_channel(ADMIN_SUMMARY_CHANNEL_ID)
    channel = bot.get_channel(channel_id)
    if not admin_channel or not channel:
        return
    embed = discord.Embed(title="Ticket Eskalation", description=summary or "Warte auf Infos...", color=0xffa500)
    embed.add_field(name="Ticket", value=channel.mention)
    embed.add_field(name="Link", value=channel.jump_url)
    view = TicketAdminView(ticket.player_id, channel, channel_id) if ticket.player_id else None
    if ticket.escalation_message:
        await ticket.escalation_message.edit(embed=embed, view=view)
    else:
        ticket.escalation_message = await admin_channel.send(embed=embed, view=view)

# === TICKET KLASSE ===
class Ticket:
    def __init__(self, channel_id: int, owner: discord.Member):
        self.channel_id = channel_id
        self.owner = owner
        self.history = INITIAL_HISTORY.copy()
        self.closed = False
        self.player_id = ""
        self.player_info_added = False
        self.admin_active = False
        self.language = 'de'
        self.pending_response_task = None
        self.admin_timeout_task = None
        self.name_request_message = None
        self.escalation_message = None

tickets = {}

# === PROMPT ===
PROMPT_FILE = 'prompts_de.json'
with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
    prompt_data = json.load(f)
INITIAL_HISTORY = [{"role": "system", "content": prompt_data}] if isinstance(prompt_data, str) else prompt_data

# === LOGGING & SESSION ===
async def log_debug(msg: str, channel_id: int = None):
    full_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Ticket {channel_id or 'Global'}] {msg}"
    print(full_msg)
    channel = bot.get_channel(DEBUG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(f"[DEBUG] {full_msg}")
        except:
            pass

http_session = None
async def create_http_session():
    global http_session
    http_session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False), timeout=aiohttp.ClientTimeout(total=90))

async def close_http_session():
    global http_session
    if http_session:
        await http_session.close()

async def reset_admin_active(ticket: Ticket):
    await asyncio.sleep(1800)
    ticket.admin_active = False
    await log_debug("Admin-Timeout abgelaufen", ticket.channel_id)

def trim_history(ticket: Ticket):
    system = [m for m in ticket.history if m["role"] == "system"]
    other = [m for m in ticket.history if m["role"] != "system"][-30:]
    ticket.history = system + other

async def debounced_ki_response(channel: discord.TextChannel, ticket: Ticket):
    await asyncio.sleep(5)
    ticket.pending_response_task = None
    await send_ki_response(channel, ticket)

# === MODAL & VIEW ===
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
        pid = extract_player_id(user_input)
        if pid and pid != ticket.player_id:
            ticket.player_id = pid
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
                ticket.name_request_message = await interaction.channel.send(content, view=view)

        if ticket.pending_response_task:
            ticket.pending_response_task.cancel()
        ticket.pending_response_task = asyncio.create_task(debounced_ki_response(interaction.channel, ticket))

class NameRequestView(View):
    def __init__(self, channel_id: int, language: str):
        super().__init__(timeout=None)
        self.channel_id = channel_id
        self.language = language

        button = Button(
            label=BUTTON_LABELS.get(language, BUTTON_LABELS['de']),
            style=discord.ButtonStyle.primary
        )
        button.callback = self.button_callback
        self.add_item(button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        ticket = tickets.get(self.channel_id)
        return ticket and interaction.user == ticket.owner

    async def button_callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(IngameNameOrIdModal(self.channel_id, self.language))

# === KI & EVENTS ===
async def send_ki_response(channel: discord.TextChannel, ticket: Ticket):
    if ticket.closed or ticket.admin_active or not http_session:
        return
    trim_history(ticket)
    payload = {"model": "grok-4", "messages": ticket.history, "max_tokens": 1024, "temperature": 0.8}
    bot_reply = None
    for _ in range(3):
        try:
            async with http_session.post("https://api.x.ai/v1/chat/completions", json=payload, headers=GROK_HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bot_reply = data["choices"][0]["message"]["content"].strip()
                    break
        except:
            await asyncio.sleep(5)
    if not bot_reply:
        await channel.send("KI-Probleme – Admin schaut drauf.")
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
    if ticket.player_id and any(w in bot_reply.lower() for w in ["temp", "tk", "votekick", "teamkill", "unban", "clear"]):
        await api_clear_temp_ban(ticket.player_id, ticket.channel_id)
        await channel.send("Temp-Ban clear versucht!")
    if any(w in bot_reply.lower() for w in ["perma", "blacklist", "cheat", "admin"]):
        await update_escalation_embed(ticket.channel_id, summary="Komplexer Fall")
    ticket.history.append({"role": "assistant", "content": bot_reply})

async def send_feedback_message(channel: discord.TextChannel):
    try:
        msg = await channel.send("Danke für dein Ticket! 😊 War alles okay mit dem Support?")
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    except Exception as e:
        await log_debug(f"Feedback Fehler: {e}", channel.id)

@bot.event
async def on_ready():
    await create_http_session()
    await log_debug("Bot online – Persistent Views deaktiviert (kein Error mehr)")

@bot.event
async def on_disconnect():
    await close_http_session()

@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.category and channel.category.name.lower() in [c.lower() for c in ACTIVE_TICKET_CATEGORIES]:
        await asyncio.sleep(8)
        members = [t for t in channel.overwrites if isinstance(t, discord.Member) and not t.bot]
        if members and channel.permissions_for(members[0]).view_channel:
            owner = members[0]
            tickets[channel.id] = Ticket(channel.id, owner)
            await log_debug(f"Neues Ticket {channel.id} – Warte auf erste Nachricht", channel.id)

@bot.event
async def on_message(message):
    if message.author.bot or not isinstance(message.channel, discord.TextChannel):
        return
    if message.channel.category and message.channel.category.name.lower() in [c.lower() for c in ACTIVE_TICKET_CATEGORIES]:
        cid = message.channel.id
        ticket = tickets.get(cid)
        if not ticket:
            ticket = Ticket(cid, message.author)
            tickets[cid] = ticket
            await log_debug(f"Ticket {cid} erstellt (erste Nachricht)", cid)

        if isinstance(message.author, discord.Member) and has_admin_role(message.author):
            ticket.admin_active = True
            if ticket.admin_timeout_task:
                ticket.admin_timeout_task.cancel()
            ticket.admin_timeout_task = asyncio.create_task(reset_admin_active(ticket))
            ticket.history.append({"role": "user", "content": f"[Admin {message.author}]: {message.content}"})
            await log_debug(f"Admin aktiv in {cid}", cid)
            return

        if message.author != ticket.owner:
            return

        is_first = len([m for m in ticket.history if m["role"] == "user"]) == 0

        content = []
        if message.content:
            content.append({"type": "text", "text": message.content})
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                content.append({"type": "image_url", "image_url": {"url": att.url}})
                await log_debug(f"Bild hochgeladen in {cid}", cid)
        if not message.content and content:
            content.insert(0, {"type": "text", "text": "User hat einen Screenshot hochgeladen:"})
        ticket.history.append({"role": "user", "content": content or message.content})

        if is_first:
            ticket.language = detect_language(message.content or "")
            await log_debug(f"Sprache erkannt: {ticket.language} in Ticket {cid}", cid)
            ticket.history.append({
                "role": "system",
                "content": f"Der User spricht {'Englisch' if ticket.language == 'en' else 'Deutsch'}. Antworte immer auf dieser Sprache."
            })
            view = NameRequestView(cid, ticket.language)
            msg = await message.channel.send(GREETINGS.get(ticket.language, GREETINGS['de']), view=view)
            ticket.name_request_message = msg

        id_changed = False
        if pid := extract_player_id(message.content or ""):
            if pid != ticket.player_id:
                ticket.player_id = pid
                id_changed = True

        if name := extract_ingame_name(message.content or ""):
            if await search_and_set_best_player_id(cid, name):
                id_changed = True

        if id_changed:
            await update_escalation_embed(cid)
            ticket.player_info_added = False
            await add_player_info_to_history(cid)
            if ticket.name_request_message:
                await ticket.name_request_message.edit(
                    content=AUTO_DETECT_SUCCESS.get(ticket.language, AUTO_DETECT_SUCCESS['de']),
                    view=None
                )

        if not ticket.player_id and not ticket.name_request_message and not is_first:
            view = NameRequestView(cid, ticket.language)
            msg = await message.channel.send(GREETINGS.get(ticket.language, GREETINGS['de']), view=view)
            ticket.name_request_message = msg

        if ticket.pending_response_task:
            ticket.pending_response_task.cancel()
        ticket.pending_response_task = asyncio.create_task(debounced_ki_response(message.channel, ticket))

    await bot.process_commands(message)

bot.run(DISCORD_TOKEN)