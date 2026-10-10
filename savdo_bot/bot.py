import logging
import re
import asyncio
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ConversationHandler, ContextTypes, filters,
)

from config import BOT_TOKEN, OWNER_ID
import database as db

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

P_NAME, P_DETAILS, P_PHOTO, CH_CHANNEL, SET_CONTACT, SET_CARD, SET_OWNER, SET_INSTRUCTIONS, SUPPORT_TEXT, ADMIN_REPLY = range(10)


def main_menu(is_owner=False):
    rows = [
        [InlineKeyboardButton("🛍 Mahsulot joylash", callback_data="product_add")],
        [InlineKeyboardButton("📣 Kanallarim", callback_data="channels"),
         InlineKeyboardButton("💎 Tariflar", callback_data="plans")],
        [InlineKeyboardButton("👤 Profilim", callback_data="profile"),
         InlineKeyboardButton("💬 Qo‘llab-quvvatlash", callback_data="support")],
    ]
    if is_owner:
        rows.append([InlineKeyboardButton("⚙️ Boshqaruv paneli", callback_data="owner")])
    return InlineKeyboardMarkup(rows)


def owner_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Statistika", callback_data="stats"),
         InlineKeyboardButton("💳 To‘lovlar", callback_data="pending_payments")],
        [InlineKeyboardButton("💎 Tariflar", callback_data="manage_plans"),
         InlineKeyboardButton("💰 Karta sozlash", callback_data="payment_settings")],
        [InlineKeyboardButton("💬 Yordam so‘rovlari", callback_data="support_queue")],
        [InlineKeyboardButton("⬅️ Asosiy menyu", callback_data="home")],
    ])


def cancel_kb():
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_flow")]])


def money(value):
    return f"{int(value):,}".replace(",", " ") + " so‘m"


def parse_product_text(text):
    """Mahsulot matnini tartibsiz yoki qatorma-qator yozilgan bo‘lsa ham ajratadi."""
    raw_lines = [re.sub(r"^\s*[•▪●➤👉📌✨🌸🛍️💰📏🧵👗🧥👚👖💎🔥🎀]+\s*", "", line).strip() for line in text.splitlines()]
    lines = [re.sub(r"\s+", " ", line).strip(" \t•-–|:") for line in raw_lines]
    lines = [line for line in lines if line]
    if not lines:
        return "Mahsulot", "Tavsif kiritilmagan", None

    # Narxni belgi bilan yoki valyuta yozuvi bilan topamiz; razmer/model raqamlarini narx deb olmaymiz.
    price = None
    price_patterns = [
        r"(?i)\b(?:narx(?:i)?|price|summa|qiymati)\s*[:：=\-]?\s*([\d][\d\s.,]*)\s*(?:so['’`]?m|uzs|сўм|сум)?",
        r"(?i)([\d][\d\s.,]*)\s*(?:so['’`]?m|uzs|сўм|сум)\b",
    ]
    price_line_indexes = set()
    for i, line in enumerate(lines):
        for pattern in price_patterns:
            match = re.search(pattern, line)
            if match:
                digits = re.sub(r"\D", "", match.group(1))
                if digits:
                    try:
                        candidate = int(digits)
                        # Oddiy kiyim/mahsulot narxlari uchun mantiqiy diapazon; kichik qiymatlar ham mumkin.
                        if candidate > 0:
                            price = candidate
                            price_line_indexes.add(i)
                            break
                    except ValueError:
                        pass
        if price is not None:
            break

    # Agar narx alohida satrda emas, jumla orasida bo‘lsa ham, aynan narx qismini olib tashlaymiz.
    cleaned_lines = []
    for line in lines:
        cleaned = re.sub(
            r"(?i)\b(?:narx(?:i)?|price|summa|qiymati)\s*[:：=\-]?\s*[\d][\d\s.,]*\s*(?:so['’`]?m|uzs|сўм|сум)?",
            " ", line,
        )
        cleaned = re.sub(r"(?i)\b[\d][\d\s.,]*\s*(?:so['’`]?m|uzs|сўм|сум)\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;|–-:.")
        if cleaned:
            cleaned_lines.append(cleaned)

    # Birinchi mos satr nom bo‘ladi; tavsif/atribut satrlari nomga qo‘shilmaydi.
    metadata = re.compile(r"(?i)\b(narx|price|summa|qiymati|razmer|o‘lcham|olcham|size|rang|rangi|mato|model|tavsif|yetkazib|bezak|standart|kofta matosi)\s*[:：=\-]?")
    name = ""
    for line in cleaned_lines:
        if line in ("Mahsulot", "Yangi kolleksiya", "New Collection", "Navinka", "Navinka"):
            continue
        # Agar satr mahsulot nomi va xususiyatlarni birga olsa, birinchi ajratuvchigacha nom sifatida olamiz.
        if metadata.search(line):
            prefix = re.split(r"(?i)\b(?:mahsulot|narx(?:i)?|razmer|o‘lcham|olcham|rang|mato|model|tavsif|bezak)\s*[:：]", line, maxsplit=1)[0].strip(" ,;|–-:")
            if prefix and len(prefix) <= 90:
                name = prefix
                break
            continue
        name = line[:90]
        break
    if not name:
        # Oxirgi fallback: birinchi satrning qisqa nom qismi.
        name = re.split(r"(?i)\b(?:mahsulot|narx(?:i)?|razmer|o‘lcham|olcham|rang|mato|model|tavsif)\s*[:：]", lines[0], maxsplit=1)[0].strip(" ,;|–-:")[:90] or "Mahsulot"

    # Takroriy sarlavha va narx satrlarini tavsifga kiritmay, qolgan xususiyatlarni satrma-satr saqlaymiz.
    desc_items = []
    for i, line in enumerate(cleaned_lines):
        if line == name or i in price_line_indexes:
            continue
        if re.fullmatch(r"(?i)(ayollar liboslari\s*\|\s*kiyimlari|new collection|navinka|yangi kolleksiya)", line):
            continue
        desc_items.append(line)
    description = "\n".join(desc_items).strip()
    if not description:
        description = "Tavsif kiritilmagan"
    return name[:90], description[:900], price


