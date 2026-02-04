import discord
from discord.ext import commands
import asyncio
import os
import re
import json
import signal
import sys
from typing import Optional
from dotenv import load_dotenv
from collections import defaultdict
import aiohttp
import urllib3
from datetime import datetime, timezone, timedelta
from discord.ui import Button, View, Modal, TextInput

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

bot = commands.Bot(command_prefix="!", intents=intents)

# === KONFIG ===
# Alle Server-URLs
API_SERVERS = [
    os.getenv('API_BASE_URL_1', 'https://gbg-hll.com:64301/api').rstrip('/'),
    os.getenv('API_BASE_URL_2', 'https://gbg-hll.com:64302/api').rstrip('/'),
    os.getenv('API_BASE_URL_3', 'https://gbg-hll.com:64303/api').rstrip('/')
]
API_BASE_URL = API_SERVERS[0]  # Hauptserver für nicht-VIP Operationen
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

# Performance & Limits
HTTP_TIMEOUT = 30  # Sekunden für API-Calls
MAX_HISTORY_LENGTH = 50  # Maximale Konversations-Historie pro Ticket
MAX_RETRIES = 3  # Retry-Versuche für API-Calls
RETRY_DELAY = 2  # Sekunden zwischen Retries

# Ticket-States
ticket_owner_cache = {}
ticket_history = defaultdict(list)
ticket_closed = defaultdict(bool)
ticket_feedback_sent = defaultdict(bool)  # Feedback nur einmal senden
ticket_player_id = defaultdict(str)
ticket_player_info_added = defaultdict(bool)
admin_active = defaultdict(bool)
ticket_escalation_message = defaultdict(lambda: None)
ticket_asked_id = defaultdict(bool)  # Nur einmal ID-Button senden
ticket_id_input_used = defaultdict(bool)  # Verhindert mehrfache ID-Abfragen

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
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{timestamp}] [Ticket {channel_id or 'Global'}] {msg}"
    print(full_msg)
    channel = bot.get_channel(DEBUG_CHANNEL_ID)
    if channel:
        try:
            await channel.send(f"[DEBUG] {full_msg}")
        except Exception as e:
            print(f"[{timestamp}] Fehler beim Senden der Debug-Nachricht: {e}")


# === RCON API: BAN-CLEAR ===
async def api_clear_temp_ban(player_id: str, channel_id: int) -> bool:
    if not player_id:
        return False

    success = False
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{API_BASE_URL}/unban",
                headers=API_HEADERS,
                json={"player_id": player_id},
                ssl=False
            ) as resp:
                await log_debug(f"unban – Status {resp.status}", channel_id)
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("result")
                    if result in (True, None):
                        success = True
    except asyncio.TimeoutError:
        await log_debug(f"unban Timeout nach {HTTP_TIMEOUT}s", channel_id)
    except Exception as e:
        await log_debug(f"unban Exception: {e}", channel_id)

    await log_debug(f"Temp-Clear für {player_id}: {'Erfolg' if success else 'ohne Effekt'}", channel_id)
    return success


async def api_clear_ban(player_id: str, channel_id: int) -> bool:
    if not player_id:
        return False

    success = False
    endpoints = ["remove_temp_ban", "unban", "remove_perma_ban", "unblacklist_player"]
    
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for endpoint in endpoints:
            for attempt in range(MAX_RETRIES):
                try:
                    async with session.post(
                        f"{API_BASE_URL}/{endpoint}",
                        headers=API_HEADERS,
                        json={"player_id": player_id},
                        ssl=False
                    ) as resp:
                        await log_debug(f"{endpoint} – Status {resp.status} (Versuch {attempt + 1})", channel_id)
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("result")
                            if result in (True, None):
                                success = True
                                break
                        elif resp.status >= 500 and attempt < MAX_RETRIES - 1:
                            await asyncio.sleep(RETRY_DELAY)
                            continue
                        break
                except asyncio.TimeoutError:
                    await log_debug(f"{endpoint} Timeout (Versuch {attempt + 1})", channel_id)
                    if attempt < MAX_RETRIES - 1:
                        await asyncio.sleep(RETRY_DELAY)
                except Exception as e:
                    await log_debug(f"{endpoint} Exception: {e}", channel_id)
                    break
            
            if success:
                break

    await log_debug(f"Ban/Blacklist-Clear für {player_id}: {'Erfolg' if success else 'ohne Effekt'}", channel_id)
    return success


