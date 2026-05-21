# Telegram Business Bot (SQLite, Production)

## نصب
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
```

## مدیریت
```bash
manage
```

## قابلیت‌های جدید این نسخه
- ذخیره کامل state و تنظیمات در SQLite (`bot.db`)
- سیستم مرکزی ایموجی سفارشی با mapping قابل تنظیم از پنل ادمین
- Self Bot toggle در پنل ادمین
- Shortcut Config Flow چندمرحله‌ای با edit message (بدون spam keyboard)
- Anti-spam (soft ban 20 دقیقه)
- Welcome system با تشخیص اولین چت/بیش از 24 ساعت
- گزارش Human-readable با جزئیات کامل کاربران
- لاگ کامل عملیات مهم

## جدول‌های SQLite
- `kv`: تنظیمات و state
- `users`: اطلاعات کامل کاربران
- `shortcuts`: شورت‌کات‌ها
- `emoji_map`: نگاشت ایموجی‌ها

## اطلاعات ذخیره‌شده کاربر
- user_id
- username
- full_name
- phone (در صورت ارسال contact)
- is_channel_joined
- first_seen_at / last_seen_at
- source
- soft_ban_until
- spam_score
- last_message

## منوی ادمین
با `/panel` یا `panel`:
- روشن/خاموش ربات
- ویرایش متن‌ها
- مدیریت فیچرها
- گزارش وضعیت
- Self Bot ON/OFF
- پیکربندی سلف بات (shortcut)
- Welcome ON/OFF
- پیکربندی Welcome
- پیکربندی ایموجی

## تست Business
اگر business message دقیقا `عجیبستان` باشد، پاسخ:
`✅ Business Bot Works`

## نکته فنی migration
ساختار کیبورد و دکمه‌ها مرکزی شده (`create_primary_button`, `create_success_button`, `create_danger_button`, `create_menu_keyboard`, `create_admin_keyboard`) تا migration تدریجی aiogram در آینده بدون شکستن flow فعلی ممکن باشد.
