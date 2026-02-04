# 🚀 Deployment Anleitung - GBG Discord Bot

## 📤 Teil 1: Code auf GitHub pushen (Windows)

### Erste Einrichtung (einmalig)

```powershell
# Im Projektverzeichnis
cd "e:\Discord Bot\GBG_KI"

# Git initialisieren (falls noch nicht geschehen)
git init

# GitHub Repository als Remote hinzufügen
git remote add origin https://github.com/kilian558/GBG_KI.git

# Prüfen ob Remote korrekt ist
git remote -v
```

### Code hochladen

```powershell
# Alle Dateien zum Commit hinzufügen
git add .

# Commit erstellen
git commit -m "Initial commit - Optimized Discord Bot with PM2 support"

# Auf GitHub pushen (main branch)
git push -u origin main

# Falls main nicht existiert, versuche master:
# git push -u origin master

# Falls Push nicht funktioniert (Repository ist neu):
git branch -M main
git push -u origin main --force
```

### Bei Updates später

```powershell
cd "e:\Discord Bot\GBG_KI"
git add .
git commit -m "Update: Beschreibung der Änderungen"
git push
```

---

## 🐧 Teil 2: Bot auf Linux Server installieren

### Schritt 1: Server vorbereiten

```bash
# Als root oder mit sudo
# System aktualisieren
sudo apt update && sudo apt upgrade -y

# Benötigte Pakete installieren
sudo apt install -y python3 python3-pip python3-venv git curl

# Node.js & npm installieren (für PM2)
curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash -
sudo apt install -y nodejs

# PM2 global installieren
sudo npm install -g pm2

# Prüfen ob alles installiert ist
python3 --version
node --version
npm --version
pm2 --version
```

### Schritt 2: Code vom GitHub klonen

```bash
# In Home-Verzeichnis oder gewünschten Pfad
cd ~

# Repository klonen
git clone https://github.com/kilian558/GBG_KI.git

# In Projekt-Verzeichnis wechseln
cd GBG_KI

# Dateiberechtigungen setzen
chmod +x start.sh
```

### Schritt 3: Umgebungsvariablen konfigurieren

```bash
# .env Datei erstellen
cp .env.example .env

# .env Datei bearbeiten
nano .env
```

**Füge deine Keys ein:**
```env
DISCORD_TOKEN=dein_echter_discord_token
API_BASE_URL=https://gbg-hll.com:64302/api
API_KEY=dein_echter_api_key
GROK_API_KEY=dein_echter_grok_key
```

**Speichern:** `Ctrl+O` → Enter → `Ctrl+X`

### Schritt 4: ecosystem.config.js anpassen

```bash
nano ecosystem.config.js
```

**Passe den Pfad an (Zeile 5):**
```javascript
cwd: '/root/GBG_KI',  // <- Dein tatsächlicher Pfad
```

**Oder automatisch:**
```bash
# Aktuellen Pfad automatisch setzen
CURRENT_DIR=$(pwd)
sed -i "s|cwd: '.*'|cwd: '$CURRENT_DIR'|g" ecosystem.config.js
```

### Schritt 5: Bot mit Start-Skript starten

```bash
# Start-Skript ausführen (macht alles automatisch)
./start.sh
```

**Das Skript macht:**
- ✅ Prüft alle Voraussetzungen
- ✅ Installiert Python-Dependencies
- ✅ Startet Bot mit PM2
- ✅ Zeigt Status an

---

## 🎛️ PM2 Befehle (wichtig!)

### Status & Monitoring

```bash
# Bot-Status anzeigen
pm2 status

# Live-Logs anzeigen
pm2 logs gbg-discord-bot

# Letzte 100 Zeilen Logs
pm2 logs gbg-discord-bot --lines 100

# Nur Fehler-Logs
pm2 logs gbg-discord-bot --err

# Monitoring Dashboard (interaktiv)
pm2 monit

# Detaillierte Infos
pm2 show gbg-discord-bot
```

### Bot-Kontrolle

```bash
# Bot neustarten
pm2 restart gbg-discord-bot

# Bot stoppen
pm2 stop gbg-discord-bot

# Bot starten (wenn gestoppt)
pm2 start gbg-discord-bot

# Bot komplett entfernen aus PM2
pm2 delete gbg-discord-bot

# Alle Prozesse neu laden
pm2 reload all

# PM2 komplett neustarten
pm2 kill
pm2 start ecosystem.config.js
```

### Logs & Cleanup

```bash
# Alle Logs löschen
pm2 flush

# Log-Dateien ansehen
tail -f ~/GBG_KI/logs/output.log
tail -f ~/GBG_KI/logs/error.log

# Alte Logs rotieren (automatisch)
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 10M
pm2 set pm2-logrotate:retain 7
```