# === ADMIN VIEW ===
class TicketAdminView(View):
    def __init__(self, player_id: str, ticket_channel: discord.TextChannel, channel_id: int):
        super().__init__(timeout=None)
        self.player_id = player_id
        self.ticket_channel = ticket_channel
        self.channel_id = channel_id

    @discord.ui.button(label="Alle Bans/Blacklists entfernen", style=discord.ButtonStyle.green)
    async def clear_ban(self, interaction: discord.Interaction, button: Button):
        player_id = self.player_id or ticket_player_id[self.channel_id]
        if not player_id:
            await interaction.response.send_message("Keine ID gefunden – manuell prüfen.", ephemeral=True)
            return

        await interaction.response.send_message(f"Ban/Blacklist-Clear für {player_id} läuft...", ephemeral=True)
        success = await api_clear_ban(player_id, self.channel_id)
        status = "erfolgreich" if success else "ohne Effekt"
        await interaction.followup.send(f"Ban/Blacklist-Clear {status}.", ephemeral=True)

    @discord.ui.button(label="KI deaktivieren", style=discord.ButtonStyle.red)
    async def toggle_ki(self, interaction: discord.Interaction, button: Button):
        if not any(role.name == ADMIN_ROLE_NAME for role in interaction.user.roles):
            await interaction.response.send_message("Nur HLL Admins dürfen die KI toggeln.", ephemeral=True)
            return

        admin_active[self.channel_id] = not admin_active[self.channel_id]
        new_label = "KI aktivieren" if admin_active[self.channel_id] else "KI deaktivieren"
        new_style = discord.ButtonStyle.green if admin_active[self.channel_id] else discord.ButtonStyle.red
        button.label = new_label
        button.style = new_style
        await interaction.response.edit_message(view=self)
        status = "deaktiviert" if admin_active[self.channel_id] else "aktiviert"
        await interaction.followup.send(f"KI {status} für dieses Ticket.", ephemeral=True)

    @discord.ui.button(label="Add VIP", style=discord.ButtonStyle.primary, emoji="🌟")
    async def add_vip(self, interaction: discord.Interaction, button: Button):
        if not any(role.name == ADMIN_ROLE_NAME for role in interaction.user.roles):
            await interaction.response.send_message("Nur HLL Admins dürfen VIP vergeben.", ephemeral=True)
            return
        
        player_id = self.player_id or ticket_player_id[self.channel_id]
        if not player_id:
            await interaction.response.send_message("❌ Keine Player-ID gefunden!", ephemeral=True)
            return
        
        # Öffne Modal für Stunden-Eingabe
        modal = VIPModal(player_id, self.channel_id)
        await interaction.response.send_modal(modal)


# === VIP MODAL ===
class VIPModal(Modal, title="VIP-Zeit vergeben"):
    hours_input = TextInput(
        label="Stunden (0 für Lifetime VIP)",
        placeholder="z.B. 24, 168 (Woche), 720 (Monat), 0 (Lifetime)",
        style=discord.TextStyle.short,
        required=True
    )

    def __init__(self, player_id: str, channel_id: int):
        super().__init__()
        self.player_id = player_id
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        try:
            hours = int(self.hours_input.value.strip())
            if hours < 0:
                await interaction.followup.send("❌ Stunden müssen >= 0 sein!", ephemeral=True)
                return
        except ValueError:
            await interaction.followup.send("❌ Ungültige Eingabe! Bitte eine Zahl eingeben.", ephemeral=True)
            return

        # VIP vergeben
        success, new_exp = await add_vip_to_player(self.player_id, hours, self.channel_id)
        
        if success:
            # Zeitformat schön formatieren
            if hours == 0:
                formatted_date = "niemals"
                time_text = "Lifetime VIP"
            else:
                # Parse Datum
                try:
                    exp_dt = datetime.fromisoformat(new_exp.replace('Z', '+00:00'))
                    formatted_date = exp_dt.strftime("%d.%m.%Y um %H:%M Uhr")
                except:
                    formatted_date = new_exp
                
                # Stunden in Tage umrechnen (runden)
                days = round(hours / 24)
                if days == 0:
                    time_text = f"{hours} Stunden"
                elif days == 1:
                    time_text = "1 Tag"
                else:
                    time_text = f"{days} Tage"
            
            # Nachricht an KI/User im Ticket-Channel
            channel = bot.get_channel(self.channel_id)
            if channel:
                if hours == 0:
                    vip_msg = f"🌟 **VIP vergeben!** Der Admin hat dir Lifetime VIP auf allen Servern zugewiesen. Dein VIP läuft nie ab!"
                else:
                    vip_msg = f"🌟 **VIP vergeben!** Der Admin hat dir VIP für {time_text} auf allen Servern zugewiesen. Dein VIP ist gültig bis: {formatted_date}"
                
                await channel.send(vip_msg)
            
            # Bestätigung für Admin
            if hours == 0:
                await interaction.followup.send(
                    f"✅ Lifetime VIP für Player {self.player_id} auf allen 3 Servern vergeben!",
                    ephemeral=True
                )
            else:
                await interaction.followup.send(
                    f"✅ VIP für Player {self.player_id} auf allen 3 Servern vergeben!\n+{time_text} ({hours}h)\nGültig bis: {formatted_date}",
                    ephemeral=True
                )
        else:
            await interaction.followup.send(
                f"❌ Fehler beim VIP-Vergeben! Details in den Logs.",
                ephemeral=True
            )