def build_caption(product, order_contact):
    price = money(product["price"]) if product.get("price") else "Narxni sotuvchidan aniqlang"
    caption = (
        f"🛍 {product['name']}\n\n"
        f"{product['description']}\n\n"
        f"💰 Narxi: {price}\n\n"
        "📩 Buyurtma berish uchun pastdagi tugmani bosing."
    )
    return caption[:1024]


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.add_user(user)
    await update.effective_message.reply_text(
        f"Assalomu alaykum, {user.first_name or 'do‘stimiz'}! 👋\n\n"
        "🛍 Savdo postlarini oson boshqarish platformasiga xush kelibsiz.\n"
        "Quyidagi menyudan kerakli bo‘limni tanlang.",
        reply_markup=main_menu(user.id == OWNER_ID),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Amal bekor qilindi.", reply_markup=main_menu(update.effective_user.id == OWNER_ID)
    )
    return ConversationHandler.END


async def cancel_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_reply_markup(reply_markup=None)
    await query.message.reply_text(
        "Amal bekor qilindi.", reply_markup=main_menu(query.from_user.id == OWNER_ID)
    )


async def owner_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("⛔ Bu bo‘lim faqat bot egasi uchun.")
        return
    await update.effective_message.reply_text("⚙️ BOT EGASI PANELI", reply_markup=owner_keyboard())


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data or ""
    is_owner = user_id == OWNER_ID

    owner_actions = {
        "owner", "stats", "pending_payments", "manage_plans",
        "payment_settings", "support_queue",
    }
    if data in owner_actions and not is_owner:
        await query.answer("Ruxsat yo‘q.", show_alert=True)
        return
    await query.answer()

    if data == "product_add":
        # This callback is also the ConversationHandler entry point.
        # Returning P_NAME is essential; without it the next text message is ignored.
        await query.message.reply_text(
            "🛍 MAHSULOT JOYLASH\n\n"
            "Mahsulot haqida ma'lumotni bitta xabarda yuboring.\n"
            "Masalan:\n"
            "Krossovka Nike\n"
            "Narxi: 250000 so'm\n"
            "Yangi, sifatli. Razmerlar: 40–44\n\n"
            "Keyin mahsulot rasmini alohida yuborasiz.",
            reply_markup=cancel_kb(),
        )
        return P_NAME

    elif data == "home":
        await query.edit_message_text("🏠 Asosiy menyu", reply_markup=main_menu(is_owner))

    elif data == "owner":
        await query.edit_message_text("⚙️ BOT EGASI PANELI", reply_markup=owner_keyboard())

    elif data == "stats":
        s = db.stats()
        await query.edit_message_text(
            "📊 BOT STATISTIKASI\n\n"
            f"👥 Foydalanuvchilar: {s['users']}\n"
            f"📦 Mahsulotlar: {s['products']}\n"
            f"📣 Ulangan kanallar: {s['channels']}\n"
            f"📨 Muvaffaqiyatli postlar: {s['posts']}\n"
            f"💳 Faol obunalar: {s['active_subscriptions']}\n"
            f"⏳ Tekshirilayotgan to‘lovlar: {s['pending_payments']}\n"
            f"💰 Tasdiqlangan tushumlar: {money(s['revenue'])}\n"
            f"💬 Ochiq murojaatlar: {s['open_support']}",
            reply_markup=owner_keyboard(),
        )

    elif data == "manage_plans":
        plans = db.list_plans(active_only=False)
        lines = ["💎 TARIFLARNI BOSHQARISH", ""]
        for p in plans:
            status = "🟢 faol" if p["is_active"] else "⚪ o‘chirilgan"
            lines.append(
                f"#{p['id']} {p['name']} — {money(p['price'])}, {p['duration_days']} kun\n"
                f"Kanallar: {p['channel_limit']} | post/kun: {p['daily_post_limit']} | {status}"
            )
        lines.append("\nTarif yaratish/yangilash buyrug‘i:")
        lines.append("/tarif Start | 39000 | 30 | 3 | 10")
        lines.append("Tarifni o‘chirish: /tarifoff ID")
        await query.edit_message_text("\n".join(lines), reply_markup=owner_keyboard())

    elif data == "payment_settings":
        card = db.get_setting("payment_card")
        owner = db.get_setting("payment_owner")
        instructions = db.get_setting("payment_instructions")
        await query.edit_message_text(
            "💰 TO‘LOV SOZLAMALARI\n\n"
            f"💳 Karta: {card or 'Kiritilmagan'}\n"
            f"👤 Karta egasi: {owner or 'Kiritilmagan'}\n"
            f"📝 Izoh: {instructions or 'Kiritilmagan'}\n\n"
            "Sozlash buyruqlari:\n"
            "/setcard 8600 1234 5678 9012\n"
            "/setcardowner Ism Familiya\n"
            "/setpaytext To‘lovdan so‘ng chekni yuboring",
            reply_markup=owner_keyboard(),
        )

    elif data == "pending_payments":
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payments WHERE status='pending' ORDER BY id"
            ).fetchall()
        if not rows:
            await query.edit_message_text(
                "Hozircha tekshirilayotgan to‘lov yo‘q.", reply_markup=owner_keyboard()
            )
            return
        await query.edit_message_text(
            "⏳ Kutilayotgan to‘lovlar:\n" + "\n".join(
                f"#{r['id']} — user {r['user_id']} — {r['plan_name']} — {money(r['amount'])}"
                for r in rows
            ) + "\n\nChek alohida xabar sifatida ham yuboriladi. "
            "Tasdiqlash: /approve ID\nRad etish: /reject ID",
            reply_markup=owner_keyboard(),
        )

    elif data == "support_queue":
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM support_messages WHERE status='open' ORDER BY id DESC LIMIT 20"
            ).fetchall()
        if not rows:
            await query.edit_message_text("Ochiq murojaat yo‘q.", reply_markup=owner_keyboard())
            return
        text = "💬 OCHIQ MUROJAATLAR\n\n" + "\n\n".join(
            f"#{r['id']} | user {r['user_id']} (@{r['username'] or 'username yo‘q'})\n{r['message'][:500]}\nJavob: /reply {r['user_id']} matn"
            for r in rows
        )
        await query.edit_message_text(text[:4000], reply_markup=owner_keyboard())

    elif data == "profile":
        user = db.get_user(user_id) or {}
        sub = db.active_subscription(user_id)
        contact = user.get("order_contact") or "Kiritilmagan"
        if sub:
            try:
                end = datetime.fromisoformat(sub["ends_at"])
                remaining = max(0, (end.date() - datetime.now().date()).days)
                sub_text = (
                    f"🟢 Tarif: {sub['plan_name']}\n"
                    f"📅 Tugash vaqti: {end.strftime('%d.%m.%Y %H:%M')}\n"
                    f"⏳ Qolgan kun: {remaining}"
                )
            except (ValueError, TypeError):
                sub_text = f"🟢 Tarif: {sub['plan_name']}\nTugash vaqti: {sub['ends_at']}"
        else:
            sub_text = "⚪ Faol obuna yo‘q"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Buyurtma kontaktini sozlash", callback_data="set_contact")],
            [InlineKeyboardButton("💎 Tarif sotib olish", callback_data="plans")],
            [InlineKeyboardButton("⬅️ Menyu", callback_data="home")],
        ])
        await query.edit_message_text(
            f"👤 PROFILIM\n\n🆔 Telegram ID: {user_id}\n"
            f"📛 Ism: {query.from_user.full_name}\n"
            f"🔗 Buyurtma kontakti: {contact}\n\n{sub_text}",
            reply_markup=keyboard,
        )

    elif data == "set_contact":
        context.user_data["setting_contact"] = True
        await query.message.reply_text(
            "Buyurtma tugmasi bosilganda mijoz qayerga o‘tishini kiriting.\n"
            "Masalan: @username yoki https://t.me/username\n"
            "Bekor qilish: /cancel",
            reply_markup=cancel_kb(),
        )

    elif data == "channels":
        channels = db.list_channels(user_id)
        rows = []
        lines = ["📣 KANALLARIM", ""]
        if channels:
            for c in channels:
                username = f"@{c['channel_username']}" if c.get("channel_username") else "username yo‘q"
                lines.append(
                    f"• {c['channel_title'] or 'Kanal'}\n"
                    f"  {username}\n  ID: {c['channel_id']}"
                )
                rows.append([InlineKeyboardButton(
                    f"🗑 O‘chirish: {c['channel_title'][:30] or c['channel_id']}",
                    callback_data=f"delchannel_{c['id']}",
                )])
        else:
            lines.append("Hozircha kanal ulanmagan.")
        lines.append("\nKanal ulash uchun pastdagi tugmani bosing. Botni kanalga administrator qiling.")
        rows.append([InlineKeyboardButton("➕ Kanal ulash", callback_data="add_channel")])
        rows.append([InlineKeyboardButton("🔄 Yangilash", callback_data="channels")])
        rows.append([InlineKeyboardButton("⬅️ Menyu", callback_data="home")])
        await query.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))

    elif data == "add_channel":
        await query.message.reply_text(
            "Kanalning @username yoki -100... ko‘rinishidagi ID sini yuboring.\n"
            "Kanal ommaviy bo‘lsa, havola ham yuborishingiz mumkin.\n"
            "Bot kanal administratori bo‘lishi va post yuborish huquqiga ega bo‘lishi shart."
        )
        context.user_data["awaiting_channel"] = True

    elif data.startswith("delchannel_"):
        try:
            row_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.message.reply_text("Kanal ID noto‘g‘ri.")
            return
        ok = db.delete_channel(user_id, row_id)
        await query.message.reply_text("✅ Kanal o‘chirildi." if ok else "Kanal topilmadi.")
        channels = db.list_channels(user_id)
        lines = ["📣 KANALLARIM"]
        for c in channels:
            lines.append(f"• {c['channel_title']} | {c['channel_id']}")
        await query.message.reply_text(
            "\n".join(lines),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Kanal ulash", callback_data="add_channel")],
                [InlineKeyboardButton("⬅️ Menyu", callback_data="home")],
            ]),
        )

    elif data == "plans":
        plans = db.list_plans()
        if not plans:
            await query.edit_message_text("Hozircha faol tarif mavjud emas.", reply_markup=main_menu(is_owner))
            return
        rows = []
        for p in plans:
            rows.append([InlineKeyboardButton(
                f"🛒 {p['name']} — {money(p['price'])}",
                callback_data=f"buyplan_{p['id']}",
            )])
        rows.append([InlineKeyboardButton("⬅️ Menyu", callback_data="home")])
        text = "💎 TARIFLAR\n\n"
        for p in plans:
            text += (
                f"✨ {p['name']}\n"
                f"💰 Narxi: {money(p['price'])}\n"
                f"📆 Muddati: {p['duration_days']} kun\n"
                f"📣 Kanallar: {p['channel_limit']}\n"
                f"📨 Kunlik post: {p['daily_post_limit']}\n\n"
            )
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(rows))

    elif data.startswith("buyplan_"):
        try:
            plan_id = int(data.split("_", 1)[1])
        except ValueError:
            await query.message.reply_text("Tarif ID noto‘g‘ri.")
            return
        plan = db.get_plan(plan_id)
        if not plan or not plan["is_active"]:
            await query.message.reply_text("Bu tarif hozir mavjud emas.")
            return
        card = db.get_setting("payment_card")
        owner = db.get_setting("payment_owner")
        instructions = db.get_setting("payment_instructions")
        context.user_data["buying_plan_id"] = plan_id
        # A new tariff selection starts a fresh receipt workflow.
        context.user_data.pop("payment_receipt", None)
        context.user_data.pop("pending_payment_id", None)
        if not card:
            await query.message.reply_text(
                "⚠️ To‘lov kartasi hali bot egasi tomonidan sozlanmagan."
            )
            return
        await query.message.reply_text(
            f"🛒 TARIF: {plan['name']}\n"
            f"💰 To‘lov: {money(plan['price'])}\n"
            f"📆 Muddat: {plan['duration_days']} kun\n\n"
            f"💳 Karta raqami: {card}\n"
            f"👤 Karta egasi: {owner or 'Ko‘rsatilmagan'}\n\n"
            f"📝 {instructions}\n\n"
            "To‘lovni amalga oshirgach, chek rasmini shu chatga yuboring. "
            "Chek yuborilishi bilan adminga avtomatik jo‘natiladi.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("⬅️ Tariflarga qaytish", callback_data="plans")],
            ]),
        )

    elif data == "payment_done":
        payment_id = context.user_data.get("pending_payment_id")
        if not payment_id:
            await query.message.reply_text(
                "Avval tarifni tanlab, chek rasmini yuboring. Chek yuborilganda admin avtomatik xabardor qilinadi."
            )
            return
        await query.message.reply_text(
            f"✅ To‘lov so‘rovingiz #{payment_id} adminga yuborilgan. Admin tekshirganidan so‘ng sizga natija keladi."
        )

    elif data == "support":
        await query.message.reply_text(
            "💬 QO‘LLAB-QUVVATLASH\n\n"
            "Savolingiz yoki muammoingizni bitta xabarda yozib yuboring. "
            "Admin javob berganda shu yerga keladi.\nBekor qilish: /cancel"
        )
        context.user_data["awaiting_support"] = True


