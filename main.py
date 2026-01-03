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
from flask import Flask  # Für Render Web Service Keep-Alive
import threading

load_dotenv()

# Flask App für Render Web Service
app = Flask(__name__)

@app.route('/')
def home():
    return "GBG KI Bot is alive! 🚀"

def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

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

# === SPRACH-TEXTE FÜR MODAL ===
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
            await log_debug(f"Temp-Clear Endpoint remove_temp_ban – Status {resp.status} | Response: {resp_text[:200]}",
                            channel_id)
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
    endpoints = [
        "remove_temp_ban",
        "unban",
        "remove_perma_ban",
        "unblacklist_player"
    ]
    for endpoint in endpoints:
        try:
            async with http_session.post(
                    f"{API_BASE_URL}/{endpoint}",
                    headers=API_HEADERS,
                    json={"player_id": player_id}
            ) as resp:
                resp_text = await resp.text()
                await log_debug(f"Full-Clear Endpoint {endpoint} – Status {resp.status} | Response: {resp_text[:200]}",
                                channel_id)
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

# === PLAYER INFO ===
async def search_and_set_best_player_id(channel_id: int, name: str) -> bool:
    ticket = tickets.get(channel_id)
    if not ticket or not name or not http_session:
        return False
    try:
        async with http_session.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params={"player_name": name, "exact_name_match": "False", "ignore_accent": "True", "page_size": 20}
        ) as resp:
            if resp.status != 200:
                return False
            data = await resp.json()
            players = data.get("result", {}).get("players", []) if isinstance(data.get("result"), dict) else []
            if not players:
                return False
            players_sorted = sorted(players, key=lambda p: max([datetime.fromisoformat(n.get("last_seen", "1970-01-01")).timestamp() for n in p.get("names", [])], default=0), reverse=True)
            best_id = players_sorted[0].get("player_id")
            if best_id and best_id != ticket.player_id:
                ticket.player_id = best_id
                await add_player_info_to_history(channel_id)
                await update_escalation_embed(channel_id)
                return True
    except Exception as e:
        await log_debug(f"Search Exception: {e}", channel_id)
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
                await log_debug(f"Player-Info Abruf fehlgeschlagen (Status {resp.status})", channel_id)
                return
            data = await resp.json()
            await log_debug(f"Roh-Player-Info Response: {json.dumps(data, ensure_ascii=False)}", channel_id)

            player_data = data.get("result", {})
            received_actions = player_data.get("received_actions", [])
            blacklists = player_data.get("blacklists", [])
            is_blacklisted = player_data.get("is_blacklisted", False)

            actions_summary = "Keine received_actions gefunden."
            if received_actions:
                latest = received_actions[0]
                last_action = latest.get("action_type", "Unbekannt")
                last_reason = latest.get("reason", "kein Grund")
                last_by = latest.get("by", "unbekannt")
                last_time = latest.get("time", "unbekannt")
                actions_summary = f"Letzter Action: {last_action} wegen '{last_reason}' am {last_time} von {last_by}. Vollständige received_actions (neueste zuerst): {json.dumps(received_actions[:15], ensure_ascii=False)}"

            blacklist_summary = f"Aktive Blacklist: {'Ja' if is_blacklisted else 'Nein'}. Blacklist-Einträge: {json.dumps(blacklists, ensure_ascii=False)}"

            full_summary = f"Player-Info für ID {ticket.player_id}:\n{actions_summary}\n{blacklist_summary}"

            ticket.history.append({"role": "system", "content": full_summary})
            ticket.player_info_added = True
            await log_debug(f"Player-Info geladen – {len(received_actions)} Actions, Blacklisted: {is_blacklisted}", channel_id)
    except Exception as e:
        await log_debug(f"Player-Info Exception: {e}", channel_id)