# === VIP FUNKTIONEN ===
async def get_vip_expiration(player_id: str, channel_id: int) -> Optional[str]:
    """Ruft die aktuelle VIP-Ablaufzeit ab"""
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{API_BASE_URL}/get_vip_ids",
                headers=API_HEADERS,
                ssl=False
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    vips = data.get("result", [])
                    for vip in vips:
                        if vip.get("player_id") == player_id:
                            exp = vip.get("vip_expiration")
                            await log_debug(f"🕒 Aktuelle VIP-Zeit für {player_id}: {exp or 'Keine'}", channel_id)
                            return exp
                    await log_debug(f"ℹ️ Kein VIP für {player_id} gefunden", channel_id)
                    return None
    except Exception as e:
        await log_debug(f"❌ get_vip_expiration Fehler: {e}", channel_id)
        return None


async def add_vip_to_player(player_id: str, hours: int, channel_id: int) -> tuple[bool, str]:
    """Vergibt VIP an einen Spieler auf allen 3 Servern. hours=0 für Lifetime, sonst +hours auf aktuelle Zeit"""
    
    # Aktuelle VIP-Zeit abrufen
    current_exp = await get_vip_expiration(player_id, channel_id)
    
    # Lifetime VIP Check
    if current_exp and current_exp.startswith("3000-"):
        await log_debug(f"⚠️ Player {player_id} hat bereits Lifetime VIP - keine Änderung", channel_id)
        return False, current_exp
    
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Berechne neue Ablaufzeit
            if hours == 0:
                # Lifetime VIP bis 3000-01-01
                new_exp = "3000-01-01T01:00:00.000000Z"
            else:
                # Parse aktuelle Zeit oder nutze jetzt
                if current_exp:
                    current_exp_clean = current_exp.removesuffix("Z").removesuffix("+00:00")
                    try:
                        if '.' in current_exp_clean:
                            current_exp_clean = current_exp_clean.split('.')[0]
                        if 'T' in current_exp_clean:
                            base_time = datetime.fromisoformat(current_exp_clean)
                        else:
                            base_time = datetime.strptime(current_exp_clean, "%Y-%m-%d %H:%M:%S")
                        base_time = base_time.replace(tzinfo=timezone.utc)
                    except Exception as e:
                        await log_debug(f"⚠️ Parse-Fehler für {current_exp}, nutze jetzt: {e}", channel_id)
                        base_time = datetime.now(timezone.utc)
                else:
                    base_time = datetime.now(timezone.utc)
                
                # Addiere Stunden
                new_base = base_time + timedelta(hours=hours)
                new_exp = new_base.isoformat().replace("+00:00", "Z")
            
            await log_debug(f"➕ Neue VIP-Zeit berechnet: {new_exp}", channel_id)
            
            # VIP hinzufügen auf ALLEN 3 Servern
            payload = {
                "player_id": player_id,
                "expiration": new_exp,
                "description": f"Admin Ticket-Support: +{hours}h" if hours > 0 else "Lifetime VIP (Ticket-Support)"
            }
            
            success_count = 0
            for idx, server_url in enumerate(API_SERVERS, 1):
                try:
                    # Entferne altes VIP (falls vorhanden)
                    try:
                        async with session.post(
                            f"{server_url}/remove_vip",
                            headers=API_HEADERS,
                            json={"player_id": player_id},
                            ssl=False
                        ) as resp:
                            await log_debug(f"🗑️ Server {idx}: VIP entfernt ({resp.status})", channel_id)
                    except Exception as e:
                        await log_debug(f"⚠️ Server {idx}: VIP-Remove Fehler (okay): {e}", channel_id)
                    
                    # Neues VIP hinzufügen
                    async with session.post(
                        f"{server_url}/add_vip",
                        headers=API_HEADERS,
                        json=payload,
                        ssl=False
                    ) as resp:
                        if resp.status == 200:
                            await log_debug(f"✅ Server {idx}: VIP erfolgreich vergeben", channel_id)
                            success_count += 1
                        else:
                            error_text = await resp.text()
                            await log_debug(f"❌ Server {idx}: add_vip Fehler {resp.status}: {error_text[:200]}", channel_id)
                except Exception as e:
                    await log_debug(f"❌ Server {idx}: Exception: {e}", channel_id)
            
            if success_count > 0:
                await log_debug(f"✅ VIP auf {success_count}/3 Servern vergeben", channel_id)
                return True, new_exp
            else:
                return False, ""
    
    except Exception as e:
        await log_debug(f"❌ add_vip_to_player Exception: {e}", channel_id)
        return False, ""


