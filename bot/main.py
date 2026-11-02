
import os
import re
import html
import sqlite3
import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dotenv import load_dotenv
from telegram import Update, ChatPermissions, BotCommand
from telegram.constants import ChatMemberStatus
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = ROOT / "database" / "moderation.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)

DEFAULT_RULES = """📜 MC MOD HUB
━━━━━━━━━━━━━━━━━━
📌 GURUH QOIDALARI
━━━━━━━━━━━━━━━━━━

🚫 1. SO‘KINISH MUMKIN EMAS
Matn, rasm, sticker, video va ovozli xabarlarda so‘kinish taqiqlanadi.

🚫 2. HAQORAT VA KAMSITISH YO‘Q
Millati, irqi, tili yoki boshqa jihatlariga qarab kamsitish taqiqlanadi.

🚫 3. RUXSATSIZ REKLAMA YO‘Q
Admin yoki Owner ruxsatisiz reklama va targ‘ibot qilish mumkin emas.

🚫 4. JANJAL CHIQARMANG
Ataylab nizo chiqarish va janjal qo‘zg‘atish mumkin emas.

🚫 5. FLOOD TAQIQLANADI
Bir xil xabarlarni takrorlash mumkin emas.

🚫 6. BOSHQA A'ZOLARNI BEZOVTA QILMANG
Guruh tinchligi va barcha a'zolarning hurmatini saqlang.

━━━━━━━━━━━━━━━━━━
⚖️ JAZO TARTIBI
━━━━━━━━━━━━━━━━━━

⚠️ 1–2 WARN — ogohlantirish
🔇 3 WARN — 3 soat mute
🔇 4 WARN — 6 soat mute
🔇 5 WARN — 12 soat mute
⛔ Keyingi qoidabuzarlik — ban

❗ Jiddiy qoidabuzarlik uchun admin vaziyatga qarab alohida jazo qo‘llashi mumkin.

🤝 Qoidalar barcha a'zolar uchun bir xil.

👑 Hurmat bilan, McModHub Admini — Fazilchik"""


def connect_db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with connect_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (chat_id, user_id)
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS group_rules (
                chat_id INTEGER PRIMARY KEY,
                rules TEXT NOT NULL
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL
            )
        """)

        # Sayt bilan umumiy jazo tarixi
        db.execute("""
            CREATE TABLE IF NOT EXISTS punishment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                punishment_type TEXT NOT NULL,
                reason TEXT,
                duration TEXT,
                admin_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        migrated = db.execute(
            "SELECT setting_value FROM bot_settings "
            "WHERE setting_key = 'default_rules_migrated'"
        ).fetchone()

        if not migrated:
            db.execute(
                "UPDATE group_rules SET rules = ?",
                (DEFAULT_RULES,),
            )
            db.execute(
                "INSERT INTO bot_settings (setting_key, setting_value) "
                "VALUES ('default_rules_migrated', '1')"
            )


