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
    # keep the service unit in sync with the repo template,
    # preserving the timezone the user picked with `hetzner tz`
    cur_tz=$(grep -E '^Environment=TZ=' "/etc/systemd/system/$SERVICE.service" 2>/dev/null | cut -d= -f3)
    sed "s|__DIR__|$DIR|g" hetzner-bot.service > "/etc/systemd/system/$SERVICE.service"
    if [ -n "$cur_tz" ]; then
        sed -i "s|^Environment=TZ=.*|Environment=TZ=$cur_tz|" "/etc/systemd/system/$SERVICE.service"
    fi
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

do_tz() {
    need_root
    local unit="/etc/systemd/system/$SERVICE.service"
    if [ ! -f "$unit" ]; then
        echo -e "${RED}Bot service is not installed yet.${PLAIN}"
        return
    fi
    local cur tz
    cur=$(grep -E '^Environment=TZ=' "$unit" | cut -d= -f3)
    echo -e "Current bot timezone: ${CYAN}${cur:-system default}${PLAIN}"
    if [ -n "$1" ]; then
        tz="$1"
    else
        read -r -p "New timezone [Asia/Tehran]: " tz
        tz="${tz:-Asia/Tehran}"
    fi
    if [ ! -f "/usr/share/zoneinfo/$tz" ]; then
        echo -e "${RED}Unknown timezone: $tz${PLAIN} (e.g. Asia/Tehran, Europe/Berlin, UTC)"
        return
    fi
    if grep -qE '^Environment=TZ=' "$unit"; then
        sed -i "s|^Environment=TZ=.*|Environment=TZ=$tz|" "$unit"
    else
        sed -i "/^\[Service\]/a Environment=TZ=$tz" "$unit"
    fi
    systemctl daemon-reload
    systemctl restart "$SERVICE"
    echo -e "${GREEN}✅ Bot timezone set to $tz — all times in the bot now use it.${PLAIN}"
}

# ---- Hetzner account management (multi-account) ----
# Accounts live in .env as HETZNER_API_TOKEN="Name=token,Name2=token2".
# A plain single token still works and shows as "Account 1".
_acct_line()  { grep '^HETZNER_API_TOKEN=' "$DIR/.env" 2>/dev/null | cut -d= -f2- | sed 's/^"//; s/"$//'; }
_acct_save()  { # $1 = new comma-joined value
    grep -v '^HETZNER_API_TOKEN=' "$DIR/.env" > "$DIR/.env.tmp"
    echo "HETZNER_API_TOKEN=$1" >> "$DIR/.env.tmp"
    mv "$DIR/.env.tmp" "$DIR/.env"; chmod 600 "$DIR/.env"
}
_acct_test()  { curl -s -o /dev/null -m 10 -w '%{http_code}' \
                  -H "Authorization: Bearer $1" https://api.hetzner.cloud/v1/servers; }

do_accounts() {
    need_root
    [ -f "$DIR/.env" ] || { echo -e "${RED}No .env yet — install first.${PLAIN}"; return; }
    while true; do
        clear
        echo -e "${CYAN}=== Hetzner Accounts ===${PLAIN}\n"
        local raw; raw="$(_acct_line)"
        IFS=',' read -r -a parts <<< "$raw"
        local n=0
        for p in "${parts[@]}"; do
            p="$(echo "$p" | sed 's/^ *//; s/ *$//')"
            [ -z "$p" ] && continue
            n=$((n+1))
            local name tok
            if [[ "$p" == *=* ]]; then name="${p%%=*}"; tok="${p#*=}"; else name="Account $n"; tok="$p"; fi
            local mask="${tok:0:6}…${tok: -4}"
            local code; code="$(_acct_test "$tok")"
            local badge; [ "$code" = "200" ] && badge="${GREEN}● connected${PLAIN}" || badge="${RED}● FAILED ($code)${PLAIN}"
            echo -e "  $n) ${CYAN}$name${PLAIN}  [$mask]  $badge"
        done
        [ "$n" -eq 0 ] && echo "  (no accounts)"
        echo -e "\n  a) ➕ Add   d) 🗑 Delete   t) 🔄 Re-test   b) ⬅ Back\n"
        read -r -p "  Choice: " c
        case "$c" in
            a)
                read -r -p "  Account name: " nm
                read -r -p "  API token: " tk
                nm="$(echo "$nm" | tr ',=' '  ' | sed 's/^ *//; s/ *$//')"
                tk="$(echo "$tk" | tr -d ' ,')"
                [ -z "$tk" ] && { echo "  Cancelled."; sleep 1; continue; }
                [ -z "$nm" ] && nm="Account $((n+1))"
                echo -n "  Testing… "; local code; code="$(_acct_test "$tk")"
                [ "$code" = "200" ] && echo -e "${GREEN}ok${PLAIN}" || echo -e "${YELLOW}token returned $code, adding anyway${PLAIN}"
                if [ -z "$raw" ]; then _acct_save "$nm=$tk"; else _acct_save "$raw,$nm=$tk"; fi
                systemctl restart "$SERVICE"; echo "  Added + restarted."; sleep 1 ;;
            d)
                read -r -p "  Delete which number? " del
                [[ "$del" =~ ^[0-9]+$ ]] || { echo "  Invalid."; sleep 1; continue; }
                local out="" i=0
                for p in "${parts[@]}"; do
                    p="$(echo "$p" | sed 's/^ *//; s/ *$//')"; [ -z "$p" ] && continue
                    i=$((i+1)); [ "$i" -eq "$del" ] && continue
                    out="${out:+$out,}$p"
                done
                _acct_save "$out"; systemctl restart "$SERVICE"; echo "  Deleted + restarted."; sleep 1 ;;
            t) : ;;  # loop re-tests on redraw
            b|"") return ;;
            *) : ;;
        esac
    done
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
    tz|timezone)      do_tz "$2";   exit 0 ;;
    accounts|acct)    do_accounts;  exit 0 ;;
    "") ;;  # no argument: open the interactive menu
    *)
        echo "Usage: hetzner [install|update|uninstall|start|stop|restart|status|logs|env|tz|accounts]"
        exit 1
        ;;
esac

while true; do
    header
    echo "  1) 📥 Install              6) 🔁 Restart"
    echo "  2) ⬆️  Update               7) 📊 Status"
    echo "  3) 🗑  Uninstall            8) 📜 Logs (live)"
    echo "  4) ▶️  Start                9) ⚙️  Edit .env"
    echo "  5) ⏹  Stop                10) 🕒 Timezone"
    echo "                            11) 🔑 Hetzner Accounts"
    echo "                             0) 🚪 Exit"
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
        10) do_tz ;;
        11) do_accounts ;;
        0) exit 0 ;;
        *) echo -e "${RED}Invalid option.${PLAIN}" ;;
    esac
    echo
    read -r -p "  Press Enter to continue..." _
done