async def product_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop("product_name", None)
    context.user_data.pop("product_details", None)
    context.user_data.pop("product_price", None)
    allowed, access_reason, days_left = db.posting_access(query.from_user.id, OWNER_ID)
    if not allowed:
        await query.edit_message_text(
            "⛔️ Bepul foydalanish muddati tugadi.\n\n"
            "Post joylashni davom ettirish uchun Pro tarifini xarid qiling. Tarif olinmaguncha post joylash yopiq bo‘ladi.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Pro tarifini olish", callback_data="plans")],
                                                [InlineKeyboardButton("🏠 Asosiy menyu", callback_data="home")]]),
        )
        return ConversationHandler.END
    trial_note = f"\n\n🎁 Bepul sinov: yana {days_left} kun." if access_reason == "trial" else ""
    await query.edit_message_text(
        "🛍 POST JOYLASH\n\n"
        "📷 Rasm va matnni BITTA xabarda yuboring: rasmni tanlang va matnni caption sifatida yozing.\n\n"
        "Matn kanalga aynan o‘zingiz yuborgan tartibda joylanadi — bot uni o‘zgartirmaydi." + trial_note,
        reply_markup=cancel_kb(),
    )
    return P_NAME


async def product_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Matn alohida yuborilsa, rasm bilan caption bir xabarda yuborilishi kerakligini eslatamiz.
    await update.message.reply_text(
        "Iltimos, rasm va matnni bitta Telegram xabarida yuboring. Rasmni tanlab, matnni caption maydoniga yozing.",
        reply_markup=cancel_kb(),
    )
    return P_NAME


