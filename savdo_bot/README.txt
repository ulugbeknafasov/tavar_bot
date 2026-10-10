SAVDO BOTI — MODULLI LOYIHA

FAYLLAR:
- bot.py: Telegram botning menyulari va handlerlari
- config.py: .env faylidan BOT_TOKEN va OWNER_ID ni o'qiydi
- database.py: SQLite bazasi va ma'lumotlar funksiyalari
- requirements.txt: kerakli Python kutubxonalari
- .env.example: .env uchun namuna
- .gitignore: maxfiy va vaqtinchalik fayllarni chetda saqlaydi

MUHIM:
Agar sizda oldindan .env mavjud bo'lsa, uni saqlang. .env.example faylini .env nomiga nusxalash mumkin, ammo ichidagi namunaviy qiymatlarni o'zingizning haqiqiy BOT_TOKEN va OWNER_ID bilan almashtiring.
Eski products.db faylingizni o'chirmang. Ishga tushirishdan oldin zaxira nusxasi bo'lsin.
Ushbu loyiha bot.py ichida `from config import BOT_TOKEN, OWNER_ID` va `import database as db` dan foydalanadi, shuning uchun uchala Python fayli bir papkada turishi kerak.

WINDOWS POWERSHELL:
1. Loyiha papkasiga kiring.
2. Virtual muhitni faollashtiring (agar mavjud bo'lsa):
   .\.venv\Scripts\Activate.ps1
3. Kutubxonalarni o'rnating:
   python -m pip install -r requirements.txt
4. Botni ishga tushiring:
   python bot.py

.env ICHIDA:
BOT_TOKEN=BotFather tokeni
OWNER_ID=admin Telegram ID raqami

BOTNI KANALGA ULASH:
Botni kerakli Telegram kanaliga administrator qilib qo'shing. Profil bo'limida buyurtma kontaktini sozlang, shunda postlarda buyurtma tugmasi paydo bo'ladi.

ADMIN BUYRUQLARI:
 /admin
 /setcard KARTA_RAQAMI
 /setcardowner Ism Familiya
 /setpaytext To'lov yo'riqnomasi
 /tarif Start | 39000 | 30 | 3 | 10
 /tarifoff ID
 /approve PAYMENT_ID
 /reject PAYMENT_ID sabab
 /reply USER_ID javob matni

ESLATMA:
To'lovlar avtomatik bank orqali tekshirilmaydi. Foydalanuvchi chek yuboradi, admin qo'lda tekshiradi. Mahsulot matnidan nom va narx ajratish qoidalarga asoslanadi, shu sababli har xil formatdagi matnni to'g'ri ajratmasligi mumkin. Kod sintaksisi tekshirildi, ammo real Telegram tokeni va kanallarda to'liq sinovdan o'tkazish kerak.