---

## 🔄 Autostart einrichten (wichtig!)

Bot startet automatisch nach Server-Neustart:

```bash
# PM2 Startup-Skript generieren
pm2 startup

# Führe den angezeigten Befehl aus (sieht etwa so aus):
sudo env PATH=$PATH:/usr/bin pm2 startup systemd -u root --hp /root

# Aktuelle PM2-Prozesse speichern
pm2 save

# Prüfen ob Autostart funktioniert
systemctl status pm2-root
```

**Autostart testen:**
```bash
# Server neu starten
sudo reboot

# Nach Neustart prüfen
pm2 status
# Bot sollte automatisch laufen!
```

---

## 🔄 Updates vom GitHub ziehen

```bash
cd ~/GBG_KI

# Code aktualisieren
git pull

# Dependencies aktualisieren
pip3 install -r requirements.txt --upgrade

# Bot neu starten
pm2 restart gbg-discord-bot

# Logs checken
pm2 logs gbg-discord-bot --lines 50
```

---

## 🐛 Troubleshooting

### Bot startet nicht

```bash
# Direkt mit Python testen (ohne PM2)
cd ~/GBG_KI
python3 main.py

# Fehler werden direkt angezeigt
```

### Dependencies fehlen

```bash
cd ~/GBG_KI
pip3 install -r requirements.txt --upgrade --force-reinstall
```

### PM2 zeigt "errored"

```bash
# Logs checken
pm2 logs gbg-discord-bot --err --lines 100

# Bot aus PM2 entfernen und neu starten
pm2 delete gbg-discord-bot
pm2 start ecosystem.config.js
```

### .env wird nicht geladen

```bash
# Prüfe ob .env existiert
ls -la ~/GBG_KI/.env

# Prüfe Inhalt (verstecke Keys!)
cat .env | grep -v "TOKEN\|KEY"

# Prüfe Berechtigungen
chmod 600 .env
```

### Port/Firewall Probleme

```bash
# Bot braucht ausgehende Verbindungen zu:
# - Discord API (443)
# - Grok API (443)
# - RCON API (64302)

# Firewall prüfen
sudo ufw status

# Falls nötig, ausgehende Verbindungen erlauben
sudo ufw allow out 443
sudo ufw allow out 64302
```

### Memory-Probleme

```bash
# Speicher-Nutzung prüfen
pm2 list
free -h

# Memory-Limit in ecosystem.config.js anpassen:
# max_memory_restart: '500M'  <- erhöhen auf '1G'

pm2 restart gbg-discord-bot
```

---

## 📊 Monitoring & Alerts (optional)

### PM2 Plus (Web-Dashboard)

```bash
# Kostenlos registrieren auf pm2.io
pm2 link <secret_key> <public_key>

# Web-Dashboard unter pm2.io ansehen
```

### Discord Webhook für PM2-Alerts (optional)

```bash
npm install -g pm2-discord-webhook

# In ecosystem.config.js unter apps[0]:
# error_file: 'logs/error.log',
# out_file: 'logs/output.log',
# combine_logs: true
```

---

## 🎯 Quick Reference

### Tägliche Befehle

```bash
pm2 status                    # Status checken
pm2 logs gbg-discord-bot      # Logs ansehen
pm2 restart gbg-discord-bot   # Neustart
```

### Nach Code-Update

```bash
cd ~/GBG_KI
git pull
pip3 install -r requirements.txt --upgrade
pm2 restart gbg-discord-bot
pm2 logs gbg-discord-bot
```

### Bei Problemen

```bash
pm2 logs gbg-discord-bot --err --lines 100
python3 main.py  # Direkt testen
pm2 delete gbg-discord-bot && pm2 start ecosystem.config.js
```

---

## ✅ Checkliste für Erstinstallation

- [ ] Python 3.8+ installiert
- [ ] Node.js & PM2 installiert
- [ ] Repository geklont
- [ ] `.env` erstellt und ausgefüllt
- [ ] `ecosystem.config.js` Pfad angepasst
- [ ] `./start.sh` ausgeführt
- [ ] Bot läuft: `pm2 status`
- [ ] Autostart eingerichtet: `pm2 startup` + `pm2 save`
- [ ] Logs prüfen: `pm2 logs gbg-discord-bot`
- [ ] Im Discord testen

---

## 📞 Support

Bei Problemen:
1. Logs prüfen: `pm2 logs gbg-discord-bot --lines 100`
2. Bot direkt testen: `python3 main.py`
3. GitHub Issues: https://github.com/kilian558/GBG_KI/issues

**Bot läuft stabil? Dann:**
```bash
pm2 save  # Konfiguration sichern
```

🎉 **Fertig! Dein Bot läuft jetzt auf Linux mit PM2!**
