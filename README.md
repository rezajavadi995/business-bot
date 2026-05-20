# ربات بیزینس تلگرام

این پروژه یک ربات بیزینسی با نصب سریع، دستور `manage`، پنل ادمین، و داده‌ی پایدار است.

## نصب سریع
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
```

## اجرای مدیریت
بعد از نصب، از هر مسیر:
```bash
manage
```

## دستورات ربات
- `/start`
- `/panel` (فقط ادمین)
- `/broadcast` (فقط ادمین)

همچنین ادمین می‌تواند به‌جای `/panel`، متن `panel` بفرستد.

## تست موقت Business Connection
یک هندلر ایزوله برای تست اضافه شده است:
- اگر پیام بیزینسی ورودی دقیقاً `عجیبستان` باشد
- پاسخ می‌دهد: `✅ Business Bot Works`
- لاگ موفق/ناموفق شفاف ثبت می‌شود.

این بخش برای تست موقت است و در آینده به‌راحتی قابل حذف است (تابع `business_test_handler`).

## دیتابیس MySQL (اختیاری)
برای فعال‌سازی ذخیره‌سازی کاربران در MySQL:
- در `.env` بگذارید:
```env
USE_MYSQL=1
MYSQL_HOST=127.0.0.1
MYSQL_USER=root
MYSQL_PASSWORD=...
MYSQL_DB=business_bot
```

اگر `USE_MYSQL=1` نباشد، ذخیره‌سازی فقط در `data.json` انجام می‌شود.

## فایل‌ها
- `.env`: تنظیمات حساس
- `data.json`: وضعیت و متن‌ها و فیچرها
- `logs/bot.log`: لاگ اجرایی
