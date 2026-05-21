# Telegram Business Bot (Production, SQLite)

ربات بیزینسی تلگرام با ذخیره‌سازی پایدار در SQLite، پنل ادمین، مدیریت شورت‌کات، سیستم بازخورد، Welcome هوشمند، و installer آماده‌ی سرور.

## نصب سریع
```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/rezajavadi995/business-bot/main/install.sh)"
```

پس از نصب:
```bash
manage
```

---

## وضعیت نهایی معماری
- Runtime فعلی: `python-telegram-bot` (async)
- دیتابیس: `bot.db` (SQLite)
- مسیر migration-safe:
  - Button Factory مرکزی
  - Keyboard Builder مرکزی
  - State Flow قابل توسعه
  - بدون شکستن handler/callback فعلی

---

## جداول SQLite
- `kv`: تنظیمات و state
- `users`: اطلاعات کاربران
- `shortcuts`: شورت‌کات‌ها
- `feedbacks`: پیام‌های بازخورد

---

## جریان اصلی کاربر
1. کاربر `/start` می‌زند -> پیام خوشامد + Reply Keyboard
2. کاربر `menu` می‌زند -> Inline menu نمایش داده می‌شود
3. کاربر می‌تواند خدمات/ساعات/آدرس/FAQ/تماس/بازخورد بگیرد
4. بازخورد کاربر ذخیره می‌شود و نوتیف برای ادمین ارسال می‌گردد

---

## پنل ادمین
ورود به پنل:
- `/panel`
- یا `panel`

امکانات پنل:
- روشن/خاموش ربات
- ویرایش متن‌ها
- مدیریت فیچرها
- گزارش وضعیت Human-readable
- راهنمای Broadcast
- Self Bot ON/OFF
- مدیریت شورت‌کات‌ها (مشاهده + افزودن/ویرایش)
- Welcome ON/OFF + تنظیم متن Welcome
- نمایش همه بازخوردها
- دکمه بازگشت در flowهای اجرایی

---

## شورت‌کات‌ها
در «مدیریت سلف بات»:
- مشاهده شورت‌کات‌های فعلی
- افزودن/ویرایش شورت‌کات

رفتار:
- اگر متن پیام شامل شورت‌کات باشد، پاسخ شورت‌کات ارسال می‌شود
- در حالت Self Bot برای ادمین: پیام ادمین حذف می‌شود و پاسخ شورت‌کات ارسال می‌شود

---

## سیستم بازخورد
- دکمه «ارسال بازخورد» کاربر را وارد حالت feedback می‌کند
- متن prompt و success از پنل قابل شخصی‌سازی است
- بعد از ثبت بازخورد:
  - پیام موفقیت برای کاربر
  - ذخیره در SQLite
  - نوتیف برای ادمین با دکمه مشاهده متن
- پنل ادمین: «پیام‌های بازخورد» برای مشاهده کل تاریخچه

---

## Welcome در Business PV
Welcome برای مسیر business message در نظر گرفته شده:
- پیام اول کاربر در PV بیزینسی
- یا پس از 24 ساعت عدم تعامل

---

## Anti-Spam
- تکرار غیرعادی کلمات یا shortcut flood تشخیص داده می‌شود
- soft ban مدت‌دار (20 دقیقه)
- لاگ کامل anti-spam ثبت می‌شود

---

## لاگ‌ها
- فایل لاگ: `logs/bot.log`
- لاگ‌های مهم:
  - anti-spam
  - shortcut execution
  - welcome trigger
  - shortcut مسیر بیزینسی
  - admin actions

---

## دستورات
- `/start`
- `/panel` (ادمین)
- `/broadcast` (ادمین)
- `panel` (ادمین)
- `menu`

---

## اجرای سرویس پایدار
از `manage` گزینه مربوط به systemd را انتخاب کنید تا بعد از reboot هم فعال بماند.
