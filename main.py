import discord
from discord.ext import commands
import asyncio
import os
import re
import json
from dotenv import load_dotenv
from collections import defaultdict
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

# Ticket-States
ticket_owner_cache = {}
ticket_history = defaultdict(list)
ticket_closed = defaultdict(bool)
ticket_player_id = defaultdict(str)
ticket_player_info_added = defaultdict(bool)
admin_active = defaultdict(bool)
ticket_escalation_message = defaultdict(lambda: None)
name_modal_sent = defaultdict(bool)
pending_response_task = defaultdict(lambda: None)

# Globale aiohttp Session
http_session: aiohttp.ClientSession | None = None

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
    full_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [Ticket {channel_id or 'Global'}] {msg}"
    print(full_msg)
    channel = bot.get_channel(DEBUG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(f"[DEBUG] {full_msg}")
        except:
            pass


# === GLOBALE HTTP SESSION ===
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


# === DEBOUNCED KI-RESPONSE ===
async def debounced_ki_response(channel: discord.TextChannel, channel_id: int):
    await asyncio.sleep(5)
    if channel_id in pending_response_task:
        del pending_response_task[channel_id]
    await send_ki_response(channel, channel_id)


# === MODAL & VIEW FÜR NAME/ID-INPUT ===
class IngameNameOrIdModal(Modal):
    def __init__(self, channel_id: int):
        super().__init__(title="Exakten Ingame-Namen oder Steam-ID eingeben")
        self.channel_id = channel_id
        self.input = TextInput(
            label="Name (mit Clan-Tag) ODER Steam-ID",
            placeholder="z. B. ℧ | Narcotic ODER 76561198986670442",
            style=discord.TextStyle.short,
            min_length=4,
            max_length=50
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        user_input = self.input.value.strip()
        owner = ticket_owner_cache.get(self.channel_id)
        if interaction.user != owner:
            await interaction.response.send_message("Nur der Ticket-Owner darf das ausfüllen!", ephemeral=True)
            return

        await interaction.response.send_message(f"Danke! Verarbeite jetzt '{user_input}'...", ephemeral=False)

        possible_id = extract_player_id(user_input)
        found = False
        if possible_id:
            if possible_id != ticket_player_id[self.channel_id]:
                ticket_player_id[self.channel_id] = possible_id
                await log_debug(f"Steam-ID direkt aus Modal gesetzt: {possible_id}", self.channel_id)
                await update_escalation_embed(self.channel_id)
                ticket_history[self.channel_id].append({
                    "role": "system",
                    "content": f"User hat Steam-ID per Modal angegeben: {possible_id}. Player-Info wird geladen."
                })
                await add_player_info_to_history(self.channel_id)
                found = True
        else:
            found = await search_and_set_best_player_id(self.channel_id, name=user_input)
            if found:
                ticket_history[self.channel_id].append({
                    "role": "system",
                    "content": f"Name '{user_input}' per Modal verarbeitet – Player-ID gefunden."
                })
                await add_player_info_to_history(self.channel_id)

        if not found:
            ticket_history[self.channel_id].append({
                "role": "system",
                "content": f"Verarbeitung von '{user_input}' per Modal fehlgeschlagen – kein Player gefunden. User muss korrekten Namen/ID angeben."
            })

        if self.channel_id in pending_response_task:
            pending_response_task[self.channel_id].cancel()
        pending_response_task[self.channel_id] = asyncio.create_task(
            debounced_ki_response(interaction.channel, self.channel_id))


class NameRequestView(View):
    def __init__(self, channel_id: int):
        super().__init__(timeout=None)
        self.channel_id = channel_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        owner = ticket_owner_cache.get(self.channel_id)
        return interaction.user == owner

    @discord.ui.button(label="Exakten Namen oder Steam-ID eingeben", style=discord.ButtonStyle.primary)
    async def request_input(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(IngameNameOrIdModal(self.channel_id))


# === RCON API: SEPARATE CLEAR-FUNKTIONEN ===
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


# === ADMIN VIEW MIT UNBAN-BUTTON (verbesserter Fallback) ===
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
        await interaction.response.send_message(f"Full Ban/Blacklist-Clear für {self.player_id} läuft...",
                                                ephemeral=True)
        success = await api_clear_full_bans(self.player_id, self.channel_id)
        status = "erfolgreich" if success else "ohne Effekt"
        await interaction.followup.send(f"Full Ban/Blacklist-Clear {status}.", ephemeral=True)

    @discord.ui.button(label="Ticket-Infos anzeigen", style=discord.ButtonStyle.primary)
    async def show_infos(self, interaction: discord.Interaction, button: Button):
        summary = "Ticket-Konversation (letzte 30 Nachrichten):\n\n"
        history = ticket_history[self.channel_id][-30:]
        for msg in history:
            role = msg["role"]
            content = msg["content"] if isinstance(msg["content"], str) else "[Nachricht mit Bild/Anhang]"
            prefix = "User" if role == "user" else "Bot"
            summary += f"{prefix}: {content}\n\n"
        try:
            await interaction.user.send(f"Infos zum Ticket {self.ticket_channel.mention}:\n{summary}")
            await interaction.response.send_message("Infos per DM gesendet!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"DM blockiert – Infos hier (nur du siehst's):\n{summary}",
                                                    ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"Fehler beim Senden der Infos: {str(e)}", ephemeral=True)


# === PLAYER-SUCHE (return bool) ===
async def search_and_set_best_player_id(channel_id: int, name: str = None) -> bool:
    if not name or not http_session:
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
            if isinstance(result, dict):
                players = result.get("players", [])
            else:
                players = []
            if not players:
                await log_debug("Keine Players gefunden", channel_id)
                ticket_history[channel_id].append({
                    "role": "system",
                    "content": f"Name-Suche für '{name}' hat keinen passenden Player gefunden. Möglicherweise falsche Schreibweise oder Clan-Tag."
                })
                return False

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
                if best_id and best_id != ticket_player_id[channel_id]:
                    old_id = ticket_player_id[channel_id] or "keine"
                    ticket_player_id[channel_id] = best_id
                    await log_debug(f"Neue beste ID {best_id} (von Name '{name}') – vorher {old_id}", channel_id)
                    await update_escalation_embed(channel_id)
                    ticket_history[channel_id].append({
                        "role": "system",
                        "content": f"Beste Player-ID zu Name '{name}' gefunden: {best_id}"
                    })
                    return True
    except Exception as e:
        await log_debug(f"Player-Suche Exception: {e}", channel_id)
    return False


# === PLAYER-INFO LADEN (super robust) ===
async def add_player_info_to_history(channel_id: int):
    player_id = ticket_player_id[channel_id]
    if not player_id or ticket_player_info_added[channel_id] or not http_session:
        return
    try:
        async with http_session.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params={"player_id": player_id, "page_size": 30}
        ) as resp:
            if resp.status != 200:
                await log_debug(f"Player-Info Abruf Status {resp.status}", channel_id)
                ticket_history[channel_id].append({
                    "role": "system",
                    "content": f"Player-Info für ID {player_id} konnte nicht abgerufen werden (Status {resp.status})."
                })
                return
            data = await resp.json()
            raw_result = data.get("result")

            punishments = []
            if isinstance(raw_result, list):
                punishments = raw_result
            elif isinstance(raw_result, dict):
                punishments = raw_result.get("punishments", []) or raw_result.get("history", []) or raw_result.get(
                    "actions", []) or []
            await log_debug(f"Punishments für ID {player_id} geparst – {len(punishments)} Einträge", channel_id)

            limited = punishments[:15]
            full_summary = f"Spieler-Info für ID {player_id} (letzte bis zu 15 Punishment-Einträge, neueste zuerst): {json.dumps(limited, ensure_ascii=False, default=str)}"
            ticket_history[channel_id].append({"role": "system", "content": full_summary})

            ban_entries = [p for p in punishments if
                           p.get("action", "").lower() in ["ban", "temp_ban", "perma_ban", "permanent_ban", "blacklist",
                                                           "remove_temp_ban", "unban", "unblacklist_player"]]
            if ban_entries:
                latest = ban_entries[0]
                action = latest.get("action", "Unbekannt")
                reason = latest.get("reason", "kein Grund angegeben")
                timestamp = latest.get("timestamp", "unbekannt")
                by = latest.get("by", "unbekannt")
                ban_summary = f"Letzter relevanter Punishment: {action} wegen '{reason}' am {timestamp} von {by}. Vollständige Liste oben in der JSON-Info."
            else:
                ban_summary = "Keine Ban- oder Blacklist-Einträge in den Daten gefunden – eventuell nur Warnings oder keine Punishments."

            ticket_history[channel_id].append({"role": "system", "content": ban_summary})

            ticket_player_info_added[channel_id] = True
            await log_debug("Player-Info + Ban-Summary erfolgreich zur KI-History hinzugefügt", channel_id)
    except Exception as e:
        await log_debug(f"Player-Info Exception: {e}", channel_id)
        ticket_history[channel_id].append({
            "role": "system",
            "content": f"Fehler beim Laden der Player-Info für ID {player_id}: {str(e)}"
        })


# === EMBED AKTUALISIEREN (robust) ===
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
            if http_session:
                async with http_session.get(
                        f"{API_BASE_URL}/get_players_history",
                        headers=API_HEADERS,
                        params={"player_id": player_id, "page_size": 10}
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_result = data.get("result")
                        punishments = []
                        if isinstance(raw_result, list):
                            punishments = raw_result
                        elif isinstance(raw_result, dict):
                            punishments = raw_result.get("punishments", []) or raw_result.get("history", []) or []
                        if punishments:
                            pun_str = "\n".join([
                                                    f"{p.get('action', 'Unknown')} ({p.get('reason', 'N/A')}) am {p.get('timestamp', 'N/A')} von {p.get('by', 'N/A')}"
                                                    for p in punishments[:5]])
                            embed.add_field(name="Letzte Punishments (mit Grund)", value=pun_str or "Keine Details",
                                            inline=False)
                        else:
                            embed.add_field(name="Letzte Punishments", value="Keine gefunden", inline=False)
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
        if len(candidate) >= 5 and re.search(r'[A-Z0-9\[\]]', candidate) and not candidate.lower() in ["hallo", "hi",
                                                                                                       "hey", "hallooo",
                                                                                                       "moinc", "heyo"]:
            return candidate
    return None


def has_admin_role(member: discord.Member) -> bool:
    return any(role.name == ADMIN_ROLE_NAME for role in member.roles)


# === KI-ANTWORT (mit massivem Logging & Fallback) ===
async def send_ki_response(channel: discord.TextChannel, channel_id: int):
    if ticket_closed[channel_id] or admin_active[channel_id] or not http_session:
        return

    await log_debug("Starte KI-Antwort – History-Länge: " + str(len(ticket_history[channel_id])), channel_id)

    messages_for_api = []
    for msg in ticket_history[channel_id]:
        if msg["role"] == "system":
            messages_for_api.append({"role": "system", "content": msg["content"]})
        elif isinstance(msg["content"], str):
            messages_for_api.append({"role": msg["role"], "content": msg["content"]})
        elif isinstance(msg["content"], list):
            messages_for_api.append({"role": msg["role"], "content": msg["content"]})

    try:
        payload = {
            "model": "grok-4",
            "messages": messages_for_api,
            "max_tokens": 500,
            "temperature": 0.8
        }
        await log_debug("Sende Payload an Grok-API", channel_id)
        async with http_session.post("https://api.x.ai/v1/chat/completions", json=payload,
                                     headers=GROK_HEADERS) as response:
            resp_text = await response.text()
            if response.status != 200:
                await log_debug(f"KI-API Fehler {response.status}: {resp_text[:500]}", channel_id)
                await channel.send(
                    "Hey, momentan hab ich technische Probleme mit meiner KI – ein Admin schaut sich's an. Erzähl trotzdem weiter! 😅")
                return
            data = await response.json()
            await log_debug("Grok-API erfolgreich – Antwort erhalten", channel_id)
            bot_reply = data["choices"][0]["message"]["content"].strip()

        # Keine Tags mehr – Actions code-basiert
        await channel.send(bot_reply)

        # Auto-Temp-Unban: Einfache Keyword-Heuristik (anpassen nach Bedarf)
        if ticket_player_id[channel_id] and any(word in bot_reply.lower() for word in
                                                ["temp", "tk", "votekick", "teamkill", "klein", "unban", "clear"]):
            await api_clear_temp_ban(ticket_player_id[channel_id], channel_id)
            await channel.send(
                "Ich hab mal versucht, einen Temp-Ban zu clearen – schau mal, ob du wieder reinkommst! 😏")

        # Eskalation: Wenn KI komplexen Fall andeutet
        if any(word in bot_reply.lower() for word in ["perma", "blacklist", "cheat", "schwer", "admin"]):
            await update_escalation_embed(channel_id, summary="Komplexer Fall – Admins prüfen")

        ticket_history[channel_id].append({"role": "assistant", "content": bot_reply})
        await log_debug("KI-Antwort erfolgreich gesendet", channel_id)

    except asyncio.CancelledError:
        await log_debug("KI-Task cancelled (Debounce)", channel_id)
    except Exception as e:
        await log_debug(f"KI-Exception (kritisch): {str(e)}", channel_id)
        await channel.send(
            "Ups, meine KI hat gerade einen Hänger – Versuch's in ein paar Sekunden nochmal oder ping einen Admin! 🙈")


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
            feedback_channel = bot.get_channel(DEBUG_CHANNEL_ID)
            if feedback_channel:
                await feedback_channel.send(f"Feedback Ticket {channel_id} von {user}: {feedback}")


@bot.event
async def on_ready():
    await create_http_session()
    await log_debug("Bot online – Safety-Fix: Keine Tags mehr, Actions code-basiert!")


@bot.event
async def on_disconnect():
    await close_http_session()


@bot.event
async def on_guild_channel_create(channel):
    if isinstance(channel, discord.TextChannel) and channel.category and channel.category.name.lower() in [c.lower() for
                                                                                                           c in
                                                                                                           ACTIVE_TICKET_CATEGORIES]:
        await asyncio.sleep(8)
        overwrites_members = [target for target in channel.overwrites if
                              isinstance(target, discord.Member) and not target.bot]
        if overwrites_members:
            owner = overwrites_members[0]
            if channel.permissions_for(owner).view_channel:
                ticket_owner_cache[channel.id] = owner
                ticket_history[channel.id] = INITIAL_HISTORY.copy()
                ticket_closed[channel.id] = False
                ticket_player_id[channel.id] = ""
                ticket_player_info_added[channel.id] = False
                admin_active[channel.id] = False
                name_modal_sent[channel.id] = False
                await log_debug(f"Neues Ticket {channel.id} – Owner aus Overwrites: {owner}", channel.id)
                return

        await log_debug(f"Neues Ticket {channel.id} – Kein Owner aus Overwrites – warte auf erste User-Nachricht",
                        channel.id)


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if not isinstance(message.channel, discord.TextChannel):
        return
    if message.channel.category and message.channel.category.name.lower() in [c.lower() for c in
                                                                              ACTIVE_TICKET_CATEGORIES]:
        channel_id = message.channel.id

        if channel_id not in ticket_owner_cache:
            ticket_owner_cache[channel_id] = message.author
            ticket_history[channel_id] = INITIAL_HISTORY.copy()
            await log_debug(f"Ticket {channel_id} – Owner dynamisch aus erster Nachricht: {message.author}", channel_id)

        owner = ticket_owner_cache.get(channel_id)

        if isinstance(message.author, discord.Member) and has_admin_role(message.author):
            admin_active[channel_id] = True
            await log_debug(f"Admin {message.author} interveniert – KI pausiert", channel_id)
            ticket_history[channel_id].append(
                {"role": "user", "content": f"[Admin {message.author}]: {message.content}"})
            admin_active[channel_id] = False
            return

        if message.author != owner:
            return

        await log_debug(f"Owner-Nachricht in Ticket {channel_id}: {message.content[:100]}", channel_id)

        user_content = []
        if message.content:
            user_content.append({"type": "text", "text": message.content})

        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith("image/"):
                user_content.append({
                    "type": "image_url",
                    "image_url": {"url": attachment.url}
                })
                await log_debug(f"Bild angehängt: {attachment.filename}", channel_id)

        if not message.content and user_content:
            user_content.insert(0, {"type": "text", "text": "User hat einen Screenshot hochgeladen:"})

        if user_content:
            ticket_history[channel_id].append({"role": "user", "content": user_content})
        else:
            ticket_history[channel_id].append({"role": "user", "content": message.content})

        direct_id = extract_player_id(message.content or "")
        ingame_name = extract_ingame_name(message.content or "")
        id_changed = False

        if direct_id and direct_id != ticket_player_id[channel_id]:
            ticket_player_id[channel_id] = direct_id
            id_changed = True

        if ingame_name:
            found = await search_and_set_best_player_id(channel_id, name=ingame_name)
            if found and not id_changed:
                id_changed = True

        if id_changed:
            await update_escalation_embed(channel_id)
            ticket_player_info_added[channel_id] = False

        await add_player_info_to_history(channel_id)

        if channel_id in pending_response_task:
            pending_response_task[channel_id].cancel()
        pending_response_task[channel_id] = asyncio.create_task(debounced_ki_response(message.channel, channel_id))

        if ticket_closed[channel_id]:
            await send_feedback_message(message.channel)

    await bot.process_commands(message)


bot.run(DISCORD_TOKEN)