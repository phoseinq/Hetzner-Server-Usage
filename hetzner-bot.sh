#!/usr/bin/env bash
# Interactive management CLI for the Hetzner Server Manager Bot.
# install.sh installs this as `hetzner`, so you can run it from anywhere.
# Subcommands also work non-interactively:
#   hetzner install|update|uninstall|start|stop|restart|status|logs|env

SERVICE="hetzner-bot"
DIR_DEFAULT="/opt/Hetzner-Server-Usage"
REPO_URL="https://github.com/phoseinq/Hetzner-Server-Usage.git"

SCRIPT_PATH="$(readlink -f "$0" 2>/dev/null || echo "$0")"
SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
if [ -f "$SCRIPT_DIR/main.py" ]; then DIR="$SCRIPT_DIR"; else DIR="$DIR_DEFAULT"; fi

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; PLAIN='\033[0m'

need_root() {
    if [ "$(id -u)" -ne 0 ]; then
        echo -e "${RED}This action needs root. Run with sudo.${PLAIN}"
        exit 1
    fi
}

svc_state() {
    if [ ! -f "/etc/systemd/system/$SERVICE.service" ]; then
        echo "not installed"
    else
        systemctl is-active "$SERVICE" 2>/dev/null || true
    fi
}

header() {
    clear
    local ver state st
    ver=$(git -C "$DIR" describe --tags --always 2>/dev/null || echo "-")
    state=$(svc_state)
    case "$state" in
        active)          st="${GREEN}● running${PLAIN}" ;;
        inactive|failed) st="${RED}● $state${PLAIN}" ;;
        *)               st="${YELLOW}● $state${PLAIN}" ;;
    esac
    echo -e "${CYAN}╔════════════════════════════════════════╗${PLAIN}"
    echo -e "${CYAN}║      Hetzner Server Manager Bot        ║${PLAIN}"
    echo -e "${CYAN}╚════════════════════════════════════════╝${PLAIN}"
    echo -e "  Status:  $st"
    echo -e "  Version: ${CYAN}$ver${PLAIN}"
    echo -e "  Path:    $DIR"
    echo
}

do_install() {
    need_root
    if [ ! -d "$DIR/.git" ]; then
        echo "⬇️  Cloning repository to $DIR..."
        apt-get update -qq
        apt-get install -y -qq git
        git clone "$REPO_URL" "$DIR"
    fi
    bash "$DIR/install.sh"
}

do_update() {
    need_root
    if [ ! -d "$DIR/.git" ]; then
        echo -e "${RED}Bot is not installed in $DIR. Use Install first.${PLAIN}"
        return
    fi
    cd "$DIR" || return
    echo "⬇️  Pulling latest version..."
    git pull
    ./venv/bin/pip install --quiet -r requirements.txt
    # keep the service unit in sync with the repo template
    sed "s|__DIR__|$DIR|g" hetzner-bot.service > "/etc/systemd/system/$SERVICE.service"
    install -m 755 hetzner-bot.sh /usr/local/bin/hetzner
    rm -f /usr/local/bin/hetzner-bot
    systemctl daemon-reload
    systemctl restart "$SERVICE"
    sleep 3
    if [ "$(systemctl is-active "$SERVICE")" = "active" ]; then
        echo -e "${GREEN}✅ Updated to $(git describe --tags --always) and running.${PLAIN}"
    else
        echo -e "${RED}❌ Service is not active after the update — check the logs.${PLAIN}"
    fi
}

do_uninstall() {
    need_root
    read -r -p "Remove the bot service? [y/N] " a
    case "$a" in [Yy]*) ;; *) echo "Cancelled."; return ;; esac
    systemctl disable --now "$SERVICE" 2>/dev/null
    rm -f "/etc/systemd/system/$SERVICE.service"
    systemctl daemon-reload
    rm -f /usr/local/bin/hetzner /usr/local/bin/hetzner-bot
    echo -e "${GREEN}Service and 'hetzner' command removed.${PLAIN}"
    read -r -p "Also DELETE $DIR (.env + cost history included)? [y/N] " b
    case "$b" in
        [Yy]*) rm -rf "$DIR"; echo "Directory deleted." ;;
        *)     echo "Directory kept." ;;
    esac
}

do_start()   { need_root; systemctl start "$SERVICE";   echo -e "${GREEN}Started.${PLAIN}"; }
do_stop()    { need_root; systemctl stop "$SERVICE";    echo -e "${YELLOW}Stopped.${PLAIN}"; }
do_restart() { need_root; systemctl restart "$SERVICE"; echo -e "${GREEN}Restarted.${PLAIN}"; }

do_status() {
    systemctl --no-pager status "$SERVICE"
}

do_logs() {
    echo -e "${CYAN}Live logs — press Ctrl+C to exit.${PLAIN}"
    journalctl -u "$SERVICE" -n 50 -f
}

do_env() {
    need_root
    ${EDITOR:-nano} "$DIR/.env"
    read -r -p "Restart the bot to apply changes? [Y/n] " a
    case "$a" in [Nn]*) ;; *) systemctl restart "$SERVICE"; echo "Restarted." ;; esac
}

case "$1" in
    install)          do_install;   exit 0 ;;
    update)           do_update;    exit 0 ;;
    uninstall|remove) do_uninstall; exit 0 ;;
    start)            do_start;     exit 0 ;;
    stop)             do_stop;      exit 0 ;;
    restart)          do_restart;   exit 0 ;;
    status)           do_status;    exit 0 ;;
    logs)             do_logs;      exit 0 ;;
    env)              do_env;       exit 0 ;;
    "") ;;  # no argument: open the interactive menu
    *)
        echo "Usage: hetzner [install|update|uninstall|start|stop|restart|status|logs|env]"
        exit 1
        ;;
esac

while true; do
    header
    echo "  1) 📥 Install              6) 🔁 Restart"
    echo "  2) ⬆️  Update               7) 📊 Status"
    echo "  3) 🗑  Uninstall            8) 📜 Logs (live)"
    echo "  4) ▶️  Start                9) ⚙️  Edit .env"
    echo "  5) ⏹  Stop                 0) 🚪 Exit"
    echo
    read -r -p "  Select an option: " choice
    echo
    case "$choice" in
        1) do_install ;;
        2) do_update ;;
        3) do_uninstall ;;
        4) do_start ;;
        5) do_stop ;;
        6) do_restart ;;
        7) do_status ;;
        8) do_logs ;;
        9) do_env ;;
        0) exit 0 ;;
        *) echo -e "${RED}Invalid option.${PLAIN}" ;;
    esac
    echo
    read -r -p "  Press Enter to continue..." _
done
