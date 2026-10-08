#!/bin/sh
# Instala (ou reinstala) o agendamento do macOS que roda heatmap.py de hora em hora.
#
# Uso: scripts/install-launchd.sh [intervalo_em_segundos]
#
# Os caminhos do vault vêm do config.local.json (veja o README). Para remover:
#   launchctl bootout gui/$(id -u)/$LABEL && rm ~/Library/LaunchAgents/$LABEL.plist
set -eu

LABEL="${HEATMAP_LABEL:-com.$(id -un).obsidian-heatmap}"
INTERVAL="${1:-3600}"
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(command -v python3)"
# Shims do pyenv não funcionam no launchd (sem o shell configurado): usa o binário real.
if command -v pyenv >/dev/null 2>&1; then PYTHON="$(pyenv which python3)"; fi
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

[ -f "$DIR/config.local.json" ] || { echo "Crie $DIR/config.local.json antes (veja o README)."; exit 1; }

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PYTHON</string>
    <string>$DIR/heatmap.py</string>
  </array>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$DIR/heatmap.log</string>
  <key>StandardErrorPath</key><string>$DIR/heatmap.log</string>
</dict>
</plist>
PLIST

plutil -lint "$PLIST" >/dev/null
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Agendamento $LABEL instalado: a cada $INTERVAL s. Log em $DIR/heatmap.log"