async def product_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("Rasm va matnni bitta xabarda yuboring.")
        return P_NAME
    user_id = update.effective_user.id
    original_caption = update.message.caption or ""
    if not original_caption.strip():
        # A payment receipt can arrive while the product conversation is still active.
        # Do not treat a caption-less photo as a product post.
        if context.user_data.get("buying_plan_id"):
            await receipt_handler(update, context)
            return ConversationHandler.END
        await update.message.reply_text(
            "Bu rasm post sifatida qabul qilinmadi. Mahsulot joylashni menyudan ochib, "
            "rasm va matnni bitta xabarda yuboring."
        )
        return ConversationHandler.END
    try:
        # Keep the caption byte-for-byte as Telegram provides it; no parsing/reformatting.
        product_id = db.save_product(
            user_id,
            original_caption.splitlines()[0][:90] or "Post",
            original_caption,
            None,
            update.message.photo[-1].file_id,
            raw_caption=original_caption,
        )
        product = db.get_product(product_id, user_id)
        if not product:
            raise RuntimeError("Saqlangan post bazadan topilmadi")
    except Exception as exc:
        logger.exception("Postni saqlashda xato: %s", exc)
        await update.message.reply_text("⚠️ Postni saqlashda xatolik yuz berdi. Terminaldagi xatoni tekshiring.")
        return ConversationHandler.END

    contact = (db.get_user(user_id) or {}).get("order_contact", "")
    keyboard = [[InlineKeyboardButton("📤 Kanallarga joylash", callback_data=f"publish_{product_id}")],
                [InlineKeyboardButton("❌ Bekor qilish", callback_data="cancel_flow")]]
    await update.message.reply_photo(
        photo=product["photo_file_id"],
        caption=original_caption,
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return ConversationHandler.END

def schedule_keyboard(product_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀 Hozir — 1 marta", callback_data=f"schednow_{product_id}")],
        [InlineKeyboardButton("⏱ 30 minut", callback_data=f"schedonce_{product_id}_1800"),
         InlineKeyboardButton("⏱ 1 soat", callback_data=f"schedonce_{product_id}_3600")],
        [InlineKeyboardButton("⏱ 2 soat", callback_data=f"schedonce_{product_id}_7200"),
         InlineKeyboardButton("⏱ 3 soat", callback_data=f"schedonce_{product_id}_10800")],
        [InlineKeyboardButton("⏱ 4 soat", callback_data=f"schedonce_{product_id}_14400"),
         InlineKeyboardButton("⏱ 6 soat", callback_data=f"schedonce_{product_id}_21600")],
        [InlineKeyboardButton("⏱ 14 soat", callback_data=f"schedonce_{product_id}_50400"),
         InlineKeyboardButton("⏱ 24 soat", callback_data=f"schedonce_{product_id}_86400")],
        [InlineKeyboardButton("⏱ 48 soat", callback_data=f"schedonce_{product_id}_172800")],
        [InlineKeyboardButton("🔁 Takroriy post", callback_data=f"schedrepeat_{product_id}")],
    ])


