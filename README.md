# Telegram Business Bot — v2 Release Notes

ربات بیزینسی/پرسونال هیبرید برای پاسخ‌دهی اتوماتیک، مدیریت منوهای اینلاین، شورت‌کات‌ها و مانیتورینگ.

## ✅ وضعیت v2
این نسخه برای جریان Inline Menu Engine با رفتار production آماده شده است، شامل:
- ترتیب صحیح ساخت منو (command → preview → button → action → output).
- اجرای اکشن دکمه برای مشتری‌ها (غیرادمین) بدون بلاک اشتباه.
- چیدمان افقی دکمه‌های منو (۲ تایی در هر ردیف).
- تبعیت کامل Inline Menu از وضعیت سراسری ربات (`active`).
- لیست زنده منوها + مشاهده دکمه‌های هر منو از داخل پنل.

---

## معماری
- Runtime: `python-telegram-bot` (async)
- DB: `SQLite` (`bot.db`)
- Entry point: `bot.py`
- Helper modules:
  - `features/inline_menu.py`
  - `features/inline_actions.py`
  - `features/inline_callback.py`
  - `features/log_export.py`

---

## رفتار کلیدی وضعیت سراسری
- اگر `active = OFF`:
  - عملیات‌های **ادمینی اینلاین** که نیاز به اجرای سیستم دارند با `Popup Alert` متوقف می‌شوند.
  - دکمه‌های **Reply Keyboard فیزیکی** (مثل `menu`) سایلنت no-op هستند.
  - اجرای منوهای اینلاین برای کاربران متوقف است.
- اگر `active = ON` و `inline_menu_enabled = ON`:
  - منوها فعال می‌شوند و خروجی طبق pipeline بیزینسی ارسال می‌گردد.

---

## Inline Menu Engine (v2)
### پنل ادمین
- 🧩 Inline Menu Toggle
- 🆕 ساخت منوی جدید
- ➕ افزودن دکمه
- 🗂️ مدیریت منوها
- ✏️ ویرایش منو
- 📡 لیست زنده منوها

### Create Flow
1. Menu command
2. Preview text
3. Button name
4. Action type (فعلاً `just_text`)
5. Output text
6. Confirm (YES/NO)

### Edit Button Output Flow
1. انتخاب دکمه
2. انتخاب Action Type (فعلاً `just_text`)
3. ورود خروجی جدید
4. ذخیره و جایگزینی متن قبلی

### Live List
- لیست منوها از دیتابیس و به‌صورت pagination.
- با کلیک روی هر منو، preview و دکمه‌های همان منو به‌صورت شیشه‌ای نمایش داده می‌شود.

---

## دیتابیس
جداول اصلی:
- `menus`
- `menu_buttons`
- `admin_states`
- `admin_logs`
- `users`
- `shortcuts`
- `feedbacks`
- `watch_settings`
- `keyword_hits`
- `kv`

---

## نصب و اجرا
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
manage
```

---

## دستورات
- `/start`
- `/panel` (ادمین)
- `/broadcast` (ادمین)
- `panel` (ادمین)
- `menu`

---

## چک سریع قبل از دیپلوی
```bash
python -m py_compile bot.py features/inline_menu.py features/inline_actions.py features/inline_callback.py
```
