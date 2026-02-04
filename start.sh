#!/bin/bash

# GBG Discord Bot Startup Script für Linux/PM2
# Dieses Skript installiert Dependencies und startet den Bot mit PM2

echo "=== GBG Discord Bot Startup ==="
echo ""

# Prüfe ob Python 3 installiert ist
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 ist nicht installiert. Bitte installiere Python 3.8 oder höher."
    exit 1
fi

# Prüfe Python Version
PYTHON_VERSION=$(python3 --version | cut -d' ' -f2 | cut -d'.' -f1,2)
echo "✓ Python Version: $PYTHON_VERSION"

# Prüfe ob PM2 installiert ist
if ! command -v pm2 &> /dev/null; then
    echo "❌ PM2 ist nicht installiert."
    echo "Installiere PM2 mit: npm install -g pm2"
    exit 1
fi

echo "✓ PM2 ist installiert"

# Prüfe ob .env existiert
if [ ! -f .env ]; then
    echo "❌ .env Datei nicht gefunden!"
    echo "Kopiere .env.example zu .env und fülle die Werte aus:"
    echo "  cp .env.example .env"
    echo "  nano .env"
    exit 1
fi

echo "✓ .env Datei gefunden"

# Erstelle logs Verzeichnis falls nicht vorhanden
mkdir -p logs

# Installiere/Update Python Dependencies
echo ""
echo "=== Python Dependencies werden installiert... ==="
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt

if [ $? -ne 0 ]; then
    echo "❌ Fehler beim Installieren der Python-Pakete"
    exit 1
fi

echo "✓ Dependencies installiert"
echo ""

# PM2 Konfiguration anpassen (CWD auf aktuelles Verzeichnis setzen)
CURRENT_DIR=$(pwd)
sed -i "s|cwd: '.*'|cwd: '$CURRENT_DIR'|g" ecosystem.config.js

# Bot mit PM2 starten
echo "=== Bot wird mit PM2 gestartet... ==="
pm2 start ecosystem.config.js

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Bot erfolgreich gestartet!"
    echo ""
    echo "Nützliche PM2 Befehle:"
    echo "  pm2 logs gbg-discord-bot    # Logs anzeigen"
    echo "  pm2 restart gbg-discord-bot # Bot neustarten"
    echo "  pm2 stop gbg-discord-bot    # Bot stoppen"
    echo "  pm2 status                  # Status anzeigen"
    echo "  pm2 monit                   # Monitoring"
    echo "  pm2 save                    # Konfiguration speichern"
    echo "  pm2 startup                 # Autostart aktivieren"
    echo ""
else
    echo "❌ Fehler beim Starten des Bots"
    exit 1
fi