def repeat_count_keyboard(product_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"{n} marta", callback_data=f"schedcount_{product_id}_{n}") for n in (2, 3, 4, 5)],
        [InlineKeyboardButton("6 marta", callback_data=f"schedcount_{product_id}_6"),
         InlineKeyboardButton("10 marta", callback_data=f"schedcount_{product_id}_10")],
        [InlineKeyboardButton("⬅️ Orqaga", callback_data=f"schedback_{product_id}")],
    ])


def repeat_interval_keyboard(product_id, count):
    # Takroriy post intervallari; 1 kunu 2 soat (26 soat) varianti yo‘q.
    intervals = [("1 soat",3600),("2 soat",7200),("3 soat",10800),
                 ("4 soat",14400),("6 soat",21600),("14 soat",50400),
                 ("24 soat",86400),("48 soat",172800)]
    rows=[]
    for i in range(0, len(intervals), 2):
        row=[]
        for label, seconds in intervals[i:i+2]:
            row.append(InlineKeyboardButton(label, callback_data=f"schedint_{product_id}_{count}_{seconds}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Orqaga", callback_data=f"schedrepeat_{product_id}")])
    return InlineKeyboardMarkup(rows)

def parse_duration_seconds(text):
    """Davomiylik: soat, minut yoki sekund. Kun ko‘rinishidagi matn qabul qilinmaydi."""
    text = (text or "").lower().strip()
    units = {
        "soat": 3600, "soati": 3600, "s": 1,
        "minut": 60, "daqiqa": 60, "min": 60,
        "sekund": 1, "soniya": 1,
    }
    total = 0
    for number, unit in re.findall(r"(\d+)\s*(soat(?:i)?|minut|daqiqa|min|sekund|soniya|s)\b", text):
        total += int(number) * units[unit]
    if total == 0:
        # Oddiy raqamni soat deb qabul qilmaymiz: aniq birlik talab qilinadi.
        return None
    return total


async def send_product_to_channels(context, user_id, product_id):
    allowed, access_reason, days_left = db.posting_access(user_id, OWNER_ID)
    if not allowed:
        await context.bot.send_message(
            user_id,
            "⛔️ Bepul foydalanish muddati tugadi. Post joylash uchun Pro tarifini xarid qiling.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Pro tarifini olish", callback_data="plans")]]),
        )
        return
    product = db.get_product(product_id, user_id)
    if not product:
        await context.bot.send_message(user_id, f"⚠️ #{product_id} mahsulot topilmadi; post yuborilmadi.")
        return
    channels = db.list_channels(user_id)
    if not channels:
        await context.bot.send_message(user_id, "⚠️ Kanallar ulanmaganligi sababli post yuborilmadi.")
        return
    user = db.get_user(user_id) or {}
    caption = product.get("raw_caption") or build_caption(product, user.get("order_contact", ""))
    contact = user.get("order_contact", "")
    rows = []
    if contact:
        url = contact if contact.startswith(("https://", "http://")) else f"https://t.me/{contact.lstrip('@')}"
        rows.append([InlineKeyboardButton("🛒 Buyurtma berish", url=url)])
    sent, failed = 0, []
    for channel in channels:
        try:
            member = await context.bot.get_chat_member(channel["channel_id"], context.bot.id)
            if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
                raise RuntimeError("Bot kanal administratori emas")
            await context.bot.send_photo(chat_id=channel["channel_id"], photo=product["photo_file_id"],
                                         caption=caption, reply_markup=InlineKeyboardMarkup(rows) if rows else None)
            db.log_post(user_id, channel["channel_id"], product_id, "sent")
            sent += 1
        except Exception as exc:
            logger.warning("Kanalga post yuborilmadi: %s", exc)
            db.log_post(user_id, channel["channel_id"], product_id, "failed")
            failed.append(channel.get("channel_title") or channel["channel_id"])
    result = f"✅ Post yuborish yakunlandi: {sent} ta kanalga joylandi."
    if failed:
        result += "\n⚠️ Muammo bo‘lgan kanallar: " + ", ".join(failed)
    await context.bot.send_message(user_id, result)


async def scheduled_post(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    await send_product_to_channels(context, data["user_id"], data["product_id"])


async def publish_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    await query.answer()
    allowed, access_reason, days_left = db.posting_access(user_id, OWNER_ID)
    if not allowed:
        await query.edit_message_caption(
            caption="⛔️ Bepul foydalanish muddati tugadi. Post joylashni davom ettirish uchun Pro tarifini xarid qiling.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💎 Pro tarifini olish", callback_data="plans")],
                                                [InlineKeyboardButton("🏠 Asosiy menyu", callback_data="home")]]),
        )
        return
    try:
        product_id = int(query.data.split("_", 1)[1])
    except (ValueError, IndexError):
        await query.message.reply_text("Mahsulot raqami noto‘g‘ri.")
        return
    product = db.get_product(product_id, user_id)
    if not product:
        await query.message.reply_text("Mahsulot topilmadi.")
        return
    if not db.list_channels(user_id):
        await query.message.reply_text("Avval Kanallarim bo‘limidan kanal ulang.")
        return
    await query.message.reply_text(
        "📅 POST JOYLASH REJASI\n\n"
        "Post matni va rasmi o‘zgartirilmaydi. Qachon va necha marta joylashni tanlang:",
        reply_markup=schedule_keyboard(product_id),
    )


