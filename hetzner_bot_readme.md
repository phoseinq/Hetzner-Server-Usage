# Hetzner Server Manager Telegram Bot

A Telegram bot to manage Hetzner Cloud servers with traffic monitoring and automatic reset capabilities.

## Features

- 📊 Real-time server monitoring
- 🔄 Automated traffic reset by upgrading/downgrading server plans
- ⚠️ Daily traffic alerts at 75% and 98% usage
- 🔴 Power management (on/off)
- 📈 Traffic usage visualization
- 🔐 Admin-only access control

## Requirements

- Ubuntu 22.04 or higher
- Python 3.10+
- Telegram Bot Token
- Hetzner Cloud API Token

## Installation

### 1. Install Python dependencies

```bash
pip3 install -r requirements.txt
```

### 2. Configure environment variables

Create a `.env` file in the project root:

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials:

```
TELEGRAM_TOKEN=your_bot_token_from_botfather
HETZNER_API_TOKEN=your_hetzner_api_token
ADMIN_ID=your_telegram_user_id
DEBUG_MODE=false
```

### 3. Get your Telegram User ID

Send a message to [@userinfobot](https://t.me/userinfobot) on Telegram to get your user ID.

### 4. Get Hetzner API Token

1. Log in to [Hetzner Cloud Console](https://console.hetzner.cloud/)
2. Go to your project
3. Navigate to Security → API Tokens
4. Generate a new token with Read & Write permissions

## Running the Bot

```bash
python3 main.py
```

The bot will start and connect to Telegram using long polling (no webhook required).

## Project Structure

```
.
├── main.py              # Entry point
├── config.py            # Configuration management
├── hetzner_api.py       # Hetzner Cloud API client
├── handlers.py          # Telegram message handlers
├── server_manager.py    # Traffic reset logic
├── monitor.py           # Daily traffic monitoring
├── utils.py             # Helper functions
├── requirements.txt     # Python dependencies
├── .env.example         # Environment template
└── server_data.csv      # Traffic alert tracking (auto-generated)
```

## Usage

1. Start the bot: `/start`
2. Click "Server Management Panel"
3. Select a server to view details
4. Use "Reset Traffic" to trigger the upgrade/downgrade cycle

## Traffic Reset Process

When you click "Reset Traffic":

1. ⚙️ Server is powered off
2. 💾 Current plan is saved
3. 🔼 Server is upgraded to next available plan
4. ⏳ Wait for upgrade completion
5. 🟢 Server is powered on
6. 🔽 Server is downgraded back to original plan
7. ✅ Process complete - traffic counter reset

## Daily Monitoring

The bot automatically checks traffic daily at 12:00 PM:

- **75% usage**: Warning notification
- **98% usage**: Critical alert

## Security Notes

- Only the configured ADMIN_ID can use the bot
- API tokens are stored in `.env` (never commit this file)
- The bot uses HTTPS for all Hetzner API calls
- Rate limiting protection is built-in

## Troubleshooting

### Bot doesn't respond
- Check if `ADMIN_ID` matches your Telegram user ID
- Verify `TELEGRAM_TOKEN` is correct

### API errors
- Ensure `HETZNER_API_TOKEN` has Read & Write permissions
- Check if you have available server types for upgrade

### Upgrade fails
- Verify there are higher-tier server types available
- Check Hetzner account has sufficient quota

## Logs

Enable debug mode for detailed logs:

```bash
DEBUG_MODE=true
```

Logs will show API calls, traffic checks, and process status.

## License

Private use only.