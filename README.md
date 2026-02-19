# 🖥️ Hetzner Server Manager Bot

[![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Telegram](https://img.shields.io/badge/Telegram-Bot-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Hetzner](https://img.shields.io/badge/Hetzner-Cloud-D50C2D?style=for-the-badge&logo=hetzner&logoColor=white)](https://hetzner.cloud)
[![License](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)

**Manage your Hetzner Cloud servers directly from Telegram**  
**مدیریت سرورهای Hetzner Cloud مستقیم از تلگرام**

[English](#english) | [فارسی](#فارسی)

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
- 💸 **Cost Report** — Monthly cost & overage breakdown
- 🔐 **Admin Only** — Only you can access the bot

---

### 📋 Requirements

- Ubuntu 22.04 or higher
- Python 3.10+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- A Hetzner Cloud API Token

---

### 🚀 Quick Setup

**Step 1 — Install**

```bash
apt update && apt install -y python3 python3-pip python3-venv git

git clone https://github.com/phoseinq/Hetzner-Server-Usage.git
cd Hetzner-Server-Usage

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**Step 2 — Configure**

```bash
cp .env.example .env
nano .env
```

Fill in your credentials:

```env
TELEGRAM_TOKEN=        # from @BotFather
HETZNER_API_TOKEN=     # from Hetzner Cloud Console → Security → API Tokens
ADMIN_ID=              # your Telegram user ID (get it from @userinfobot)
DEBUG_MODE=false
```

**Step 3 — Run**

```bash
source venv/bin/activate
python3 main.py
```

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

---

### ⚠️ Daily Monitoring

The bot checks traffic every day at **12:00 PM**:

| Usage | Alert |
|---|---|
| 75% | ⚠️ Warning notification |
| 98% | 🚨 Critical alert |

---

### 📁 Project Structure

```
├── main.py             Entry point
├── config.py           Config & env loader
├── handlers.py         Telegram button handlers
├── shell_handler.py    SSH console logic
├── hetzner_api.py      Hetzner Cloud API client
├── server_manager.py   Traffic reset logic
├── monitor.py          Daily traffic monitor
├── overage_tracker.py  Cost history tracker
├── utils.py            Helper functions
└── .env.example        Environment template
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
- 💸 **گزارش هزینه** — خلاصه هزینه ماهانه و اضافه مصرف
- 🔐 **فقط ادمین** — فقط شما به ربات دسترسی دارید

---

### 📋 پیش‌نیازها

- Ubuntu 22.04 یا بالاتر
- Python 3.10+
- توکن ربات تلگرام (از [@BotFather](https://t.me/BotFather))
- توکن API هتزنر

---

### 🚀 راه‌اندازی سریع

**مرحله ۱ — نصب**

```bash
apt update && apt install -y python3 python3-pip python3-venv git

git clone https://github.com/phoseinq/Hetzner-Server-Usage.git
cd Hetzner-Server-Usage

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

**مرحله ۲ — تنظیمات**

```bash
cp .env.example .env
nano .env
```

مقادیر زیر رو پر کن:

```env
TELEGRAM_TOKEN=        # از @BotFather بگیر
HETZNER_API_TOKEN=     # از پنل Hetzner → Security → API Tokens
ADMIN_ID=              # آیدی تلگرامت (از @userinfobot بگیر)
DEBUG_MODE=false
```

**مرحله ۳ — اجرا**

```bash
source venv/bin/activate
python3 main.py
```

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

---

### ⚠️ مانیتور روزانه

ربات هر روز ساعت **۱۲:۰۰** ترافیک رو چک می‌کنه:

| مصرف | هشدار |
|---|---|
| ۷۵٪ | ⚠️ اطلاع‌رسانی warning |
| ۹۸٪ | 🚨 هشدار بحرانی |

---

### 📁 ساختار پروژه

```
├── main.py             نقطه شروع
├── config.py           مدیریت تنظیمات
├── handlers.py         هندلر دکمه‌های تلگرام
├── shell_handler.py    لاجیک کنسول SSH
├── hetzner_api.py      کلاینت API هتزنر
├── server_manager.py   لاجیک ریست ترافیک
├── monitor.py          مانیتور روزانه ترافیک
├── overage_tracker.py  ردیاب تاریخچه هزینه
├── utils.py            توابع کمکی
└── .env.example        نمونه فایل تنظیمات
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

### 📄 لایسنس

MIT — فایل [LICENSE](LICENSE) رو ببین.

---

**Made with ❤️**