async def schedule_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id
    try:
        if data.startswith("schednow_"):
            product_id = int(data.split("_")[1])
            await query.message.reply_text("🚀 Post hozir yuborilmoqda...")
            await send_product_to_channels(context, user_id, product_id)
        elif data.startswith("schedonce_"):
            _, product_text, seconds_text = data.split("_")
            product_id, seconds = int(product_text), int(seconds_text)
            context.job_queue.run_once(scheduled_post, seconds, data={"user_id": user_id, "product_id": product_id})
            await query.message.reply_text(f"✅ Post rejalashtirildi: {format_duration(seconds)} dan keyin.")
        elif data.startswith("schedrepeat_"):
            product_id = int(data.split("_")[1])
            await query.message.reply_text("Necha marta joylansin? Tugmani tanlang:", reply_markup=repeat_count_keyboard(product_id))
        elif data.startswith("schedcount_"):
            _, product_text, count_text = data.split("_")
            product_id, count = int(product_text), int(count_text)
            context.user_data["repeat_count"] = count
            context.user_data["repeat_product"] = product_id
            await query.message.reply_text(f"{count} marta tanlandi. Har bir post orasidagi vaqtni tanlang:", reply_markup=repeat_interval_keyboard(product_id, count))
        elif data.startswith("schedint_"):
            _, product_text, count_text, seconds_text = data.split("_")
            product_id, count, seconds = int(product_text), int(count_text), int(seconds_text)
            for index in range(1, count + 1):
                context.job_queue.run_once(scheduled_post, seconds * index, data={"user_id": user_id, "product_id": product_id})
            await query.message.reply_text(f"✅ Rejalashtirildi: {count} marta, har {format_duration(seconds)} da.")
        elif data.startswith("schedback_"):
            product_id = int(data.split("_")[1])
            await query.message.reply_text("Post vaqtini tanlang:", reply_markup=schedule_keyboard(product_id))
    except Exception as exc:
        logger.exception("Post rejalashtirishda xato: %s", exc)
        await query.message.reply_text("⚠️ Rejalashtirishda xato yuz berdi. Terminaldagi xatoni tekshiring.")


def format_duration(seconds):
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    parts=[]
    if days: parts.append(f"{days} kun")
    if hours: parts.append(f"{hours} soat")
    if minutes: parts.append(f"{minutes} minut")
    return " ".join(parts) or "1 minut"

async def schedule_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = context.user_data.get("schedule_custom_mode")
    if not mode:
        return False
    text = (update.message.text or "").strip().lower()
    product_id = context.user_data.get("schedule_custom_product")
    if mode == "delay":
        seconds = parse_duration_seconds(text)
        if not seconds:
            await update.message.reply_text("Vaqtni tushunmadim. Masalan: 2 soat 3 minut. Kun ko‘rinishidagi vaqtni tugmalardan tanlang.")
            return True
        context.job_queue.run_once(scheduled_post, seconds, data={"user_id": update.effective_user.id, "product_id": product_id})
        context.user_data.pop("schedule_custom_mode", None)
        context.user_data.pop("schedule_custom_product", None)
        await update.message.reply_text("✅ Rejalashtirildi. Post " + text + "dan keyin yuboriladi.")
        return True
    if mode == "repeat":
        match = re.search(r"(\d+)\s*marta\s*(?:har|oraliq(?:da)?)\s*(.+)", text)
        if not match:
            await update.message.reply_text("Format: 4 marta har 3 soat. 24 va 48 soatlik intervallarni tugmalardan tanlang.")
            return True
        count = int(match.group(1))
        seconds = parse_duration_seconds(match.group(2))
        if not seconds or count < 2 or count > 50:
            await update.message.reply_text("Takrorlash soni 2–50 orasida, interval esa masalan 4 soat bo‘lsin.")
            return True
        for index in range(1, count + 1):
            context.job_queue.run_once(scheduled_post, seconds * index,
                data={"user_id": update.effective_user.id, "product_id": product_id})
        context.user_data.pop("schedule_custom_mode", None)
        context.user_data.pop("schedule_custom_product", None)
        await update.message.reply_text(f"✅ Rejalashtirildi: {count} marta, har {match.group(2)}. Birinchi post interval tugagach yuboriladi.")
        return True
    return False


async def addchannel_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Kanal @username yoki -100... ID sini yuboring. "
        "Bot kanal administratori bo‘lishi shart.\n/cancel — bekor qilish"
    )
    return CH_CHANNEL


async def addchannel_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = (update.message.text or "").strip()
    if raw.startswith("https://t.me/"):
        raw = "@" + raw.rstrip("/").split("/")[-1]
    try:
        chat = await context.bot.get_chat(raw)
        member = await context.bot.get_chat_member(chat.id, context.bot.id)
        if member.status not in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER):
            await update.message.reply_text("Botni kanalga administrator qilib qo‘shing.")
            return ConversationHandler.END
        db.add_channel(
            update.effective_user.id,
            str(chat.id),
            chat.title or chat.username or str(chat.id),
            chat.username or "",
        )
        await update.message.reply_text(
            f"✅ Kanal ulandi!\n📣 Nomi: {chat.title or 'Noma’lum'}\n"
            f"🔗 Username: @{chat.username}" if chat.username else
            f"✅ Kanal ulandi!\n📣 Nomi: {chat.title or 'Noma’lum'}\n🆔 ID: {chat.id}"
        )
    except Exception as exc:
        logger.info("Kanalni ulash xatosi: %s", exc)
        await update.message.reply_text(
            "Kanalni tekshirib bo‘lmadi. Username/ID ni tekshiring va bot admin ekanini tasdiqlang."
        )
    return ConversationHandler.END


async def set_contact_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Bu sozlama faqat bot egasi uchun.")
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text(
            "Format: /setcard 8600 1234 5678 9012"
        )
        return
    db.set_setting("payment_card", text)
    await update.effective_message.reply_text("✅ To‘lov kartasi saqlandi.")


async def setcardowner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    value = " ".join(context.args).strip()
    if not value:
        await update.effective_message.reply_text("Format: /setcardowner Ism Familiya")
        return
    db.set_setting("payment_owner", value)
    await update.effective_message.reply_text("✅ Karta egasi saqlandi.")


