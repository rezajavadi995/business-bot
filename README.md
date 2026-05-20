# Telegram Business Bot

این نسخه با حفظ flow فعلی، برای migration تدریجی به معماری مدرن (aiogram-first در آینده) آماده شده است؛ بدون rewrite سنگین و بدون شکستن handler/callback های فعلی.

## نصب
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
```

سپس:
```bash
manage
```

## معماری فعلی (Hybrid Migration Ready)
- Runtime فعلی روی `python-telegram-bot` باقی مانده (پایدار و production-safe).
- لایه abstraction برای keyboard/buttons اضافه شده تا مهاجرت آینده ساده شود:
  - `create_primary_button`
  - `create_success_button`
  - `create_danger_button`
  - `create_menu_keyboard`
  - `create_admin_keyboard`
- Handlerها و flow فعلی حذف یا شکسته نشده‌اند.

## پایگاه داده: SQLite
- تمام داده‌های مهم در `bot.db` نگه‌داری می‌شوند.
- جدول `kv`: تنظیمات ربات (features/texts/state)
- جدول `users`: اطلاعات کاربران

فیلدهای کاربر:
- `user_id`
- `username` (شناسه @)
- `full_name`
- `phone` (اگر ارسال شود)
- `is_channel_joined` (yes/no)
- `last_seen_at`
- `source`

## رفتار Business Test (ایزوله و قابل حذف)
هندلر تست ایزوله فعال است:
- اگر business message دقیقاً `عجیبستان` باشد
- پاسخ می‌دهد: `✅ Business Bot Works`
- لاگ موفق/خطا واضح ثبت می‌شود.

## دستورات
- `/start`
- `/panel` (فقط ادمین)
- `/broadcast` (فقط ادمین)
- `panel` بدون اسلش (فقط ادمین)
- `menu`

## Dependency Analysis
- کتابخانه اصلی: `python-telegram-bot==21.10`
- سازگار با async handlers فعلی پروژه.
- پشتیبانی style واقعی دکمه‌ها (`style=primary/success/danger`) در این لایه تضمین‌شده نیست؛ بنابراین fallback امن با emoji semantic استفاده شده است (بدون crash/serialization issue).
- برای migration آینده به aiogram، abstraction layer دکمه‌ها و keyboardها coupling را کم کرده است.

## Migration Readiness Report
- ✅ Keyboard system مرکزی شد.
- ✅ Button factory اضافه شد.
- ✅ Callback routing حفظ شد.
- ✅ Business test flow ایزوله شد.
- ✅ Data persistence از JSON پراکنده به SQLite مرکزی منتقل شد.
- ✅ Backward compatibility حفظ شد (commands/handlers اصلی پابرجا).

### Compatibility Risks
- Business message routing وابسته به تنظیمات Telegram Business account و permissionها است.
- در برخی clientها/سناریوها ممکن است business update نرسد؛ لاگ برای تشخیص اضافه شده است.

## فایل‌ها
- `bot.py` منطق ربات + adapters + handlers
- `bot.db` دیتابیس SQLite
- `logs/bot.log` لاگ
- `.env` تنظیمات حساس
