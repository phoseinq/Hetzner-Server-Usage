#!/usr/bin/env bash
# Hetzner Server Manager Bot — installer
# Run as root from the repo directory:  bash install.sh
# Installs system packages, the Python venv, the systemd service and
# the `hetzner` management CLI. Safe to re-run (idempotent).
set -euo pipefail

SERVICE="hetzner-bot"
DIR="$(cd "$(dirname "$0")" && pwd)"

RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; PLAIN='\033[0m'
step() { echo -e "${CYAN}==>${PLAIN} $1"; }
fail() { echo -e "${RED}✗ $1${PLAIN}" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "Run as root: sudo bash install.sh"
command -v apt-get >/dev/null 2>&1 || fail "This installer supports Debian/Ubuntu (apt) only."
[ -f "$DIR/main.py" ] || fail "Run install.sh from inside the repo directory."
cd "$DIR"

step "Installing system packages (python3, venv, git)..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git >/dev/null

step "Setting up the Python virtualenv..."
[ -d venv ] || python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

ENV_CREATED=0
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    ENV_CREATED=1
fi

step "Installing the systemd service ($SERVICE)..."
sed "s|__DIR__|$DIR|g" hetzner-bot.service > "/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null 2>&1

step "Installing the 'hetzner' management command..."
install -m 755 hetzner-bot.sh /usr/local/bin/hetzner
rm -f /usr/local/bin/hetzner-bot

echo
echo -e "${GREEN}✅ Installation complete.${PLAIN}"
echo
if [ "$ENV_CREATED" = "1" ]; then
    echo -e "  1) Fill in your tokens:  ${CYAN}nano $DIR/.env${PLAIN}"
    echo -e "  2) Start the bot:        ${CYAN}hetzner start${PLAIN}"
else
    echo -e "  Restart to apply:        ${CYAN}hetzner restart${PLAIN}"
fi
echo
echo -e "  Manage anytime:          ${CYAN}hetzner${PLAIN} (menu) — or: hetzner update|status|logs|tz"