async def setpaytext(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    value = " ".join(context.args).strip()
    if not value:
        await update.effective_message.reply_text("Format: /setpaytext To‘lovdan so‘ng chekni yuboring")
        return
    db.set_setting("payment_instructions", value)
    await update.effective_message.reply_text("✅ To‘lov izohi saqlandi.")


async def plan_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) != 5:
        await update.effective_message.reply_text(
            "Format: /tarif Nomi | narx | kun | kanal_limiti | kunlik_post_limiti\n"
            "Misol: /tarif Start | 39000 | 30 | 3 | 10"
        )
        return
    name = parts[0]
    try:
        price, days, channels, posts = map(int, parts[1:])
        if not name or min(price, days, channels, posts) < 1:
            raise ValueError
        db.create_plan(name, price, days, channels, posts)
    except ValueError:
        await update.effective_message.reply_text("Barcha raqamlar musbat butun son bo‘lishi kerak.")
        return
    await update.effective_message.reply_text(f"✅ {name} tarifi saqlandi.")


async def plan_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    try:
        plan_id = int(context.args[0])
        db.set_plan_active(plan_id, False)
        await update.effective_message.reply_text(f"Tarif #{plan_id} o‘chirildi.")
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Format: /tarifoff ID")


async def approve_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    try:
        payment_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Format: /approve ID")
        return
    payment = db.review_payment(payment_id, True)
    if not payment:
        await update.effective_message.reply_text("To‘lov topilmadi yoki allaqachon ko‘rib chiqilgan.")
        return
    await update.effective_message.reply_text(f"✅ #{payment_id} to‘lov tasdiqlandi.")
    try:
        await context.bot.send_message(
            payment["user_id"],
            f"🎉 To‘lovingiz tasdiqlandi!\n"
            f"💎 Tarif: {payment['plan_name']}\n"
            f"💰 Summa: {money(payment['amount'])}\n"
            "Obunangiz faollashtirildi. Profilim bo‘limida muddatni ko‘rishingiz mumkin.",
            reply_markup=main_menu(False),
        )
    except Exception as exc:
        logger.warning("Foydalanuvchiga tasdiq xabari yuborilmadi: %s", exc)


async def reject_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    try:
        payment_id = int(context.args[0])
    except (IndexError, ValueError):
        await update.effective_message.reply_text("Format: /reject ID sabab")
        return
    note = " ".join(context.args[1:]).strip() or "Chekni qayta tekshiring."
    payment = db.review_payment(payment_id, False, note)
    if not payment:
        await update.effective_message.reply_text("To‘lov topilmadi yoki allaqachon ko‘rib chiqilgan.")
        return
    await update.effective_message.reply_text(f"❌ #{payment_id} to‘lov rad etildi.")
    try:
        await context.bot.send_message(
            payment["user_id"],
            f"❌ To‘lov so‘rovingiz rad etildi.\nSabab: {note}\n"
            "Iltimos, chek va to‘lov ma’lumotlarini tekshirib, qayta urinib ko‘ring.",
            reply_markup=main_menu(False),
        )
    except Exception as exc:
        logger.warning("Foydalanuvchiga rad xabari yuborilmadi: %s", exc)


async def payment_review_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != OWNER_ID:
        await query.answer("Faqat bot egasi.", show_alert=True)
        return
    await query.answer()
    action, raw_id = query.data.split("_", 1)
    payment_id = int(raw_id)
    payment = db.review_payment(payment_id, action == "approvepay")
    if not payment:
        await query.message.reply_text("To‘lov allaqachon ko‘rib chiqilgan yoki topilmadi.")
        return
    approved = action == "approvepay"
    await query.message.reply_text(
        f"{'✅ Tasdiqlandi' if approved else '❌ Rad etildi'}: to‘lov #{payment_id}"
    )
    if approved:
        message = (
            f"🎉 To‘lovingiz tasdiqlandi!\nTarif: {payment['plan_name']}\n"
            f"Summa: {money(payment['amount'])}\nObunangiz faollashtirildi."
        )
    else:
        message = "❌ To‘lov rad etildi. Iltimos, admin bilan bog‘laning yoki qayta urinib ko‘ring."
    try:
        await context.bot.send_message(payment["user_id"], message, reply_markup=main_menu(False))
    except Exception as exc:
        logger.warning("Foydalanuvchiga xabar yuborilmadi: %s", exc)


async def receipt_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == OWNER_ID:
        return

    # Only accept receipt photos after the user has explicitly selected a tariff.
    # Never interpret arbitrary photos as posts or payment receipts.
    plan_id = context.user_data.get("buying_plan_id")
    if not plan_id:
        return

    if not update.message.photo:
        await update.message.reply_text("Chekni rasm ko‘rinishida yuboring.")
        return

    # Avoid creating duplicate payment requests if the user resends a photo
    # while the current receipt is already awaiting admin review.
    existing_payment_id = context.user_data.get("pending_payment_id")
    if existing_payment_id:
        await update.message.reply_text(
            f"🧾 Chek bo‘yicha so‘rov #{existing_payment_id} allaqachon adminga yuborilgan. Yangi chek yuborish uchun Tariflar bo‘limidan tarifni qayta tanlang."
        )
        return

    plan = db.get_plan(plan_id)
    if not plan:
        await update.message.reply_text("Tarif topilmadi. Tariflar bo‘limidan qayta tanlang.")
        return

    receipt = update.message.photo[-1].file_id
    user = update.effective_user
    payment_id = db.create_payment(user.id, plan, receipt)
    context.user_data["payment_receipt"] = receipt
    context.user_data["pending_payment_id"] = payment_id

    try:
        await context.bot.send_photo(
            chat_id=OWNER_ID,
            photo=receipt,
            caption=(
                f"🧾 YANGI TO‘LOV #{payment_id}\n"
                f"Foydalanuvchi: {user.full_name}\n"
                f"Username: @{user.username if user.username else 'mavjud emas'}\n"
                f"ID: {user.id}\nTarif: {plan['name']}\n"
                f"Summa: {money(plan['price'])}\n\n"
                f"Tasdiqlash: /approve {payment_id}\n"
                f"Rad etish: /reject {payment_id}"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"approvepay_{payment_id}"),
                InlineKeyboardButton("❌ Rad etish", callback_data=f"rejectpay_{payment_id}"),
            ]]),
        )
    except Exception as exc:
        logger.exception("Admin to‘lov xabari yuborilmadi (payment_id=%s): %s", payment_id, exc)
        await update.message.reply_text(
            "⚠️ Chek saqlandi, lekin adminga xabar yuborilmadi. Bot egasi BOT_TOKEN va OWNER_ID sozlamalarini tekshirishi kerak."
        )
        return

    await update.message.reply_text(
        "✅ To‘lov chekingiz qabul qilindi!\n\n"
        "🕒 To‘lovingiz admin tomonidan 12 soat ichida tekshiriladi.\n"
        "Natija bo‘yicha shu bot orqali xabar beramiz. Iltimos, kuting."
    )


