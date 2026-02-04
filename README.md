# GBG Discord Support Bot

Discord Bot für automatisierte Ticket-Verwaltung mit KI-Unterstützung (Grok AI) und RCON-Integration für Hell Let Loose Server.

## Features

- 🤖 Automatische KI-Antworten auf Support-Tickets
- 🎮 RCON-Integration für Ban-Management
- 👥 Admin-Eskalation bei komplexen Fällen
- 🔍 Automatische Spieler-ID-Erkennung
- 📊 Admin-Dashboard mit Echtzeit-Infos
- 🔄 PM2-Integration für zuverlässigen Betrieb

## Voraussetzungen

- Linux Server (Ubuntu 20.04+ empfohlen)
- Python 3.8 oder höher
- Node.js & npm (für PM2)
- Discord Bot Token
- Grok API Key
- RCON API Zugang

## Installation auf Linux

### 1. System-Pakete installieren

```bash
# System aktualisieren
sudo apt update && sudo apt upgrade -y

# Python und Dependencies
sudo apt install -y python3 python3-pip python3-venv git

# Node.js und PM2 installieren
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2
```

### 2. Bot-Code klonen/kopieren

```bash
# In gewünschtes Verzeichnis wechseln
cd /root  # oder ein anderer Pfad

# Repository klonen oder Dateien kopieren
# Option A: Mit Git
git clone <repository-url> GBG_KI

# Option B: Dateien manuell hochladen via SCP/SFTP
cd GBG_KI
```

### 3. Konfiguration

```bash
# .env Datei erstellen
cp .env.example .env
nano .env  # oder vi/vim
```

Fülle folgende Werte in der `.env` aus:
```
DISCORD_TOKEN=dein_discord_bot_token
API_BASE_URL=https://gbg-hll.com:64302/api
API_KEY=dein_rcon_api_key
GROK_API_KEY=dein_grok_api_key
```

### 4. Skript ausführbar machen und starten

```bash
chmod +x start.sh
./start.sh
```

Das Skript:
- ✓ Prüft alle Voraussetzungen
- ✓ Installiert Python-Pakete
- ✓ Startet den Bot mit PM2
- ✓ Richtet Logging ein

## PM2 Management

### Bot-Status prüfen
```bash
pm2 status
```

### Logs anzeigen
```bash
pm2 logs gbg-discord-bot
# oder
tail -f logs/output.log
```

### Bot neu starten
```bash
pm2 restart gbg-discord-bot
```

### Bot stoppen
```bash
pm2 stop gbg-discord-bot
```

### Bot aus PM2 entfernen
```bash
pm2 delete gbg-discord-bot
```

### Monitoring
```bash
pm2 monit
```

## Autostart bei Server-Neustart

```bash
# PM2 Startup-Skript generieren
pm2 startup

# Führe den angezeigten Befehl aus (systemctl enable...)

# Aktuelle PM2-Prozesse speichern
pm2 save
```

## Logs

Logs werden in folgenden Dateien gespeichert:
- `logs/output.log` - Standard-Output
- `logs/error.log` - Fehler
- `logs/combined.log` - Kombinierte Logs

## Konfiguration anpassen

### PM2 Einstellungen
Bearbeite `ecosystem.config.js`:
- `cwd`: Arbeitsverzeichnis (wird automatisch angepasst)
- `max_memory_restart`: Memory-Limit für Auto-Restart
- `instances`: Anzahl Instanzen (Standard: 1)

### Bot-Einstellungen
In `main.py`:
- `ACTIVE_TICKET_CATEGORIES`: Ticket-Kategorien
- `ADMIN_SUMMARY_CHANNEL_ID`: Admin-Channel-ID
- `DEBUG_CHANNEL_ID`: Debug-Channel-ID
- `ADMIN_ROLE_NAME`: Admin-Rollenname

### KI-Prompts
Bearbeite `prompts_de.json` für das KI-Verhalten.

## Troubleshooting

### Bot startet nicht
```bash
# Logs prüfen
pm2 logs gbg-discord-bot --lines 100

# Python-Fehler direkt testen
python3 main.py
```

### Dependencies fehlen
```bash
pip3 install -r requirements.txt --upgrade
```

### PM2 Probleme
```bash
pm2 kill  # PM2 komplett neustarten
pm2 start ecosystem.config.js
```

### Firewall/Ports
Der Bot benötigt ausgehende Verbindungen zu:
- Discord API (HTTPS)
- Grok API (HTTPS)
- RCON API (je nach Konfiguration)

## Updates

```bash
# Code aktualisieren (Git)
git pull

# Dependencies aktualisieren
pip3 install -r requirements.txt --upgrade

# Bot neu starten
pm2 restart gbg-discord-bot
```

## Support

Bei Problemen:
1. Logs prüfen: `pm2 logs gbg-discord-bot`
2. Python-Version prüfen: `python3 --version`
3. Dependencies prüfen: `pip3 list`
4. .env-Datei validieren

## Lizenz

Internes Projekt für GBG Hell Let Loose Community.
