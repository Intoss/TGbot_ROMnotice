# OWNER_ID is set to your Telegram ID (owner): 1850766719

import os
import sqlite3
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict
from telegram import BotCommand, MenuButtonCommands
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (ApplicationBuilder, ContextTypes, CommandHandler,
                          CallbackQueryHandler, MessageHandler, filters)

# ---------------- CONFIG ----------------
OWNER_ID = 1850766719  # твой ID - владелец бота
#TOKEN = os.environ.get("TELEGRAM_TOKEN")  # поставь токен в ENV на Replit
TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = "bot.db"

# Two clans
CLANS = ["BALDEG", "AlterEgo"]
awaiting_custom_timer: Dict[str, Dict] = {}
# Bosses list exactly as requested (keep numbers)
# Map key -> (display_name, respawn_hours)
#BOSSES = {
#"02. Windlong (Gigantus)": 2,
#"03. Death Valley (DeathCrow)": 2,
#"05. Dark Forest (Floneble)": 3,
#"06. Limst (Chimera)": 3,
#"08. Fire Plains (Lindwurm)": 3,
#"10. Croco Forest (Gyes)": 3,
#"12. Fog Valley (Thrandir)": 4,
#"14. Kar. Volcano (Ruginoa)": 4,
#"17. Cremo Lake (Briare)": 5,
#"18. Rain Bay (Lythea)": 5,
#"19. Akama Salt Desert (Leo)": 5,
# }
# ----------------------------------------
BOSSES = {
    "02.Map ": 2,
    "03.Map ": 2,
    "05.Map ": 3,
    "06.Map ": 3,
    "08.Map ": 3,
    "10.Map ": 3,
    "12.Map ": 4,
    "14.Map ": 4,
    "17.Map ": 5,
    "18.Map ": 5,
    "19.Map ": 5,
}
# In-memory running tasks (boss_key -> asyncio.Task)
boss_tasks: Dict[str, asyncio.Task] = {}