# === ID INPUT MODAL ===
class IDInputModal(Modal, title="Steam-ID oder Ingame-Name"):
    input = TextInput(label="ID oder Name", placeholder="z. B. 7656119... oder Ingame-Name",
                      style=discord.TextStyle.short)

    def __init__(self, channel_id: int):
        super().__init__()
        self.channel_id = channel_id

    async def on_submit(self, interaction: discord.Interaction):
        # Response SOFORT senden (verhindert "Etwas ist schiefgelaufen")
        await interaction.response.send_message("🔍 Danke! Ich checke das jetzt...", ephemeral=True)
        
        input_text = self.input.value.strip()

        direct_id = extract_player_id(input_text)
        ingame_name = extract_ingame_name(input_text) or input_text

        id_changed = False
        if direct_id:
            if direct_id != ticket_player_id[self.channel_id]:
                ticket_player_id[self.channel_id] = direct_id
                id_changed = True

        if ingame_name and not direct_id:
            await search_and_set_best_player_id(self.channel_id, name=ingame_name)
            if ticket_player_id[self.channel_id]:
                id_changed = True

        if id_changed:
            await update_escalation_embed(self.channel_id)

        # Ban-Grund automatisch abrufen und zur Historie hinzufügen
        await add_player_info_to_history(self.channel_id)
        await add_ban_reason_to_history(self.channel_id)

        ticket_id_input_used[self.channel_id] = True

        ticket_history[self.channel_id].append({"role": "user", "content": f"[ID/Name eingegeben: {input_text}]"})
        
        # Button aus ursprünglicher Nachricht entfernen
        try:
            if interaction.message:
                await interaction.message.edit(view=None)
        except:
            pass
        
        # KI-Response mit Ban-Grund-Info
        await send_ki_response(interaction.channel, self.channel_id)


# === PLAYER-SUCHE ===
async def search_and_set_best_player_id(channel_id: int, name: Optional[str] = None):
    if not name:
        return

    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            params = {
                "player_name": name,
                "exact_name_match": "False",
                "ignore_accent": "True",
                "page_size": 50  # Mehr Ergebnisse für besseres Matching
            }
            async with session.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params=params,
                ssl=False
            ) as resp:
                if resp.status != 200:
                    await log_debug(f"❌ Name-Suche Status {resp.status}", channel_id)
                    return

                data = await resp.json()
                players = data.get("result", {}).get("players", [])
                if not isinstance(players, list) or not players:
                    await log_debug(f"❌ Keine Players für '{name}' gefunden", channel_id)
                    return
                
                await log_debug(f"🔍 {len(players)} Players gefunden für '{name}'", channel_id)

                # Prüfe auf exakte Übereinstimmung (100%)
                exact_match = None
                for player in players:
                    player_names = player.get("names", [])
                    for name_entry in player_names:
                        player_name = name_entry.get("name", "")
                        if player_name.lower() == name.lower():
                            exact_match = player
                            await log_debug(f"✅ Exakte Übereinstimmung gefunden: {player_name}", channel_id)
                            break
                    if exact_match:
                        break
                
                # Falls exakte Übereinstimmung, nimm diese
                if exact_match:
                    best = exact_match
                else:
                    # Sonst: Sortiere nach neuester Aktivität
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
                    best = players_sorted[0] if players_sorted else None
                    await log_debug(f"🕒 Keine exakte Übereinstimmung, nehme neuesten: {best.get('names', [{}])[0].get('name', 'Unknown') if best else 'None'}", channel_id)

                if best:
                    best_id = best.get("player_id")
                    if best_id:
                        old_id = ticket_player_id[channel_id]
                        if best_id != old_id:
                            ticket_player_id[channel_id] = best_id
                            best_name = best.get("names", [{}])[0].get("name", "Unknown")
                            await log_debug(f"✅ Neue ID gesetzt: {best_id} (Name: {best_name}) - vorher {old_id}", channel_id)
                            await update_escalation_embed(channel_id)

    except Exception as e:
        await log_debug(f"❌ Player-Suche Exception: {e}", channel_id)


