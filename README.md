# 🖥️ Hetzner Server Manager Bot

> A Telegram bot to monitor and manage your Hetzner Cloud servers — traffic, power, SSH console, and more.

---

## ✨ Features

| Feature | Description |
|---|---|
| 📊 Traffic Monitoring | Real-time usage per server |
| ♻️ Reset Traffic | Auto upgrade/downgrade to reset counter |
| ⚠️ Daily Alerts | Warnings at 75% and 98% usage |
| 🔴 Power Control | Turn servers on/off |
| 💻 SSH Console | Run commands directly from Telegram |
| 🔑 Reset Password | Generate new root password via API |
| 💸 Cost Report | Monthly cost & overage breakdown |
| 🔐 Admin Only | Only you can use the bot |

---

## 🚀 Quick Setup

### 1 — Install

```bash
apt update && apt install -y python3 python3-pip python3-venv git

git clone https://github.com/phoseinq/Hetzner-Server-Usage.git
cd Hetzner-Server-Usage

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2 — Configure

```bash
cp .env.example .env
nano .env
```

Fill in your credentials:

```env
TELEGRAM_TOKEN=        # from @BotFather
HETZNER_API_TOKEN=     # from Hetzner Cloud Console
ADMIN_ID=              # your Telegram user ID (get it from @userinfobot)
DEBUG_MODE=false
```

### 3 — Run

```bash
source venv/bin/activate
python3 main.py
```

---

## 💻 SSH Console Usage

1. Open any server from the panel
2. Tap **💻 SSH Console**
3. Enter port, username, and password
4. Send any command — output appears in chat
5. Tap **🔌 Disconnect** when done

> Commands like `apt`, `pip`, `systemctl` run in clean mode — no noisy progress bars.

---

## 📁 Project Structure

```
├── main.py             Entry point
├── config.py           Config & env loader
├── handlers.py         Telegram button handlers
├── shell_handler.py    SSH console logic
├── hetzner_api.py      Hetzner Cloud API client
├── server_manager.py   Traffic reset logic
├── monitor.py          Daily traffic monitor
├── utils.py            Helper functions
└── .env.example        Environment template
```

---

---

# 🖥️ ربات مدیریت سرور هتزنر

> ربات تلگرامی برای مانیتور و مدیریت سرورهای Hetzner Cloud — ترافیک، پاور، کنسول SSH و بیشتر.

---

## ✨ امکانات

| امکان | توضیح |
|---|---|
| 📊 مانیتور ترافیک | نمایش مصرف لحظه‌ای هر سرور |
| ♻️ ریست ترافیک | آپگرید/داونگرید خودکار برای ریست کانتر |
| ⚠️ هشدار روزانه | اطلاع‌رسانی در ۷۵٪ و ۹۸٪ مصرف |
| 🔴 کنترل پاور | روشن و خاموش کردن سرور |
| 💻 کنسول SSH | اجرای دستور مستقیم از تلگرام |
| 🔑 ریست پسورد | تولید پسورد root جدید از طریق API |
| 💸 گزارش هزینه | خلاصه هزینه ماهانه و اضافه مصرف |
| 🔐 فقط ادمین | فقط شما به ربات دسترسی دارید |

---

## 🚀 راه‌اندازی سریع

### مرحله ۱ — نصب

```bash
apt update && apt install -y python3 python3-pip python3-venv git

git clone https://github.com/phoseinq/Hetzner-Server-Usage.git
cd Hetzner-Server-Usage

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### مرحله ۲ — تنظیمات

```bash
cp .env.example .env
nano .env
```

مقادیر زیر رو پر کن:

```env
TELEGRAM_TOKEN=        # از @BotFather بگیر
HETZNER_API_TOKEN=     # از پنل Hetzner Cloud بگیر
ADMIN_ID=              # آیدی تلگرامت (از @userinfobot بگیر)
DEBUG_MODE=false
```

### مرحله ۳ — اجرا

```bash
source venv/bin/activate
python3 main.py
```

---

## 💻 نحوه استفاده از کنسول SSH

1. یک سرور رو از پنل باز کن
2. روی **💻 SSH Console** بزن
3. پورت، یوزرنیم و پسورد رو وارد کن
4. هر دستوری بفرست — خروجی توی چت نمایش داده میشه
5. وقتی کارت تموم شد **🔌 Disconnect** بزن

> دستوراتی مثل `apt`، `pip` و `systemctl` بدون خروجی اضافه اجرا میشن.

---

## 📁 ساختار پروژه

```
├── main.py             نقطه شروع
├── config.py           مدیریت تنظیمات
├── handlers.py         هندلر دکمه‌های تلگرام
├── shell_handler.py    لاجیک کنسول SSH
├── hetzner_api.py      کلاینت API هتزنر
├── server_manager.py   لاجیک ریست ترافیک
├── monitor.py          مانیتور روزانه ترافیک
├── utils.py            توابع کمکی
└── .env.example        نمونه فایل تنظیمات
```

---

## 📄 License

MIT
