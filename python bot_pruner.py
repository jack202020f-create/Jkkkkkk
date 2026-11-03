
import os
import logging
import sqlite3
import time

from telegram import Update
from telegram.ext import (
    Application,
    MessageHandler,
    filters,
    ContextTypes,
)

# =========================
# CONFIGURATION
# =========================

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

DB_PATH = "bot_activity.db"
INACTIVE_DAYS = 3
CHECK_INTERVAL = 3600

if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN is missing")

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# =========================
# DATABASE
# =========================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_activity (
                chat_id INTEGER,
                bot_id INTEGER,
                last_seen INTEGER,
                PRIMARY KEY (chat_id, bot_id)
            )
        """)


def update_activity(chat_id, bot_id):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO bot_activity
            (chat_id, bot_id, last_seen)
            VALUES (?, ?, ?)
            ON CONFLICT(chat_id, bot_id)
            DO UPDATE SET last_seen = excluded.last_seen
        """, (chat_id, bot_id, int(time.time())))


# =========================
# TRACK NEW MEMBERS
# =========================

async def track_new_members(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.message or not update.effective_chat:
        return

    if update.effective_chat.type not in ("group", "supergroup"):
        return

    for member in update.message.new_chat_members:
        if member.is_bot:
            update_activity(
                update.effective_chat.id,
                member.id
            )

            logging.info(
                "New bot detected: %s",
                member.id
            )


# =========================
# TRACK BOT MESSAGES
# =========================

async def track_messages(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not update.effective_chat or not update.effective_user:
        return

    if update.effective_chat.type not in ("group", "supergroup"):
        return

    if update.effective_user.is_bot:
        update_activity(
            update.effective_chat.id,
            update.effective_user.id
        )


# =========================
# PRUNE INACTIVE BOTS
# =========================

async def prune_inactive_bots(
    context: ContextTypes.DEFAULT_TYPE
):
    cutoff = int(time.time()) - (
        INACTIVE_DAYS * 24 * 60 * 60
    )

    with sqlite3.connect(DB_PATH) as conn:
        inactive_bots = conn.execute("""
            SELECT chat_id, bot_id
            FROM bot_activity
            WHERE last_seen < ?
        """, (cutoff,)).fetchall()

    for chat_id, bot_id in inactive_bots:

        if bot_id == context.bot.id:
            continue

        try:
            await context.bot.ban_chat_member(
                chat_id=chat_id,
                user_id=bot_id
            )

            await context.bot.unban_chat_member(
                chat_id=chat_id,
                user_id=bot_id
            )

            with sqlite3.connect(DB_PATH) as conn:
                conn.execute("""
                    DELETE FROM bot_activity
                    WHERE chat_id = ? AND bot_id = ?
                """, (chat_id, bot_id))

            logging.info(
                "Removed inactive bot %s from chat %s",
                bot_id,
                chat_id
            )

        except Exception as error:
            logging.error(
                "Failed to remove bot %s: %s",
                bot_id,
                error
            )


# =========================
# MAIN
# =========================

def main():
    init_db()

    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    app.add_handler(
        MessageHandler(
            filters.StatusUpdate.NEW_CHAT_MEMBERS,
            track_new_members
        )
    )

    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.StatusUpdate.ALL,
            track_messages
        )
    )

    if app.job_queue is None:
        raise RuntimeError(
            "Install python-telegram-bot[job-queue]"
        )

    app.job_queue.run_repeating(
        prune_inactive_bots,
        interval=CHECK_INTERVAL,
        first=10
    )

    logging.info(
        "Bot Pruner started. Monitoring activity..."
    )

    app.run_polling()


if __name__ == "__main__":
    main()