async def support_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await schedule_text_handler(update, context):
        return
    if context.user_data.get("awaiting_support"):
        text = (update.message.text or "").strip()
        if not text:
            await update.message.reply_text("Savolingizni matn ko‘rinishida yozing.")
            return
        user = update.effective_user
        ticket_id = db.create_support_message(user.id, user.username or "", text)
        context.user_data["awaiting_support"] = False
        await update.message.reply_text(
            f"✅ Murojaatingiz #{ticket_id} yuborildi. Admin javobini shu botda olasiz."
        )
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"💬 YANGI MUROJAAT #{ticket_id}\n"
                f"Foydalanuvchi: {user.full_name} (@{user.username or 'username yo‘q'})\n"
                f"ID: {user.id}\n\n{text}\n\n"
                f"Javob: /reply {user.id} javob matni",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✉️ Javob berish bo‘yicha ko‘rsatma", callback_data="support_queue")
                ]]),
            )
        except Exception as exc:
            logger.warning("Support xabari adminga yuborilmadi: %s", exc)
        return

    if context.user_data.get("setting_contact"):
        contact = (update.message.text or "").strip()
        if not (contact.startswith("@") or contact.startswith("https://t.me/") or contact.startswith("https://")):
            await update.message.reply_text(
                "Iltimos, @username yoki https://t.me/username kabi havola yuboring."
            )
            return
        db.set_order_contact(update.effective_user.id, contact)
        context.user_data["setting_contact"] = False
        await update.message.reply_text(
            f"✅ Buyurtma kontakti saqlandi: {contact}\n"
            "Endi yangi postlarda buyurtma tugmasi shu manzilga olib boradi.",
            reply_markup=main_menu(update.effective_user.id == OWNER_ID),
        )
        return

    if context.user_data.get("awaiting_channel"):
        context.user_data["awaiting_channel"] = False
        await addchannel_receive(update, context)


async def reply_support(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.effective_message.reply_text("Ruxsat yo‘q.")
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Format: /reply USER_ID javob matni")
        return
    try:
        user_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("USER_ID raqam bo‘lishi kerak.")
        return
    text = " ".join(context.args[1:]).strip()
    await context.bot.send_message(
        user_id,
        f"💬 Qo‘llab-quvvatlash javobi:\n\n{text}",
    )
    await update.effective_message.reply_text("✅ Javob yuborildi.")


async def error_handler(update, context):
    logger.error("Bot xatosi", exc_info=context.error)


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP endpoint so Render Web Service detects an open port."""
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return
        body = b"Savdo bot ishlayapti"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Keep routine health checks from flooding Render logs.
        return


def start_render_health_server():
    """Open the port Render assigns; Telegram bot continues using polling."""
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, name="render-health-server", daemon=True)
    thread.start()
    logger.info("Render health server %s portida ishga tushdi", port)
    return server


def main():
    start_render_health_server()
    db.init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    product_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(product_add_start, pattern="^product_add$")],
        states={
            P_NAME: [MessageHandler(filters.PHOTO, product_photo), MessageHandler(filters.TEXT & ~filters.COMMAND, product_name)],
            P_PHOTO: [MessageHandler(filters.PHOTO, product_photo)],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel_button, pattern="^cancel_flow$"),
        ],
        per_user=True,
        per_chat=True,
        allow_reentry=True,
    )
    channel_conv = ConversationHandler(
        entry_points=[CommandHandler("addchannel", addchannel_start)],
        states={CH_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, addchannel_receive)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        per_chat=True,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", owner_command))
    app.add_handler(CommandHandler("tarif", plan_save))
    app.add_handler(CommandHandler("tarifoff", plan_off))
    app.add_handler(CommandHandler("setcard", set_contact_start))
    app.add_handler(CommandHandler("setcardowner", setcardowner))
    app.add_handler(CommandHandler("setpaytext", setpaytext))
    app.add_handler(CommandHandler("approve", approve_payment))
    app.add_handler(CommandHandler("reject", reject_payment))
    app.add_handler(CommandHandler("reply", reply_support))
    app.add_handler(CommandHandler("cancel", cancel))

    app.add_handler(product_conv)
    app.add_handler(channel_conv)
    app.add_handler(CallbackQueryHandler(publish_product, pattern=r"^publish_\d+$"))
    app.add_handler(CallbackQueryHandler(schedule_action, pattern=r"^(schednow_\d+|schedonce_\d+_\d+|schedrepeat_\d+|schedcount_\d+_\d+|schedint_\d+_\d+_\d+|schedback_\d+)$"))
    app.add_handler(CallbackQueryHandler(payment_review_button, pattern=r"^(approvepay|rejectpay)_\d+$"))
    app.add_handler(CallbackQueryHandler(cancel_button, pattern="^cancel_flow$"))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, receipt_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, support_text_handler))
    app.add_error_handler(error_handler)

    logger.info("Savdo boti ishga tushmoqda...")
    app.run_polling()


if __name__ == "__main__":
    main()