async def add_player_info_to_history(channel_id: int):
    """Fügt Spieler-Info zur KI-Historie hinzu + zeigt sie im Ticket an"""
    player_id = ticket_player_id[channel_id]
    if not player_id or ticket_player_info_added[channel_id]:
        return

    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                f"{API_BASE_URL}/get_players_history",
                headers=API_HEADERS,
                params={"player_id": player_id, "page_size": 20},
                ssl=False
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    player_data = data.get("result", [])
                    
                    if isinstance(player_data, list) and player_data:
                        player = player_data[0]
                        player_name = player.get("name", "Unbekannt")
                        sessions = player.get("sessions_count", 0)
                        playtime = player.get("total_playtime_seconds", 0)
                        playtime_hours = round(playtime / 3600, 1)
                        
                        # Für KI
                        limited = player_data[:10]
                        summary = f"Spieler-Info (ID {player_id}, Name: {player_name}): Sessions: {sessions}, Spielzeit: {playtime_hours}h. Letzte Aktivitäten: {json.dumps(limited, ensure_ascii=False, default=str)}"
                        
                        # Im Ticket anzeigen
                        channel = bot.get_channel(channel_id)
                        if channel:
                            info_embed = discord.Embed(
                                title="📄 Spieler-Informationen gefunden",
                                description=f"**Name:** {player_name}\n**Steam-ID:** `{player_id}`\n**Sessions:** {sessions}\n**Spielzeit:** {playtime_hours}h",
                                color=discord.Color.blue()
                            )
                            await channel.send(embed=info_embed)
                    else:
                        summary = f"Spieler-Info (ID {player_id}): Keine Daten verfügbar."
                    
                    # Verhindere Memory-Leak: Limitiere Historie-Länge
                    if len(ticket_history[channel_id]) >= MAX_HISTORY_LENGTH:
                        # Behalte System-Prompt + letzte Nachrichten
                        system_msgs = [m for m in ticket_history[channel_id] if m.get("role") == "system"][:1]
                        recent_msgs = [m for m in ticket_history[channel_id] if m.get("role") != "system"][-(MAX_HISTORY_LENGTH-5):]
                        ticket_history[channel_id] = system_msgs + recent_msgs
                    
                    ticket_history[channel_id].append({"role": "system", "content": summary})
                    ticket_player_info_added[channel_id] = True
                    await log_debug("✅ Player-Info zur KI-History hinzugefügt", channel_id)
    except asyncio.TimeoutError:
        await log_debug(f"Player-Info Timeout nach {HTTP_TIMEOUT}s", channel_id)
    except Exception as e:
        await log_debug(f"Player-Info Abruf Exception: {e}", channel_id)


