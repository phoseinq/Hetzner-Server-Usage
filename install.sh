#!/usr/bin/env bash
# Installs the bot as a systemd service. Run as root from the repo directory:
#   bash install.sh
set -e

cd "$(dirname "$0")"
DIR="$(pwd)"

echo "📦 Installing system packages..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git

echo "🐍 Creating virtualenv and installing requirements..."
python3 -m venv venv
./venv/bin/pip install --quiet --upgrade pip
./venv/bin/pip install --quiet -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
    echo ""
    echo "⚠️  .env created from template — fill in your tokens:"
    echo "    nano $DIR/.env"
fi

echo "⚙️  Installing systemd service..."
sed "s|__DIR__|$DIR|g" hetzner-bot.service > /etc/systemd/system/hetzner-bot.service
systemctl daemon-reload
systemctl enable hetzner-bot

echo "🧰 Installing the 'hetzner-bot' management command..."
install -m 755 "$DIR/hetzner-bot.sh" /usr/local/bin/hetzner-bot

echo ""
echo "✅ Done. After filling .env, start the bot with:"
echo "    systemctl start hetzner-bot"
echo ""
echo "Manage the bot anytime with the interactive CLI:"
echo "    hetzner-bot              # menu"
echo "    hetzner-bot update       # or: start|stop|restart|status|logs|env"