# ---------------- Database ----------------
def init_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    c = conn.cursor()
    # users: telegram_id (unique), role ('user' or 'admin')
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'user'
        )
        """)

    # bosses: name (primary key), respawn_hours, last_killer (nullable), respawn_end_ts (nullable)
    c.execute("""
        CREATE TABLE IF NOT EXISTS bosses (
            name TEXT PRIMARY KEY,
            respawn_hours INTEGER NOT NULL,
            last_killer TEXT,
            respawn_end_ts INTEGER
        )
        """)

    # populate bosses table if empty
    for name, hours in BOSSES.items():
        c.execute(
            "INSERT OR IGNORE INTO bosses (name, respawn_hours) VALUES (?, ?)",
            (name, hours),
        )

    # ensure owner exists as admin
    c.execute(
        "INSERT OR IGNORE INTO users (telegram_id, role) VALUES (?, ?)",
        (OWNER_ID, "admin"),
    )

    conn.commit()
    return conn


db_conn = init_db()


def add_user_if_not_exists(telegram_id: int):
    c = db_conn.cursor()
    c.execute("SELECT telegram_id FROM users WHERE telegram_id = ?",
              (telegram_id, ))
    if c.fetchone() is None:
        c.execute("INSERT INTO users (telegram_id, role) VALUES (?, ?)",
                  (telegram_id, "user"))
        db_conn.commit()


def set_admin(telegram_id: int):
    c = db_conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO users (telegram_id, role) VALUES (?, ?)",
        (telegram_id, "admin"),
    )
    db_conn.commit()


def is_admin(telegram_id: int) -> bool:
    if telegram_id == OWNER_ID:
        return True
    c = db_conn.cursor()
    c.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id, ))
    r = c.fetchone()
    return r is not None and r[0] == "admin"


def get_all_user_ids():
    c = db_conn.cursor()
    c.execute("SELECT telegram_id FROM users")
    return [row[0] for row in c.fetchall()]


def set_boss_killer_and_respawn(boss_name: str, killer: str,
                                respawn_end_ts: int):
    c = db_conn.cursor()
    c.execute(
        "UPDATE bosses SET last_killer = ?, respawn_end_ts = ? WHERE name = ?",
        (killer, respawn_end_ts, boss_name),
    )
    db_conn.commit()


def get_boss_info(boss_name: str):
    c = db_conn.cursor()
    c.execute(
        "SELECT respawn_hours, last_killer, respawn_end_ts FROM bosses WHERE name = ?",
        (boss_name, ),
    )
    row = c.fetchone()
    if row:
        return {
            "respawn_hours": row[0],
            "last_killer": row[1],
            "respawn_end_ts": row[2]
        }
    return None


def get_all_bosses():
    c = db_conn.cursor()
    c.execute(
        "SELECT name, respawn_hours, last_killer, respawn_end_ts FROM bosses")
    return c.fetchall()


# ---------------- Utilities ----------------
def format_datetime_ts(ts: int) -> str:
    # ts is unix timestamp (seconds)
    tz = timezone(timedelta(hours=3))  # UTC+3
    return datetime.fromtimestamp(ts, tz=tz).strftime("%d-%m %H:%M:%S")


async def broadcast_message(application, text: str):
    user_ids = get_all_user_ids()
    for uid in user_ids:
        try:
            await application.bot.send_message(
                chat_id=uid,
                text=text,
                parse_mode="HTML"  # включаем поддержку HTML
            )
        except Exception:
            # ignore failed sends (user blocked bot etc.)
            pass


def build_menu_keyboard():
    rows = []
    timestamp = int(datetime.now().timestamp())  # уникальность кнопок
    for name, hours in BOSSES.items():
        info = get_boss_info(name)
        last = info["last_killer"] if info and info["last_killer"] else "—"
        respawn_ts = info[
            "respawn_end_ts"] if info and info["respawn_end_ts"] else None
        respawn_text = format_datetime_ts(respawn_ts) if respawn_ts else "—"

        # очередь клана
        queue_clan = None
        if last and last in CLANS:
            queue_clan = [c for c in CLANS if c != last][0]

        label = f"{name}\nNext: {queue_clan if queue_clan else '—'}\nResp: {respawn_text}"
        rows.append([
            InlineKeyboardButton(label,
                                 callback_data=f"boss_view|{name}|{timestamp}")
        ])

    # кнопки обновления и помощи
    rows.append([
        InlineKeyboardButton("Обновить 🔄",
                             callback_data=f"menu_refresh|{timestamp}"),
        InlineKeyboardButton("Объяснение ❓", callback_data=f"help|{timestamp}")
    ])
    return InlineKeyboardMarkup(rows)


def build_boss_choice_keyboard(boss_name: str):
    rows = []
    timestamp = int(datetime.now().timestamp())  # уникальность кнопок
    for clan in CLANS:
        rows.append([
            InlineKeyboardButton(
                clan,
                callback_data=f"boss_kill|{boss_name}|{clan}|{timestamp}")
        ])

    # кнопки "Другие" и "Настройка"
    rows.append([
        InlineKeyboardButton(
            "Другие", callback_data=f"boss_other|{boss_name}|{timestamp}")
    ])
    rows.append([
        InlineKeyboardButton(
            "Настройка ⚙️",
            callback_data=f"boss_setup|{boss_name}|{timestamp}")
    ])
    rows.append([
        InlineKeyboardButton("Назад ◀️",
                             callback_data=f"menu_back|{timestamp}")
    ])
    return InlineKeyboardMarkup(rows)


# ---------------- Handlers ----------------
async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    telegram_id = user.id
    add_user_if_not_exists(telegram_id)
    text = f"Ваш Telegram ID: {telegram_id}\nВы зарегистрированы в системе."

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Start ▶️", callback_data="first_start")],
    ])

    await update.effective_chat.send_message(text, reply_markup=keyboard)


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Инструкция:\n"
        "- /start — регистрация в системе и получение меню боссов\n"
        "- /menu — открыть главное меню боссов\n"
        "- /add_admin [id] — назначение админа (только владелец бота)\n"
        "- Главное меню показывает всех боссов, чей клан в очереди и время воскрешения\n"
        "- Нажав на босса, <b>админ</b> может выбрать клан, который убил босса\n"
        "- 💀 Уведомление о убийстве босса рассылается всем пользователям\n"
        "- 🔔 За 10 минут до воскрешения приходит предупреждение с очередью клана\n"
        "- ⚔️ Уведомление о том, что босс снова доступен для убийства\n"
        "- Кнопка 'Обновить 🔄' — обновление главного меню\n"
        "Админы могут отмечать убийства босса в меню.")
    await update.effective_chat.send_message(text, parse_mode="HTML")


    # send persistent start -> menu button
async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user:
        return
        # показываем меню боссов
    await update.message.reply_text("Меню:",
                                    reply_markup=build_menu_keyboard(),
                                    parse_mode="HTML")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Start ▶️", callback_data="first_start")],
    ])
    await update.message.reply_text(text,
                                    reply_markup=keyboard,
                                    parse_mode="HTML")


async def add_admin_handler(update: Update,
                            context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user is None:
        return
    if user.id != OWNER_ID:
        await update.message.reply_text(
            "❌ Только владелец бота может назначать админов.")
        return
    args = context.args
    if not args:
        await update.message.reply_text(
            "Использование: /add_admin <telegram_id>")
        return
    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("ID должен быть числом.")
        return
    set_admin(tid)
    await update.message.reply_text(f"✅ Пользователь {tid} назначен админом.")


async def callback_query_handler(update: Update,
                                 context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    data = query.data or ""
    await query.answer()
    application = context.application

    parts = data.split("|")
    key = parts[0]

    # ---------------- Start / Refresh menu ----------------
    if key in ("first_start", "menu_refresh"):
        text = "Меню:\u200b"
        try:
            await query.message.edit_text(text,
                                          reply_markup=build_menu_keyboard(),
                                          parse_mode="HTML")
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise
        return

    # ---------------- Boss view ----------------
    if key == "boss_view" and len(parts) >= 2:
        boss_name = parts[1]
        keyboard = build_boss_choice_keyboard(boss_name)
        text = f"Босс: <b>{boss_name}</b>\nВыберите клан убивший босса:\u200b"
        try:
            await query.message.edit_text(text,
                                          reply_markup=keyboard,
                                          parse_mode="HTML")
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise
        return

    # ---------------- Boss other ----------------
    if key == "boss_other" and len(parts) >= 2:
        boss_name = parts[1]
        info = get_boss_info(boss_name)
        if not info:
            return
        last_killer = info["last_killer"]
        hours = BOSSES.get(boss_name)
        if hours is None:
            return
        respawn_ts = int((datetime.now() + timedelta(hours=hours)).timestamp())
        set_boss_killer_and_respawn(boss_name, last_killer, respawn_ts)
        if boss_name in boss_tasks:
            task = boss_tasks[boss_name]
            if not task.done():
                task.cancel()
        boss_tasks[boss_name] = asyncio.create_task(
            boss_respawn_task(application, boss_name, respawn_ts))
        try:
            await query.message.edit_text("Меню:\u200b",
                                          reply_markup=build_menu_keyboard(),
                                          parse_mode="HTML")
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise
        return

    # ---------------- Boss setup ----------------
    if key == "boss_setup" and len(parts) >= 2:
        boss_name = parts[1]
        user = query.from_user
        if not user or not is_admin(user.id):
            await query.answer("❌ Только админы могут настраивать таймеры.",
                               show_alert=True)
            return

        keyboard_buttons = [[
            InlineKeyboardButton(
                clan, callback_data=f"boss_setup_clan|{boss_name}|{clan}")
        ] for clan in CLANS]
        keyboard_buttons.append([
            InlineKeyboardButton(
                "Назад ◀️",
                callback_data=f"menu_back|{int(datetime.now().timestamp())}")
        ])
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        try:
            await query.message.edit_text(
                f"Выберите клан, забравший лут для {boss_name}:\u200b",
                reply_markup=keyboard)
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise
        return

    # ---------------- Boss setup clan (custom timer input) ----------------
    if key == "boss_setup_clan" and len(parts) >= 3:
        boss_name, clan = parts[1], parts[2]
        # сохраняем message_id текущего меню
        awaiting_custom_timer[boss_name] = {
            "clan": clan,
            "awaiting_minutes": True,
            "message_id": query.message.message_id,
            "chat_id": query.message.chat_id
        }
        # редактируем меню на инструкцию
        await query.message.edit_text(
            f"Введите количество минут до респавна для {boss_name} (клан {clan}):"
        )
        return

    # ---------------- Record boss kill ----------------
    if key == "boss_kill" and len(parts) >= 3:
        boss_name, clan = parts[1], parts[2]
        user = query.from_user
        if not user or not is_admin(user.id):
            await query.answer("❌ Только админы могут отмечать убийство.",
                               show_alert=True)
            return

        hours = BOSSES.get(boss_name)
        if hours is None:
            await query.message.reply_text("Ошибка: неизвестный босс.")
            return

        respawn_ts = int((datetime.now() + timedelta(hours=hours)).timestamp())
        set_boss_killer_and_respawn(boss_name, clan, respawn_ts)
        if boss_name in boss_tasks:
            task = boss_tasks[boss_name]
            if not task.done():
                task.cancel()
        boss_tasks[boss_name] = asyncio.create_task(
            boss_respawn_task(application, boss_name, respawn_ts))

        emoji_kill = "💀"
        emoji_time = "⏰"
        text = f"{emoji_kill} <b>{boss_name}</b> убит кланом <b>{clan}</b>.\n{emoji_time} Следующее воскрешение - {format_datetime_ts(respawn_ts)}"
        await broadcast_message(application, text)
        try:
            await query.message.edit_text("Меню:\u200b",
                                          reply_markup=build_menu_keyboard(),
                                          parse_mode="HTML")
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise
        return

    # ---------------- Back to menu ----------------
    if key == "menu_back":
        try:
            await query.message.edit_text("Меню боссов:\u200b",
                                          reply_markup=build_menu_keyboard(),
                                          parse_mode="HTML")
        except Exception as e:
            if "Message is not modified" not in str(e):
                raise
        return

    # ---------------- Help ----------------
    if key == "help":
        help_text = (
            "Инструкция:\n"
            "- /start — регистрация и меню боссов\n"
            "- /menu — открыть главное меню боссов\n"
            "- /add_admin [id] — назначение админа (только владелец бота)\n"
            "- Главное меню показывает всех боссов, чей клан в очереди и время воскрешения\n"
            "- Нажав на босса, <b>админ</b> может выбрать клан, который убил босса\n"
            "- 💀 Уведомление о убийстве босса рассылается всем пользователям\n"
            "- 🔔 За 10 минут до воскрешения приходит предупреждение с очередью клана\n"
            "- ⚔️ Уведомление о том, что босс снова доступен для убийства\n"
            "- Кнопка 'Обновить 🔄' — обновление главного меню")
        await query.message.reply_text(help_text, parse_mode="HTML")
        return


async def custom_timer_input_handler(update: Update,
                                     context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Нужно ввести число минут.")
        return
    minutes = int(text)

    for boss_name, data in list(awaiting_custom_timer.items()):
        if data.get("awaiting_minutes"):
            clan = data["clan"]
            respawn_ts = int(
                (datetime.now() + timedelta(minutes=minutes)).timestamp())

            # обновляем БД
            set_boss_killer_and_respawn(boss_name, clan, respawn_ts)

            # отменяем старую задачу
            if boss_name in boss_tasks:
                task = boss_tasks[boss_name]
                if not task.done():
                    task.cancel()

            # создаём новую задачу
            task = asyncio.create_task(
                boss_respawn_task(context.application, boss_name, respawn_ts))
            boss_tasks[boss_name] = task

            # удаляем старое меню (редактируем сообщение, где был выбор клана)
            try:
                msg_id = data.get("message_id")
                if msg_id:
                    await context.bot.edit_message_text(
                        chat_id=update.effective_chat.id,
                        message_id=msg_id,
                        text="✅ Таймер установлен.")
            except Exception:
                pass

            # удаляем элемент ожидания
            del awaiting_custom_timer[boss_name]

            # показываем главное меню
            await update.effective_chat.send_message(
                "Главное меню:", reply_markup=build_menu_keyboard())
            break


# ---------------- Background task for respawn reminders ----------------
async def boss_respawn_task(application, boss_name: str, respawn_ts: int):
    """
    Wait until (respawn_ts - 10 minutes) -> send 10-min warning
    Then wait until respawn -> notify resurrected
    """
    try:
        now_ts = int(datetime.now().timestamp())
        warn_ts = respawn_ts - 10 * 60  # 10 minutes before
        if warn_ts > now_ts:
            await asyncio.sleep(warn_ts - now_ts)

            # 10-минутное уведомление
            info = get_boss_info(boss_name)
            last_killer = info["last_killer"] if info else None

            queue_clan = None
            if last_killer and last_killer in CLANS:
                queue_clan = [c for c in CLANS if c != last_killer][0]

            emoji_alarm = "🔔"
            text = f"{emoji_alarm} {boss_name}, воскреснет через 10 минут."
            if queue_clan:
                text += f"\nОчередь клана - {queue_clan}."
            await broadcast_message(application, text)

        # ждать точное время воскресения
        now_ts = int(datetime.now().timestamp())
        if respawn_ts > now_ts:
            await asyncio.sleep(respawn_ts - now_ts)

        # уведомление о воскресении
        emoji_revive = "⚔️"
        text = f"{emoji_revive} {boss_name} теперь снова доступен для убийства!"
        await broadcast_message(application, text)

        # очищаем только respawn_end_ts, сохраняем last_killer
        info = get_boss_info(boss_name)
        set_boss_killer_and_respawn(boss_name, info["last_killer"], None)

    except asyncio.CancelledError:
        return
    except Exception:
        return


async def custom_timer_input_handler(update: Update,
                                     context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Нужно ввести число минут.")
        return
    minutes = int(text)

    for boss_name, data in list(awaiting_custom_timer.items()):
        if data.get("awaiting_minutes"):
            clan = data["clan"]
            respawn_ts = int(
                (datetime.now() + timedelta(minutes=minutes)).timestamp())

            # обновляем БД
            set_boss_killer_and_respawn(boss_name, clan, respawn_ts)

            # отменяем старую задачу
            if boss_name in boss_tasks:
                task = boss_tasks[boss_name]
                if not task.done():
                    task.cancel()

            # создаём новую задачу
            task = asyncio.create_task(
                boss_respawn_task(context.application, boss_name, respawn_ts))
            boss_tasks[boss_name] = task

            # редактируем сообщение меню, чтобы убрать его
            try:
                chat_id = data["chat_id"]
                message_id = data["message_id"]
                await context.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=
                    f"✅ Таймер для {boss_name} установлен на {minutes} минут.")
            except Exception:
                pass

            # удаляем из ожидания
            del awaiting_custom_timer[boss_name]

            # отправляем главное меню
            await update.effective_chat.send_message(
                "Главное меню:", reply_markup=build_menu_keyboard())
            break


# ---------------- Application setup ----------------
async def on_startup(application):
    # пересоздаем все активные таймеры
    await restore_boss_tasks(application)
    # опционально можно расслать меню всем
    await broadcast_message(application,
                            "Меню боссов восстановлено после перезапуска")


async def restore_boss_tasks(application):
    now_ts = int(datetime.now().timestamp())
    for name, hours, last_killer, respawn_end_ts in get_all_bosses():
        if respawn_end_ts and respawn_end_ts > now_ts:
            # создаем background task
            task = asyncio.create_task(
                boss_respawn_task(application, name, respawn_end_ts))
            boss_tasks[name] = task


async def set_commands(application):
    commands = [
        BotCommand("start", "Регистрация и меню"),
        BotCommand("add_admin", "Добавить админа (только владелец)"),
        BotCommand("help", "Инструкция"),
        BotCommand("menu", "Меню боссов")
    ]
    await application.bot.set_my_commands(commands)
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands())
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonCommands())


def main():
    if not TOKEN:
        print("ERROR: TELEGRAM_TOKEN env var not set.")
        return

    app = ApplicationBuilder().token(TOKEN).post_init(set_commands).build()

    # Commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("menu", menu_handler))
    app.add_handler(CommandHandler("add_admin", add_admin_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.
            COMMAND,  # ловим все текстовые сообщения, которые не команды
            custom_timer_input_handler  # твой обработчик
        ))
    # CallbackQuery handler for buttons
    app.add_handler(CallbackQueryHandler(callback_query_handler))

    print("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()

