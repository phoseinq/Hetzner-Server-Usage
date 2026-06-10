<div align="center">

# 🖥️ Hetzner Server Manager Bot

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Hetzner](https://img.shields.io/badge/Hetzner-Cloud-D50C2D?style=for-the-badge&logo=hetzner&logoColor=white)](https://hetzner.cloud)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)

**Manage your Hetzner Cloud servers directly from Telegram**

[English](#english) | [فارسی](#فارسی)

</div>

---

## English

### 📖 Description

A Telegram bot that lets you monitor and manage all your Hetzner Cloud servers from a single chat. Track traffic, control power, run SSH commands, reset passwords — all without opening a browser.

**Key Features:**

- 📊 **Traffic Monitoring** — Real-time usage per server
- ♻️ **Reset Traffic** — Auto upgrade/downgrade cycle to reset the counter
- ⚠️ **Daily Alerts** — Notifications at 75% and 98% usage
- 🔴 **Power Control** — Turn servers on/off instantly
- 💻 **SSH Console** — Run commands directly from Telegram chat
- 🔑 **Reset Password** — Generate a new root password via Hetzner API
- 📸 **Snapshots** — Take, list and delete server snapshots from the bot
- 🌐 **Floating & Primary IPs** — Create and delete IPs in bulk with multi-select
- 💸 **Cost Report** — Per-server costs, snapshots, floating/primary IPs, persisted overage history & month-end projection
- 🔐 **Admin Only** — Only you can access the bot

---

### 📋 Requirements

- Ubuntu 22.04 or higher
- Python 3.10+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- A Hetzner Cloud API Token

---

### 🚀 Quick Setup

**Step 1 — Install (as root)**

```bash
apt update && apt install -y git
git clone https://github.com/phoseinq/Hetzner-Server-Usage.git /opt/Hetzner-Server-Usage
cd /opt/Hetzner-Server-Usage
bash install.sh
```

`install.sh` installs system packages, creates the virtualenv, installs the
requirements, and registers the bot as a **systemd service** so it survives
SSH disconnects and reboots.

**Step 2 — Configure**

```bash
nano /opt/Hetzner-Server-Usage/.env
```

Fill in your credentials:

```env
TELEGRAM_TOKEN=        # from @BotFather
HETZNER_API_TOKEN=     # from Hetzner Cloud Console → Security → API Tokens
ADMIN_ID=              # your Telegram user ID (get it from @userinfobot)
DEBUG_MODE=false
```

> The bot verifies `HETZNER_API_TOKEN` against the Hetzner API at startup
> and exits with a clear error if the token is invalid.

**Step 3 — Run**

```bash
systemctl start hetzner-bot
journalctl -u hetzner-bot -f      # follow the logs
```

<details>
<summary>Manual run without systemd (not recommended — dies when SSH closes)</summary>

```bash
cd /opt/Hetzner-Server-Usage
source venv/bin/activate
python3 main.py
```

The bot **must** be started from the repo directory: its data files
(`server_data.csv`, `overage_history.json`, `monitor_state.json`) use
relative paths.

</details>

---

### 💻 SSH Console Usage

| Step | Action |
|---|---|
| 1 | Open any server from the panel |
| 2 | Tap **💻 SSH Console** |
| 3 | Enter port, username, and password |
| 4 | Send any command — output appears in chat |
| 5 | Tap **🔌 Disconnect** when done |

> Commands like `apt`, `pip`, `systemctl` run in clean mode — no noisy progress bars.  
> Your session stays alive between commands — `cd` works as expected.  
> Session auto-closes after **10 minutes** of inactivity.

---

### ♻️ Traffic Reset Process

When you tap **Reset Traffic**, the bot:

| Step | Action |
|---|---|
| 1 | Powers off the server |
| 2 | Upgrades to the next plan |
| 3 | Powers on the server |
| 4 | Downgrades back to the original plan |
| 5 | ✅ Traffic counter is reset |

> Before the reset starts, any overage cost from the current cycle is saved
> to the cost history (`overage_history.json`) — resetting no longer wipes
> it from the **Cost Report**.

---

### ⚠️ Traffic Monitoring

The bot checks traffic **every hour** automatically:

| Usage | Alert |
|---|---|
| 75% | ⚠️ One-time warning — resets when usage drops below 75% |
| 98% | 🚨 Critical alert — once per day |
| 100%+ | 🔥 Overage alert — once per day |

---

### 📁 Project Structure

```
├── main.py              Entry point
├── config.py            Config & env loader
├── handlers.py          Telegram button handlers
├── shell_handler.py     SSH console logic
├── hetzner_api.py       Hetzner Cloud API client
├── server_manager.py    Traffic reset logic
├── monitor.py           Hourly traffic monitor
├── overage_tracker.py   Cost history tracker
├── utils.py             Helper functions
├── install.sh           One-command installer (systemd)
├── hetzner-bot.service  systemd unit template
└── .env.example         Environment template
```

---

### 🐛 Troubleshooting

**Bot doesn't respond?**
- Check that `ADMIN_ID` matches your Telegram user ID
- Verify `TELEGRAM_TOKEN` is correct

**SSH Console won't connect?**
- Make sure the server status is **RUNNING**
- Double-check port, username, and password

**Reset Traffic fails?**
- Make sure your Hetzner account has a higher-tier plan available
- Verify your API token has **Read & Write** permissions

---

### 📄 License

MIT — see [LICENSE](LICENSE) for details.

---

---

## فارسی

### 📖 معرفی

یه ربات تلگرامی که بهت اجازه میده همه سرورهای Hetzner Cloud رو مستقیم از یه چت مدیریت کنی. ترافیک رو مانیتور کن، پاور رو کنترل کن، دستور SSH بزن، پسورد ریست کن — همه اینا بدون باز کردن مرورگر.

**امکانات:**

- 📊 **مانیتور ترافیک** — مصرف لحظه‌ای هر سرور
- ♻️ **ریست ترافیک** — آپگرید/داونگرید خودکار برای ریست کانتر
- ⚠️ **هشدار روزانه** — اطلاع‌رسانی در ۷۵٪ و ۹۸٪ مصرف
- 🔴 **کنترل پاور** — روشن و خاموش کردن سرور
- 💻 **کنسول SSH** — اجرای دستور مستقیم از چت تلگرام
- 🔑 **ریست پسورد** — تولید پسورد root جدید از طریق API هتزنر
- 📸 **اسنپ‌شات** — گرفتن، مشاهده و حذف اسنپ‌شات سرورها از داخل ربات
- 🌐 **IP های Floating و Primary** — ساخت و حذف گروهی IP ها با انتخاب چندتایی
- 💸 **گزارش هزینه** — هزینه هر سرور، اسنپ‌شات‌ها، IP های Floating/Primary، تاریخچه ماندگار اضافه‌مصرف و پیش‌بینی آخر ماه
- 🔐 **فقط ادمین** — فقط شما به ربات دسترسی دارید

---

### 📋 پیش‌نیازها

- Ubuntu 22.04 یا بالاتر
- Python 3.10+
- توکن ربات تلگرام (از [@BotFather](https://t.me/BotFather))
- توکن API هتزنر

---

### 🚀 راه‌اندازی سریع

**مرحله ۱ — نصب (با root)**

```bash
apt update && apt install -y git
git clone https://github.com/phoseinq/Hetzner-Server-Usage.git /opt/Hetzner-Server-Usage
cd /opt/Hetzner-Server-Usage
bash install.sh
```

اسکریپت `install.sh` پکیج‌های سیستمی رو نصب می‌کنه، virtualenv می‌سازه،
نیازمندی‌ها رو نصب می‌کنه و ربات رو به‌صورت **سرویس systemd** ثبت می‌کنه —
یعنی با قطع شدن SSH یا ریبوت سرور، ربات نمی‌میره.

**مرحله ۲ — تنظیمات**

```bash
nano /opt/Hetzner-Server-Usage/.env
```

مقادیر زیر رو پر کن:

```env
TELEGRAM_TOKEN=        # از @BotFather بگیر
HETZNER_API_TOKEN=     # از پنل Hetzner → Security → API Tokens
ADMIN_ID=              # آیدی تلگرامت (از @userinfobot بگیر)
DEBUG_MODE=false
```

> ربات موقع استارت `HETZNER_API_TOKEN` رو با API هتزنر چک می‌کنه و اگه
> توکن نامعتبر باشه با پیام واضح خارج میشه.

**مرحله ۳ — اجرا**

```bash
systemctl start hetzner-bot
journalctl -u hetzner-bot -f      # دیدن لاگ‌ها
```

<details>
<summary>اجرای دستی بدون systemd (پیشنهاد نمیشه — با بستن SSH می‌میره)</summary>

```bash
cd /opt/Hetzner-Server-Usage
source venv/bin/activate
python3 main.py
```

ربات **حتماً** باید از پوشه ریپو اجرا بشه: فایل‌های داده‌اش
(`server_data.csv`، `overage_history.json`، `monitor_state.json`)
مسیر نسبی دارن.

</details>

---

### 💻 نحوه استفاده از کنسول SSH

| مرحله | کار |
|---|---|
| ۱ | یک سرور رو از پنل باز کن |
| ۲ | روی **💻 SSH Console** بزن |
| ۳ | پورت، یوزرنیم و پسورد رو وارد کن |
| ۴ | هر دستوری بفرست — خروجی توی چت میاد |
| ۵ | وقتی کارت تموم شد **🔌 Disconnect** بزن |

> دستوراتی مثل `apt`، `pip` و `systemctl` بدون خروجی اضافه اجرا میشن.  
> Session بین دستورات زنده میمونه — `cd` درست کار می‌کنه.  
> بعد از **۱۰ دقیقه** بی‌تحرکی session خودکار قطع میشه.

---

### ♻️ فرآیند ریست ترافیک

وقتی **Reset Traffic** میزنی، ربات:

| مرحله | کار |
|---|---|
| ۱ | سرور رو خاموش می‌کنه |
| ۲ | به پلن بالاتر آپگرید می‌کنه |
| ۳ | سرور رو روشن می‌کنه |
| ۴ | به پلن اصلی برمی‌گرده |
| ۵ | ✅ کانتر ترافیک ریست میشه |

> قبل از شروع ریست، هزینه اضافه‌مصرف این دوره توی تاریخچه هزینه
> (`overage_history.json`) ذخیره میشه — دیگه با ریست، عدد **Cost Report**
> از بین نمیره.

---

### ⚠️ مانیتور ترافیک

ربات **هر ساعت** ترافیک رو چک می‌کنه:

| مصرف | هشدار |
|---|---|
| ۷۵٪ | ⚠️ یه بار نوتیف — وقتی برگشت زیر ۷۵٪ ریست میشه |
| ۹۸٪ | 🚨 هشدار بحرانی — یه بار در روز |
| ۱۰۰٪+ | 🔥 هشدار اضافه مصرف — یه بار در روز |

---

### 📁 ساختار پروژه

```
├── main.py              نقطه شروع
├── config.py            مدیریت تنظیمات
├── handlers.py          هندلر دکمه‌های تلگرام
├── shell_handler.py     لاجیک کنسول SSH
├── hetzner_api.py       کلاینت API هتزنر
├── server_manager.py    لاجیک ریست ترافیک
├── monitor.py           مانیتور ساعتی ترافیک
├── overage_tracker.py   ردیاب تاریخچه هزینه
├── utils.py             توابع کمکی
├── install.sh           نصب یک‌مرحله‌ای (systemd)
├── hetzner-bot.service  قالب سرویس systemd
└── .env.example         نمونه فایل تنظیمات
```

---

### 🐛 رفع مشکلات

**ربات جواب نمیده؟**
- چک کن `ADMIN_ID` با آیدی تلگرامت مطابقت داشته باشه
- `TELEGRAM_TOKEN` رو تأیید کن

**کنسول SSH وصل نمیشه؟**
- مطمئن شو وضعیت سرور **RUNNING** باشه
- پورت، یوزرنیم و پسورد رو دوباره چک کن

**ریست ترافیک خطا میده؟**
- مطمئن شو اکانت هتزنرت پلن بالاتری در دسترس داره
- بررسی کن توکن API دسترسی **Read & Write** داشته باشه

---

**Made with ❤️**