# === ADMIN VIEW ===
class TicketAdminView(View):
    def __init__(self, player_id: str, channel_id: int):
        super().__init__(timeout=None)
        self.player_id = player_id
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not any(role.name == ADMIN_ROLE_NAME for role in interaction.user.roles):
            await interaction.response.send_message("Nur Admins!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Alle Bans/Blacklists entfernen (inkl. Perma)", style=discord.ButtonStyle.green, custom_id="admin_full_unban")
    async def full_unban(self, interaction: discord.Interaction, button: Button):
        if not self.player_id:
            await interaction.response.send_message("Keine ID!", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        success = await api_clear_full_bans(self.player_id, self.channel_id)
        await interaction.followup.send(f"Full Clear {'erfolgreich' if success else 'ohne Effekt'}.", ephemeral=True)

    @discord.ui.button(label="Ticket-Infos anzeigen", style=discord.ButtonStyle.primary, custom_id="admin_show_infos")
    async def show_infos(self, interaction: discord.Interaction, button: Button):
        ticket = tickets.get(self.channel_id)
        if not ticket:
            await interaction.response.send_message("Ticket nicht gefunden.", ephemeral=True)
            return
        summary = "Letzte 30 Nachrichten:\n\n"
        for msg in ticket.history[-30:]:
            prefix = "User" if msg.get("role") == "user" else "Bot" if msg.get("role") == "assistant" else "System"
            content = msg.get("content", "") if isinstance(msg.get("content"), str) else "[Bild/Anhang]"
            summary += f"{prefix}: {content}\n\n"
        try:
            await interaction.user.send(f"Infos Ticket <#{self.channel_id}>:\n{summary}")
            await interaction.response.send_message("Infos per DM gesendet!", ephemeral=True)
        except:
            await interaction.response.send_message(f"Infos:\n{summary}", ephemeral=True)

    @discord.ui.button(label="KI pausieren", style=discord.ButtonStyle.red, custom_id="admin_ki_pause")
    async def pause_ki(self, interaction: discord.Interaction, button: Button):
        ticket = tickets.get(self.channel_id)
        if ticket:
            ticket.admin_active = True
            button.label = "KI starten"
            button.style = discord.ButtonStyle.green
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("KI pausiert.", ephemeral=True)

    @discord.ui.button(label="KI starten", style=discord.ButtonStyle.green, custom_id="admin_ki_resume", disabled=True)
    async def resume_ki(self, interaction: discord.Interaction, button: Button):
        ticket = tickets.get(self.channel_id)
        if ticket:
            ticket.admin_active = False
            button.label = "KI pausieren"
            button.style = discord.ButtonStyle.red
            button.disabled = False
            await interaction.response.edit_message(view=self)
            await interaction.followup.send("KI gestartet.", ephemeral=True)

# === EMBED ===
async def update_escalation_embed(channel_id: int, summary: str = None):
    ticket = tickets.get(channel_id)
    if not ticket:
        return
    admin_channel = bot.get_channel(ADMIN_SUMMARY_CHANNEL_ID)
    channel = bot.get_channel(channel_id)
    if not admin_channel or not channel:
        return
    embed = discord.Embed(title="Ticket Eskalation", description=summary or "Aktives Ticket", color=0xffa500)
    embed.add_field(name="Ticket", value=channel.mention)
    embed.add_field(name="Link", value=channel.jump_url)
    if ticket.player_id:
        embed.add_field(name="Player-ID", value=ticket.player_id, inline=False)
        actions = []
        for m in reversed(ticket.history):
            if m.get("role") == "system" and "Player-Info" in m.get("content", ""):
                try:
                    start = m["content"].find("Letzter Action:")
                    if start != -1:
                        actions = m["content"][start:].splitlines()[:6]
                        break
                except:
                    pass
        if actions:
            embed.add_field(name="Letzte Actions", value="\n".join(actions), inline=False)
    view = TicketAdminView(ticket.player_id or "", channel_id)
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
        self.pending_task = None
        self.admin_timeout_task = None
        self.name_request_message: discord.Message | None = None
        self.escalation_message: discord.Message | None = None

tickets = {}

# === PROMPT ===
PROMPT_FILE = 'prompts_de.json'
with open(PROMPT_FILE, 'r', encoding='utf-8') as f:
    prompt_data = json.load(f)

INITIAL_HISTORY = [{"role": "system", "content": prompt_data["content"]}] if isinstance(prompt_data, dict) and prompt_data.get("role") == "system" else [{"role": "system", "content": prompt_data}] if isinstance(prompt_data, str) else prompt_data if isinstance(prompt_data, list) else [{"role": "system", "content": str(prompt_data)}]

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

def trim_history(ticket: Ticket):
    system = [m for m in ticket.history if isinstance(m, dict) and m.get("role") == "system"]
    other = [m for m in ticket.history if isinstance(m, dict) and m.get("role") != "system"][-30:]
    ticket.history = system + other

async def debounced_ki_response(channel: discord.TextChannel, ticket: Ticket):
    await asyncio.sleep(4)
    ticket.pending_task = None
    await send_ki_response(channel, ticket)

# === MODAL & VIEW ===
class IngameNameOrIdModal(Modal):
    def __init__(self, language: str):
        super().__init__(title=MODAL_TITLES.get(language, MODAL_TITLES['de']))
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
        ticket = tickets.get(interaction.channel_id)
        if not ticket or interaction.user != ticket.owner:
            await interaction.response.send_message("Nur du!", ephemeral=True)
            return

        user_input = self.input.value.strip()
        await interaction.response.defer(ephemeral=True)

        found = False
        pid = extract_player_id(user_input)
        if pid and pid != ticket.player_id:
            ticket.player_id = pid
            await add_player_info_to_history(interaction.channel_id)
            found = True

        if not found:
            found = await search_and_set_best_player_id(interaction.channel_id, name=user_input)
            if found:
                await add_player_info_to_history(interaction.channel_id)

        if found and ticket.name_request_message:
            await ticket.name_request_message.edit(content="Player-Info geladen!", view=None)

        if ticket.pending_task:
            ticket.pending_task.cancel()
        ticket.pending_task = asyncio.create_task(debounced_ki_response(interaction.channel, ticket))

class NameRequestView(View):
    def __init__(self, language: str):
        super().__init__(timeout=None)
        self.language = language
        button = Button(label="Name/ID eingeben", style=discord.ButtonStyle.primary, custom_id=f"name_button_{language}")
        button.callback = self.button_callback
        self.add_item(button)

    async def button_callback(self, interaction: discord.Interaction):
        ticket = tickets.get(interaction.channel_id)
        if ticket:
            await interaction.response.send_modal(IngameNameOrIdModal(ticket.language))

# === KI RESPONSE ===
async def send_ki_response(channel: discord.TextChannel, ticket: Ticket):
    if ticket.closed or ticket.admin_active or not http_session:
        return

    trim_history(ticket)

    messages = [m for m in ticket.history if isinstance(m, dict)]

    payload = {"model": "grok-4", "messages": messages, "max_tokens": 1024, "temperature": 0.8}

    bot_reply = None
    for _ in range(3):
        try:
            async with http_session.post("https://api.x.ai/v1/chat/completions", json=payload, headers=GROK_HEADERS) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    bot_reply = data["choices"][0]["message"]["content"]
                    break
        except Exception as e:
            await log_debug(f"KI Exception: {e}", ticket.channel_id)
            await asyncio.sleep(5)

    if not bot_reply:
        await channel.send("KI-Probleme – weiter schreiben!")
        return

    clean_reply = bot_reply
    request_modal = "**REQUEST_NAME_MODAL:**" in bot_reply
    auto_unban = "**AUTO_UNBAN:**" in bot_reply
    close_ticket = bot_reply.strip() == "**CLOSE TICKET:**"
    escalation_summary = None

    if "**ZUSAMMENFASSUNG FÜR ADMINS:**" in bot_reply:
        parts = bot_reply.split("**ZUSAMMENFASSUNG FÜR ADMINS:**", 1)
        clean_reply = parts[0].strip()
        escalation_summary = parts[1].strip() if len(parts) > 1 else None

    for tag in ["**REQUEST_NAME_MODAL:**", "**AUTO_UNBAN:**", "**ZUSAMMENFASSUNG FÜR ADMINS:**", "**CLOSE TICKET:**"]:
        clean_reply = clean_reply.replace(tag, "").strip()

    if clean_reply:
        await channel.send(clean_reply)

    if request_modal:
        view = NameRequestView(ticket.language)
        if ticket.name_request_message:
            await ticket.name_request_message.edit(content="Klick für Namen/ID:", view=view)
        else:
            ticket.name_request_message = await channel.send("Klick für Namen/ID:", view=view)

    if auto_unban and ticket.player_id:
        success = await api_clear_temp_ban(ticket.player_id, ticket.channel_id)
        # KI handhabt Text

    if close_ticket:
        ticket.closed = True
        await channel.send("Ticket geschlossen!")
        await send_feedback_message(channel)
        del tickets[ticket.channel_id]
        return

    if escalation_summary:
        await update_escalation_embed(ticket.channel_id, summary=escalation_summary)

    ticket.history.append({"role": "assistant", "content": bot_reply})

async def send_feedback_message(channel: discord.TextChannel):
    try:
        msg = await channel.send("Danke! Alles okay?")
        await msg.add_reaction("👍")
        await msg.add_reaction("👎")
    except:
        pass

@bot.event
async def on_ready():
    await create_http_session()
    bot.add_view(NameRequestView('de'))
    bot.add_view(NameRequestView('en'))
    bot.add_view(TicketAdminView("", 0))
    await log_debug("Bot online – Web Service Mode aktiv")

@bot.event
async def on_disconnect():
    await close_http_session()

@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.category and channel.category.name.lower() in [c.lower() for c in ACTIVE_TICKET_CATEGORIES]:
        await asyncio.sleep(8)
        members = [t for t in channel.overwrites if isinstance(t, discord.Member) and not t.bot]
        if members:
            owner = members[0]
            tickets[channel.id] = Ticket(channel.id, owner)

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

        if isinstance(message.author, discord.Member) and has_admin_role(message.author):
            ticket.admin_active = True
            if ticket.admin_timeout_task:
                ticket.admin_timeout_task.cancel()
            ticket.admin_timeout_task = asyncio.create_task(reset_admin_active(ticket))
            ticket.history.append({"role": "user", "content": f"[Admin {message.author}]: {message.content}"})
            return

        if message.author != ticket.owner:
            return

        content = [{"type": "text", "text": message.content}] if message.content else []
        for att in message.attachments:
            if att.content_type and att.content_type.startswith("image/"):
                content.append({"type": "image_url", "image_url": {"url": att.url}})
        ticket.history.append({"role": "user", "content": content or message.content})

        if len([m for m in ticket.history if isinstance(m, dict) and m.get("role") == "user"]) == 1:
            ticket.language = detect_language(message.content or "")

        id_changed = False
        if pid := extract_player_id(message.content or ""):
            if pid != ticket.player_id:
                ticket.player_id = pid
                await add_player_info_to_history(cid)
                id_changed = True

        if name := extract_ingame_name(message.content or ""):
            if await search_and_set_best_player_id(cid, name):
                id_changed = True

        if id_changed:
            await update_escalation_embed(cid)

        if ticket.pending_task:
            ticket.pending_task.cancel()
        ticket.pending_task = asyncio.create_task(debounced_ki_response(message.channel, ticket))

    await bot.process_commands(message)

if __name__ == "__main__":
    # Flask in separatem Thread starten (für Render Web Service)
    threading.Thread(target=run_flask, daemon=True).start()
    # Discord Bot starten
    bot.run(DISCORD_TOKEN)