async def add_ban_reason_to_history(channel_id: int):
    """Ruft den letzten Ban-Grund ab und fügt ihn strukturiert zur KI-Historie hinzu - durchsucht alle 3 Server"""
    player_id = ticket_player_id[channel_id]
    if not player_id:
        return

    ban_info_parts = []
    
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # Alle 3 Server durchsuchen
            for server_idx, server_url in enumerate(API_SERVERS, 1):
                server_bans = []
                
                # 1. get_ban - Aktuelle Bans
                try:
                    async with session.get(
                        f"{server_url}/get_ban",
                        headers=API_HEADERS,
                        params={"player_id": player_id},
                        ssl=False
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("result")
                            if result and isinstance(result, list):
                                for ban in result:
                                    ban_type = ban.get("type", "Unknown")
                                    reason = ban.get("reason", "Kein Grund")
                                    timestamp = ban.get("timestamp", "Unbekannt")
                                    server_bans.append(f"AKTIVER BAN [Server {server_idx}] ({ban_type}): {reason} (seit {timestamp})")
                                await log_debug(f"✅ Server {server_idx} get_ban: {len(result)} aktive Bans", channel_id)
                except Exception as e:
                    await log_debug(f"❌ Server {server_idx} get_ban Fehler: {e}", channel_id)

                # 2. get_blacklist_records - Blacklist
                try:
                    async with session.get(
                        f"{server_url}/get_blacklist_records",
                        headers=API_HEADERS,
                        params={"player_id": player_id, "page_size": 10},
                        ssl=False
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            result = data.get("result")
                            if result and isinstance(result, list) and result:
                                for record in result[:2]:  # Max 2 pro Server
                                    reason = record.get("reason", "Kein Grund")
                                    timestamp = record.get("timestamp", "Unbekannt")
                                    server_bans.append(f"BLACKLIST [Server {server_idx}]: {reason} (am {timestamp})")
                                await log_debug(f"✅ Server {server_idx} blacklist: {len(result)} Einträge", channel_id)
                except Exception as e:
                    await log_debug(f"❌ Server {server_idx} blacklist Fehler: {e}", channel_id)

                # 3. get_historical_logs - Punishment-Historie
                try:
                    async with session.get(
                        f"{server_url}/get_historical_logs",
                        headers=API_HEADERS,
                        params={"player_name": "", "player_id": player_id, "limit": 50},
                        ssl=False
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            logs = data.get("result", [])
                            if isinstance(logs, list) and logs:
                                ban_actions = ["BAN", "TEMPBAN", "PERMABAN", "BLACKLIST"]
                                last_bans = []
                                for log in logs:
                                    action = log.get("action", "").upper()
                                    if any(ban_type in action for ban_type in ban_actions):
                                        last_bans.append(log)
                                        if len(last_bans) >= 2:  # Max 2 pro Server
                                            break
                                
                                for ban_log in last_bans:
                                    action = ban_log.get("action", "Ban")
                                    reason = ban_log.get("message", "Kein Grund")
                                    timestamp = ban_log.get("creation_time", "Unbekannt")
                                    server_bans.append(f"HISTORIE [Server {server_idx}] ({action}): {reason} (am {timestamp})")
                                
                                if last_bans:
                                    await log_debug(f"✅ Server {server_idx} history: {len(last_bans)} Ban-Logs", channel_id)
                except Exception as e:
                    await log_debug(f"❌ Server {server_idx} history Fehler: {e}", channel_id)

                # 4. Fallback: get_players_history
                if not server_bans:
                    try:
                        async with session.get(
                            f"{server_url}/get_players_history",
                            headers=API_HEADERS,
                            params={"player_id": player_id, "page_size": 50},
                            ssl=False
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                punishments = data.get("result", [])
                                
                                if isinstance(punishments, list) and punishments:
                                    ban_actions = ["ban", "perma", "temp_ban", "blacklist"]
                                    for punishment in punishments:
                                        action = punishment.get("action", "").lower()
                                        if any(ban_type in action for ban_type in ban_actions):
                                            reason = punishment.get("reason", "Kein Grund")
                                            timestamp = punishment.get("timestamp", "Unbekannt")
                                            server_bans.append(f"PLAYER HISTORY [Server {server_idx}] ({action}): {reason} (am {timestamp})")
                                            break
                    except Exception as e:
                        await log_debug(f"❌ Server {server_idx} player_history Fehler: {e}", channel_id)
                
                # Füge Server-Bans zu Gesamt-Liste hinzu
                ban_info_parts.extend(server_bans)

        # Ban-Info zur Historie hinzufügen
        if ban_info_parts:
            ban_summary = "\n".join(ban_info_parts)
            ban_message = (
                f"LETZTER BAN-GRUND gefunden:\n"
                f"{ban_summary}\n\n"
                f"Teile dem User den Grund mit und entscheide basierend darauf, ob AUTO_UNBAN oder Eskalation."
            )
            ticket_history[channel_id].append({"role": "system", "content": ban_message})
            await log_debug(f"✅ Ban-Info zur Historie hinzugefügt: {len(ban_info_parts)} Einträge", channel_id)
        else:
            ticket_history[channel_id].append({
                "role": "system",
                "content": "Keine Ban-/Blacklist-Informationen für diesen Spieler gefunden. Evtl. nur Votekick oder temporär."
            })
            await log_debug("⚠️ Keine Ban-Infos in allen Endpoints gefunden", channel_id)
            
    except Exception as e:
        await log_debug(f"❌ Ban-Grund Abruf genereller Fehler: {e}", channel_id)


# === EMBED AKTUALISIEREN ===
async def update_escalation_embed(channel_id: int, summary: str = None):
    admin_channel = bot.get_channel(ADMIN_SUMMARY_CHANNEL_ID)
    if not admin_channel:
        return

    channel = bot.get_channel(channel_id)
    if not channel:
        return

    # Nutze summary falls vorhanden, sonst Default
    description = summary if summary else "Warte auf Infos/ID vom User..."
    
    embed = discord.Embed(
        title="🎫 Ticket Eskalation",
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
            timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(
                    f"{API_BASE_URL}/get_players_history",
                    headers=API_HEADERS,
                    params={"player_id": player_id, "page_size": 10},
                    ssl=False
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        punishments = data.get("result", []) or []
                        if isinstance(punishments, list) and punishments:
                            pun_str = "\n".join(
                                [f"{p.get('action', 'Unknown')} am {p.get('timestamp', 'N/A')}" for p in punishments[:5]])
                            embed.add_field(name="Letzte Punishments", value=pun_str or "Keine", inline=False)
        except asyncio.TimeoutError:
            await log_debug(f"Eskalation Player-Info Timeout", channel_id)
        except Exception as e:
            await log_debug(f"Eskalation Player-Info Fehler: {e}", channel_id)

    msg = ticket_escalation_message[channel_id]
    try:
        if msg:
            try:
                await msg.edit(embed=embed, view=view)
            except discord.NotFound:
                # Message wurde gelöscht, erstelle neue
                msg = await admin_channel.send(embed=embed, view=view)
                ticket_escalation_message[channel_id] = msg
            except discord.HTTPException as e:
                await log_debug(f"Discord API Fehler beim Embed-Update: {e}", channel_id)
        else:
            msg = await admin_channel.send(embed=embed, view=view)
            ticket_escalation_message[channel_id] = msg
    except discord.Forbidden:
        await log_debug(f"Keine Berechtigung für Admin-Channel {ADMIN_SUMMARY_CHANNEL_ID}", channel_id)
    except Exception as e:
        await log_debug(f"Fehler beim Senden/Editieren des Eskalations-Embeds: {e}", channel_id)


# === ID & NAME ERKENNEN ===
def extract_player_id(text: str):
    match = re.search(r'(7656119\d{10}|[a-f0-9]{32})', text)
    return match.group(0) if match else None


def extract_ingame_name(text: str):
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


def is_report_about_other_player(message: str) -> bool:
    """Erkennt ob es sich um einen Report über einen ANDEREN Spieler handelt"""
    report_keywords = [
        "report", "cheater", "cheat", "hacker", "hack", "aimbot", 
        "wallhack", "esp", "verdacht", "verdächtig", "suspicious",
        "der spieler", "ein spieler", "jemand", "player", 
        "eben noch", "gerade", "war auf", "ist auf",
        "sollte", "wohlmöglich", "eventuell", "vielleicht"
    ]
    
    message_lower = message.lower()
    
    # Wenn Report-Keywords + nicht über sich selbst ("ich", "mein", "bin")
    has_report_keyword = any(keyword in message_lower for keyword in report_keywords)
    is_about_self = any(word in message_lower for word in ["ich bin", "bin ich", "wurde ich", "mein ban", "meine id", "ich wurde"])
    
    return has_report_keyword and not is_about_self


# === KI-ANTWORT ===
async def send_ki_response(channel: discord.TextChannel, channel_id: int):
    if ticket_closed[channel_id] or admin_active[channel_id]:
        return

    # Memory-Leak Prevention: Limitiere Historie
    history = ticket_history[channel_id]
    if len(history) > MAX_HISTORY_LENGTH:
        system_msgs = [m for m in history if m.get("role") == "system"][:1]
        recent_msgs = [m for m in history if m.get("role") != "system"][-(MAX_HISTORY_LENGTH-2):]
        history = system_msgs + recent_msgs
        ticket_history[channel_id] = history

    try:
        payload = {
            "model": "grok-4-1-fast-reasoning",
            "messages": history,
            "max_tokens": 200,
            "temperature": 0.8
        }
        
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                "https://api.x.ai/v1/chat/completions",
                json=payload,
                headers=GROK_HEADERS
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    await log_debug(f"KI-API Fehler: {response.status} - {error_text[:200]}", channel_id)
                    return

                data = await response.json()
                bot_reply = data["choices"][0]["message"]["content"].strip()

        user_reply = bot_reply
        admin_summary = ""
        do_temp_unban = False
        ask_id = False

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
            await log_debug(f"📢 Admin-Zusammenfassung erstellt (wird nicht an User gesendet)", channel_id)

        if "**ASK_ID:**" in bot_reply and not ticket_asked_id[channel_id]:
            parts = bot_reply.split("**ASK_ID:**", 1)
            user_reply = parts[0].strip()
            ask_id = True
            ticket_asked_id[channel_id] = True  # Nur einmal Button

        view = None
        if ask_id:
            view = View()
            button = Button(label="ID/Name eingeben", style=discord.ButtonStyle.primary)

            async def button_callback(interaction: discord.Interaction):
                modal = IDInputModal(channel_id)
                await interaction.response.send_modal(modal)

            button.callback = button_callback
            view.add_item(button)

        if user_reply:
            try:
                await channel.send(user_reply, view=view if ask_id else None)
            except discord.HTTPException as e:
                if e.status == 429:  # Rate limit
                    retry_after = e.retry_after if hasattr(e, 'retry_after') else 5
                    await log_debug(f"Rate-Limit erreicht, warte {retry_after}s", channel_id)
                    await asyncio.sleep(retry_after)
                    await channel.send(user_reply, view=view if ask_id else None)
                else:
                    await log_debug(f"Discord API Fehler beim Senden: {e}", channel_id)
            except Exception as e:
                await log_debug(f"Fehler beim Senden der KI-Antwort: {e}", channel_id)

        if do_temp_unban:
            player_id = ticket_player_id[channel_id]
            if player_id:
                await api_clear_temp_ban(player_id, channel_id)

        if admin_summary:
            await update_escalation_embed(channel_id, summary=admin_summary)

        ticket_history[channel_id].append({"role": "assistant", "content": bot_reply})

    except asyncio.TimeoutError:
        await log_debug(f"KI-API Timeout nach {HTTP_TIMEOUT}s", channel_id)
        try:
            await channel.send("⚠️ Die KI braucht gerade etwas länger. Bitte hab einen Moment Geduld.")
        except:
            pass
    except KeyError as e:
        await log_debug(f"KI-API Response-Format Fehler: {e}", channel_id)
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


@bot.event
async def on_ready():
    await log_debug("Bot online – ID-Anfrage nur einmal + Button nur einmal")


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
            ticket_feedback_sent[channel.id] = False
            ticket_player_id[channel.id] = ""
            ticket_player_info_added[channel.id] = False
            admin_active[channel.id] = False
            ticket_asked_id[channel.id] = False
            ticket_id_input_used[channel.id] = False
            ticket_escalation_message[channel.id] = None
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

        await log_debug(f"💬 Owner-Nachricht in Ticket {channel_id}: {message.content[:100]}", channel_id)

        # Prüfe ob es ein Report über anderen Spieler ist
        is_report = is_report_about_other_player(message.content)
        if is_report:
            await log_debug(f"🚨 Report über anderen Spieler erkannt - keine ID-Suche", channel_id)
            ticket_history[channel_id].append({
                "role": "system", 
                "content": "[WICHTIG] User reportet einen ANDEREN Spieler (kein eigenes Ban-Problem). KEINE ID-Abfrage, direkt eskalieren!"
            })
            ticket_history[channel_id].append({"role": "user", "content": message.content})
            await send_ki_response(message.channel, channel_id)
            return

        # Normale Ban-Problem-Behandlung
        direct_id = extract_player_id(message.content)
        ingame_name = extract_ingame_name(message.content)

        id_changed = False
        if direct_id and direct_id != ticket_player_id[channel_id]:
            ticket_player_id[channel_id] = direct_id
            id_changed = True

        if ingame_name and not ticket_id_input_used[channel_id]:
            await search_and_set_best_player_id(channel_id, name=ingame_name)
            if ticket_player_id[channel_id]:
                id_changed = True

        if id_changed:
            await update_escalation_embed(channel_id)

        await add_player_info_to_history(channel_id)

        ticket_history[channel_id].append({"role": "user", "content": message.content})
        await send_ki_response(message.channel, channel_id)

        if ticket_closed[channel_id] and not ticket_feedback_sent[channel_id]:
            await send_feedback_message(message.channel)
            ticket_feedback_sent[channel_id] = True

    await bot.process_commands(message)


# === GRACEFUL SHUTDOWN FÜR PM2 ===
async def shutdown():
    """Sauberes Herunterfahren des Bots"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] Bot wird heruntergefahren...")
    await log_debug("Bot-Shutdown initiiert")
    await bot.close()
    print(f"[{timestamp}] Bot erfolgreich beendet.")

def shutdown_handler(signum, frame):
    """Signal-Handler für SIGINT/SIGTERM"""
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] Shutdown-Signal empfangen (Signal {signum})")
    
    # Erstelle Task für async shutdown
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(shutdown())
    else:
        loop.run_until_complete(shutdown())

signal.signal(signal.SIGINT, shutdown_handler)
signal.signal(signal.SIGTERM, shutdown_handler)


# === BOT STARTEN MIT ERROR HANDLING ===
if __name__ == "__main__":
    try:
        token = os.getenv("DISCORD_TOKEN")
        if not token:
            raise ValueError("DISCORD_TOKEN fehlt in .env!")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot wird gestartet...")
        bot.run(token)
    except KeyboardInterrupt:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Bot durch Benutzer gestoppt.")
    except Exception as e:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Kritischer Fehler beim Bot-Start: {e}")
        sys.exit(1)