def log_punishment(update, user, punishment_type,
                   reason="Ko‘rsatilmagan", duration=None):
    admin = update.effective_user
    chat = update.effective_chat

    admin_name = "Noma’lum"
    if admin:
        admin_name = (
            f"@{admin.username}"
            if admin.username
            else admin.full_name
        )

    with connect_db() as db:
        db.execute("""
            INSERT INTO punishment_history (
                chat_id, user_id, username, full_name,
                punishment_type, reason, duration, admin_name,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            chat.id if chat else None,
            user.id,
            user.username,
            user.full_name,
            punishment_type.upper(),
            reason,
            duration,
            admin_name,
            datetime.now(timezone.utc).isoformat()
        ))


def get_count(chat_id, user_id):
    with connect_db() as db:
        row = db.execute(
            "SELECT count FROM warnings WHERE chat_id=? AND user_id=?",
            (chat_id, user_id),
        ).fetchone()

    return row[0] if row else 0


def set_count(chat_id, user_id, count):
    now = datetime.now(timezone.utc).isoformat()

    with connect_db() as db:
        if count <= 0:
            db.execute(
                "DELETE FROM warnings WHERE chat_id=? AND user_id=?",
                (chat_id, user_id),
            )
        else:
            db.execute("""
                INSERT INTO warnings (chat_id, user_id, count, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id)
                DO UPDATE SET
                    count=excluded.count,
                    updated_at=excluded.updated_at
            """, (chat_id, user_id, count, now))


async def start_command(update, context):
    message = update.effective_message

    if not message:
        return

    await message.reply_text(
        "👋 Salom! Men Tartibsaqla_bot — McModHub guruh moderatori.\n\n"
        "📜 /qoida — guruh qoidalari\n"
        "🆘 /yordam — adminlarni yordamga chaqirish\n"
        "⚙️ Moderatsiya buyruqlari guruhda ishlaydi.\n"
        "🔐 Jazo buyruqlaridan faqat guruh adminlari foydalanishi mumkin."
    )


async def is_admin(update, context, user_id=None):
    chat = update.effective_chat

    if not chat or chat.type == "private":
        return False

    if user_id is None:
        user = update.effective_user
        if not user:
            return False
        user_id = user.id

    try:
        member = await context.bot.get_chat_member(chat.id, user_id)
        return member.status in (
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except TelegramError:
        logging.exception("Admin holatini tekshirishda xato")
        return False


async def require_admin(update, context):
    if not await is_admin(update, context):
        message = update.effective_message
        if message:
            await message.reply_text(
                "⛔ Bu buyruqdan faqat guruh adminlari foydalanishi mumkin."
            )
        return False

    return True


async def help_admins_command(update, context):
    chat = update.effective_chat
    message = update.effective_message
    caller = update.effective_user

    if not chat or not message or chat.type == "private":
        if message:
            await message.reply_text(
                "🆘 /yordam buyrug‘ini guruh ichida ishlating."
            )
        return

    if not caller:
        return

    if await is_admin(update, context, caller.id):
        await message.reply_text(
            "🛡 Siz adminsiz. Boshqa adminlarni chaqirish shart emas."
        )
        return

    try:
        administrators = await context.bot.get_chat_administrators(chat.id)
    except TelegramError:
        logging.exception("Guruh adminlarini olishda xato")
        await message.reply_text(
            "❌ Adminlar ro‘yxatini olish imkoni bo‘lmadi."
        )
        return

    mentions = []

    for administrator in administrators:
        admin_user = administrator.user
        if not admin_user.is_bot:
            mentions.append(admin_user.mention_html())

    if not mentions:
        await message.reply_text("❌ Guruh adminlari topilmadi.")
        return

    caller_name = caller.mention_html()
    group_name = html.escape(chat.title or "Guruh")

    reported_message = message.reply_to_message
    target_message_id = (
        reported_message.message_id
        if reported_message
        else message.message_id
    )

    if chat.username:
        message_url = (
            f"https://t.me/{chat.username}/{target_message_id}"
        )
    elif str(chat.id).startswith("-100"):
        internal_chat_id = str(chat.id)[4:]
        message_url = (
            f"https://t.me/c/{internal_chat_id}/{target_message_id}"
        )
    else:
        message_url = None

    if message_url:
        context_line = (
            f'<a href="{message_url}">Guruhdagi vaziyatni ko‘rish</a>'
        )
    else:
        context_line = "Adminlar guruhdagi vaziyatni tekshiring."

    alert_text = (
        "🆘 <b>ADMIN YORDAMI KERAK!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"👤 Chaqirgan: {caller_name}\n"
        f"💬 Guruh: {group_name}\n\n"
        f"{context_line}\n\n"
        "📢 <b>Chaqirilgan adminlar:</b>\n"
        + "\n".join(mentions)
        + "\n\n⚠️ Iltimos, vaziyatni tekshiring."
    )

    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=alert_text,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=target_message_id,
            allow_sending_without_reply=True,
        )
        await message.reply_text(
            "✅ Adminlarga yordam chaqiruvi yuborildi."
        )
    except TelegramError:
        logging.exception("Adminlarga yordam chaqirishda xato")
        await message.reply_text("❌ Chaqiruv yuborilmadi.")


async def get_target(update, context):
    message = update.effective_message

    if not message:
        return None

    reply = message.reply_to_message

    if not reply or not reply.from_user:
        await message.reply_text(
            "ℹ️ Foydalanuvchining xabariga Reply qilib buyruq yuboring."
        )
        return None

    user = reply.from_user

    if user.is_bot:
        await message.reply_text("🤖 Botni jazolab bo‘lmaydi.")
        return None

    if update.effective_user and user.id == update.effective_user.id:
        await message.reply_text("O‘zingizga bu jazoni qo‘llay olmaysiz.")
        return None

    if await is_admin(update, context, user.id):
        await message.reply_text(
            "🛡 Guruh admini yoki Ownerini jazolab bo‘lmaydi."
        )
        return None

    return user


def parse_duration(value):
    match = re.fullmatch(
        r"\s*(\d+)\s*(m|minut|minutlar|daq|daqiqa|h|soat|d|kun)\s*",
        value.lower(),
    )

    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if amount < 1:
        return None

    if unit in ("m", "minut", "minutlar", "daq", "daqiqa"):
        duration = timedelta(minutes=amount)
    elif unit in ("h", "soat"):
        duration = timedelta(hours=amount)
    else:
        duration = timedelta(days=amount)

    if duration > timedelta(days=365):
        return None

    return duration


def format_duration(duration):
    seconds = int(duration.total_seconds())

    if seconds % 86400 == 0:
        return f"{seconds // 86400} kun"

    if seconds % 3600 == 0:
        return f"{seconds // 3600} soat"

    return f"{seconds // 60} daqiqa"


async def rules_command(update, context):
    chat = update.effective_chat
    message = update.effective_message

    if not chat or not message or chat.type == "private":
        if message:
            await message.reply_text("Bu buyruqni guruh ichida ishlating.")
        return

    with connect_db() as db:
        db.execute("""
            INSERT INTO group_rules (chat_id, rules)
            VALUES (?, ?)
            ON CONFLICT(chat_id)
            DO UPDATE SET rules=excluded.rules
        """, (chat.id, DEFAULT_RULES))

    await message.reply_text(DEFAULT_RULES)


async def setrules_command(update, context):
    if not await require_admin(update, context):
        return

    message = update.effective_message
    if not message or not message.text:
        return

    rules = message.text.partition(" ")[2].strip()

    if not rules:
        await message.reply_text(
            "Foydalanish:\n/setrules Yangi qoidalar matni"
        )
        return

    with connect_db() as db:
        db.execute("""
            INSERT INTO group_rules (chat_id, rules)
            VALUES (?, ?)
            ON CONFLICT(chat_id)
            DO UPDATE SET rules=excluded.rules
        """, (update.effective_chat.id, rules))

    await message.reply_text("✅ Guruh qoidalari yangilandi.")


async def warn_command(update, context):
    if not await require_admin(update, context):
        return

    user = await get_target(update, context)
    if not user:
        return

    chat_id = update.effective_chat.id
    count = get_count(chat_id, user.id) + 1
    set_count(chat_id, user.id, count)

    # Har bir yangi WARN tarixga yoziladi.
    log_punishment(update, user, "WARN")

    if count <= 2:
        await update.effective_message.reply_text(
            f"⚠️ {user.full_name} ogohlantirildi.\nJami WARN: {count}"
        )
        return

    if count == 3:
        hours = 3
    elif count == 4:
        hours = 6
    elif count == 5:
        hours = 12
    else:
        try:
            await context.bot.ban_chat_member(chat_id, user.id)
            log_punishment(
                update, user, "BAN",
                reason="Takroriy qoidabuzarlik"
            )
            await update.effective_message.reply_text(
                f"⛔ {user.full_name} takroriy qoidabuzarlik uchun ban qilindi."
            )
        except TelegramError as exc:
            await update.effective_message.reply_text(
                f"Ban bajarilmadi. Bot admin huquqlarini tekshiring.\n{exc}"
            )
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=chat_id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now(timezone.utc) + timedelta(hours=hours),
        )
        log_punishment(
            update, user, "MUTE",
            reason=f"{count} ta WARN natijasida",
            duration=f"{hours} soat"
        )
        await update.effective_message.reply_text(
            f"🔇 {user.full_name} jim qilindi.\n"
            f"WARN: {count}\nMuddat: {hours} soat"
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"WARN saqlandi, lekin mute bajarilmadi.\n{exc}"
        )


async def unwarn_command(update, context):
    if not await require_admin(update, context):
        return

    user = await get_target(update, context)
    if not user:
        return

    count = get_count(update.effective_chat.id, user.id)

    if count == 0:
        await update.effective_message.reply_text(
            "Bu foydalanuvchida ogohlantirish yo‘q."
        )
        return

    count -= 1
    set_count(update.effective_chat.id, user.id, count)
    log_punishment(update, user, "UNWARN")

    await update.effective_message.reply_text(
        f"✅ Bitta WARN olib tashlandi.\nQolgan WARN: {count}"
    )


async def warnings_command(update, context):
    if not await require_admin(update, context):
        return

    user = await get_target(update, context)
    if not user:
        return

    count = get_count(update.effective_chat.id, user.id)

    await update.effective_message.reply_text(
        f"📋 {user.full_name}\nWARN soni: {count}"
    )


async def mute_command(update, context):
    if not await require_admin(update, context):
        return

    user = await get_target(update, context)
    if not user:
        return

    duration = parse_duration(" ".join(context.args))

    if duration is None:
        await update.effective_message.reply_text(
            "Muddatni kiriting:\n"
            "/jimlik 30m — 30 daqiqa\n"
            "/jimlik 2h — 2 soat\n"
            "/jimlik 1d — 1 kun"
        )
        return

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user.id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=datetime.now(timezone.utc) + duration,
        )
        log_punishment(
            update, user, "MUTE",
            duration=format_duration(duration)
        )
        await update.effective_message.reply_text(
            f"🔇 {user.full_name}\n"
            f"Mute muddati: {format_duration(duration)}"
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"Mute bajarilmadi. Bot admin huquqlarini tekshiring.\n{exc}"
        )


async def unmute_command(update, context):
    if not await require_admin(update, context):
        return

    user = await get_target(update, context)
    if not user:
        return

    permissions = ChatPermissions(
        can_send_messages=True,
        can_send_audios=True,
        can_send_documents=True,
        can_send_photos=True,
        can_send_videos=True,
        can_send_video_notes=True,
        can_send_voice_notes=True,
        can_send_polls=True,
        can_send_other_messages=True,
        can_add_web_page_previews=True,
    )

    try:
        await context.bot.restrict_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user.id,
            permissions=permissions,
        )
        log_punishment(update, user, "UNMUTE")
        await update.effective_message.reply_text(
            f"🔊 {user.full_name} uchun mute bekor qilindi."
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"Unmute bajarilmadi. Bot huquqlarini tekshiring.\n{exc}"
        )


async def ban_command(update, context):
    if not await require_admin(update, context):
        return

    user = await get_target(update, context)
    if not user:
        return

    try:
        await context.bot.ban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user.id,
        )
        log_punishment(update, user, "BAN")
        await update.effective_message.reply_text(
            f"⛔ {user.full_name} guruhdan ban qilindi."
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"Ban bajarilmadi. Bot admin huquqlarini tekshiring.\n{exc}"
        )


async def unban_command(update, context):
    if not await require_admin(update, context):
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Foydalanish: /qayt USER_ID"
        )
        return

    user_id = int(context.args[0])

    try:
        await context.bot.unban_chat_member(
            chat_id=update.effective_chat.id,
            user_id=user_id,
            only_if_banned=True,
        )

        unknown_user = SimpleNamespace(
            id=user_id,
            username=None,
            full_name=str(user_id)
        )
        log_punishment(update, unknown_user, "UNBAN")

        await update.effective_message.reply_text(
            f"✅ {user_id} ban'dan chiqarildi."
        )
    except TelegramError as exc:
        await update.effective_message.reply_text(
            f"Unban bajarilmadi. ID va bot huquqlarini tekshiring.\n{exc}"
        )


async def welcome_new_members(update, context):
    message = update.effective_message

    if not message or not message.new_chat_members:
        return

    for user in message.new_chat_members:
        if user.id == context.bot.id:
            continue

        greeting = (
            f"@{user.username}"
            if user.username
            else user.mention_html()
        )

        await message.reply_text(
            f"{greeting} 👋 Xush kelibsiz! 💚\n\n"
            "🎮 <b>MC Mod Hub</b> chatiga xush kelibsiz!\n\n"
            "💬 Bemalol suhbatlashing va Minecraft haqida fikr almashing! ⛏️",
            parse_mode="HTML",
        )


async def post_init(app):
    commands = [
        BotCommand("start", "Bot haqida ma'lumot"),
        BotCommand("qoida", "Guruh qoidalari"),
        BotCommand("yordam", "Adminlarni yordamga chaqirish"),
        BotCommand("jazo", "Reply orqali warn berish"),
        BotCommand("ozodlik", "Reply orqali warnni olish"),
        BotCommand("jazolar", "Warn sonini ko'rish"),
        BotCommand("jimlik", "Reply orqali mute qilish"),
        BotCommand("gapir", "Reply orqali unmute qilish"),
        BotCommand("xaydalding", "Reply orqali ban qilish"),
        BotCommand("qayt", "USER_ID orqali unban qilish"),
        BotCommand("setrules", "Guruh qoidalarini o'zgartirish"),
    ]

    await app.bot.set_my_commands(commands)
    logging.info("Telegram bot buyruqlari sozlandi.")


async def error_handler(update, context):
    logging.error(
        "Bot xatosi. Update=%s",
        getattr(update, "update_id", None),
        exc_info=context.error,
    )


def main():
    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Loyiha papkasidagi .env faylini tekshiring."
        )

    init_db()

    app = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("qoida", rules_command))
    app.add_handler(CommandHandler("yordam", help_admins_command))
    app.add_handler(CommandHandler("setrules", setrules_command))

    app.add_handler(CommandHandler(["jazo", "warn"], warn_command))
    app.add_handler(CommandHandler(["ozodlik", "unwarn"], unwarn_command))
    app.add_handler(CommandHandler(["jazolar", "warnings"], warnings_command))
    app.add_handler(CommandHandler(["jimlik", "mute"], mute_command))
    app.add_handler(CommandHandler(["gapir", "unmute"], unmute_command))
    app.add_handler(CommandHandler(["xaydalding", "ban"], ban_command))
    app.add_handler(CommandHandler(["qayt", "unban"], unban_command))

    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            welcome_new_members,
        )
    )

    app.add_error_handler(error_handler)

    print("McModHub | Jazolanganlar ishga tushmoqda...")
    app.run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
