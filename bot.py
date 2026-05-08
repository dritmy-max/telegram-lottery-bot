import os
import logging
import sqlite3
import random
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# --- Flask для Render ---
from flask import Flask
import threading

app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return "Бот работает!"

def run_web():
    app_flask.run(host='0.0.0.0', port=8000)

# --- Настройка логирования ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Токен бота ---
TOKEN = "8718532267:AAEq7afHk_Nuqjy3KeqI52KdzanQLQ1_iEI"

# --- База данных SQLite ---
DB_FILE = "lottery.db"

def init_db():
    """Создает таблицы, если их нет"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Таблица розыгрышей
    c.execute('''CREATE TABLE IF NOT EXISTS lotteries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  price INTEGER NOT NULL,
                  total_tickets INTEGER NOT NULL,
                  sold_tickets INTEGER DEFAULT 0,
                  status TEXT DEFAULT 'active',
                  created_at TEXT)''')
    # Таблица билетов
    c.execute('''CREATE TABLE IF NOT EXISTS tickets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  lottery_id INTEGER NOT NULL,
                  number INTEGER NOT NULL,
                  user_id INTEGER NOT NULL,
                  status TEXT DEFAULT 'free',
                  FOREIGN KEY(lottery_id) REFERENCES lotteries(id))''')
    # Таблица покупок
    c.execute('''CREATE TABLE IF NOT EXISTS purchases
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  lottery_id INTEGER NOT NULL,
                  user_id INTEGER NOT NULL,
                  numbers TEXT NOT NULL,
                  purchased_at TEXT)''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Жёсткая проверка администратора по вашему ID"""
    YOUR_ID = 982485177
    return update.effective_user.id == YOUR_ID

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Главное меню"""
    keyboard = [
        [InlineKeyboardButton("🎫 Участвовать", callback_data='participate')],
        [InlineKeyboardButton("👤 Мои билеты", callback_data='my_tickets')]
    ]
    if is_admin(update, context):
        keyboard.append([InlineKeyboardButton("⚙️ Админ панель", callback_data='admin_panel')])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Добро пожаловать в мясную лотерею!\nВыберите действие:", reply_markup=reply_markup)

async def lottery(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда для открытия меню лотереи"""
    await start(update, context)

async def set_commands(application):
    """Устанавливает команды в меню Telegram"""
    commands = [
        BotCommand("start", "Главное меню"),
        BotCommand("lottery", "🎰 Лотерея")
    ]
    await application.bot.set_my_commands(commands)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'participate':
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id, name, price, total_tickets, sold_tickets FROM lotteries WHERE status='active'")
        lotteries = c.fetchall()
        conn.close()
        if not lotteries:
            await query.edit_message_text("Сейчас нет активных розыгрышей. Загляните позже!")
            return
        keyboard = []
        for l in lotteries:
            remaining = l[3] - l[4]
            keyboard.append([InlineKeyboardButton(f"{l[1]} (осталось {remaining}/{l[3]}, {l[2]}₽/номер)", callback_data=f'select_lottery_{l[0]}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='back_to_main')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("Выберите розыгрыш:", reply_markup=reply_markup)

    elif data.startswith('select_lottery_'):
        lottery_id = int(data.split('_')[2])
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, price FROM lotteries WHERE id=?", (lottery_id,))
        lottery = c.fetchone()
        c.execute("SELECT number FROM tickets WHERE lottery_id=? AND status='free'", (lottery_id,))
        free_numbers = [row[0] for row in c.fetchall()]
        conn.close()
        if not free_numbers:
            await query.edit_message_text("😔 Все номера в этом розыгрыше проданы!")
            return
        # Клавиатура с номерами и кнопкой подтверждения
        keyboard = []
        row = []
        for num in free_numbers[:50]:
            row.append(InlineKeyboardButton(str(num), callback_data=f'select_ticket_{lottery_id}_{num}'))
            if len(row) == 10:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("✅ Подтвердить покупку", callback_data=f'confirm_{lottery_id}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='participate')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"🎫 Розыгрыш: {lottery[0]}\nЦена номерка: {lottery[1]}₽\n"
            f"Корзина: {context.user_data.get('cart', [])}\n"
            f"Выберите номера (нажимайте — они будут добавляться в корзину):",
            reply_markup=reply_markup
        )

    elif data.startswith('select_ticket_'):
        parts = data.split('_')
        lottery_id = int(parts[2])
        number = int(parts[3])
        # Работаем с корзиной
        if 'cart' not in context.user_data:
            context.user_data['cart'] = []
        if number in context.user_data['cart']:
            context.user_data['cart'].remove(number)
        else:
            context.user_data['cart'].append(number)
        # Обновляем то же сообщение
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, price FROM lotteries WHERE id=?", (lottery_id,))
        lottery = c.fetchone()
        c.execute("SELECT number FROM tickets WHERE lottery_id=? AND status='free'", (lottery_id,))
        free_numbers = [row[0] for row in c.fetchall()]
        conn.close()
        keyboard = []
        row = []
        for num in free_numbers[:50]:
            row.append(InlineKeyboardButton(str(num), callback_data=f'select_ticket_{lottery_id}_{num}'))
            if len(row) == 10:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("✅ Подтвердить покупку", callback_data=f'confirm_{lottery_id}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='participate')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"🎫 Розыгрыш: {lottery[0]}\nЦена номерка: {lottery[1]}₽\n"
            f"Корзина: {context.user_data['cart']}\n"
            f"Выберите номера (нажимайте — они будут добавляться в корзину):",
            reply_markup=reply_markup
        )

    elif data.startswith('confirm_'):
        lottery_id = int(data.split('_')[1])
        if 'cart' not in context.user_data or not context.user_data['cart']:
            await query.edit_message_text("Корзина пуста. Выберите хотя бы один номер.")
            return
        numbers = context.user_data['cart']
        user_id = update.effective_user.id
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        # Проверяем, что все номера ещё свободны
        placeholders = ','.join(['?'] * len(numbers))
        c.execute(f"SELECT number FROM tickets WHERE lottery_id=? AND number IN ({placeholders}) AND status='free'", (lottery_id, *numbers))
        free_numbers = [row[0] for row in c.fetchall()]
        if len(free_numbers) != len(numbers):
            occupied = [n for n in numbers if n not in free_numbers]
            await query.edit_message_text(f"❌ Номера {occupied} уже заняты! Обновите страницу и выберите другие.")
            conn.close()
            context.user_data['cart'] = []
            return
        # Покупаем номера
        for num in numbers:
            c.execute("UPDATE tickets SET status='sold', user_id=? WHERE lottery_id=? AND number=?", (user_id, lottery_id, num))
        c.execute("UPDATE lotteries SET sold_tickets = sold_tickets + ? WHERE id=?", (len(numbers), lottery_id))
        # Записываем покупку
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT INTO purchases (lottery_id, user_id, numbers, purchased_at) VALUES (?, ?, ?, ?)",
                  (lottery_id, user_id, ','.join(map(str, numbers)), now))
        conn.commit()
        conn.close()
        context.user_data['cart'] = []
        await query.edit_message_text(f"✅ Вы успешно купили номера: {numbers}\nСпасибо за участие!")

    elif data == 'my_tickets':
        user_id = update.effective_user.id
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT lottery_id, number FROM tickets WHERE user_id=? AND status='sold'", (user_id,))
        tickets = c.fetchall()
        conn.close()
        if not tickets:
            await query.edit_message_text("У вас пока нет билетов.")
            return
        text = "🎫 Ваши билеты:\n"
        for t in tickets:
            text += f"Розыгрыш ID {t[0]}, номер {t[1]}\n"
        await query.edit_message_text(text)

    elif data == 'admin_panel':
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        keyboard = [
            [InlineKeyboardButton("➕ Создать розыгрыш", callback_data='create_lottery')],
            [InlineKeyboardButton("📋 Все розыгрыши", callback_data='all_lotteries')],
            [InlineKeyboardButton("← Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚙️ Админ панель:", reply_markup=reply_markup)

    elif data == 'create_lottery':
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        await query.edit_message_text("Введите название розыгрыша:")
        context.user_data['admin_action'] = 'create_lottery_name'

    elif data == 'all_lotteries':
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id, name, price, total_tickets, sold_tickets, status, created_at FROM lotteries")
        lotteries = c.fetchall()
        conn.close()
        if not lotteries:
            await query.edit_message_text("Нет созданных розыгрышей.")
            return
        keyboard = []
        for l in lotteries:
            remaining = l[3] - l[4]
            status_emoji = "🟢" if l[5] == 'active' else "🟠" if l[5] == 'archived' else "🔴"
            keyboard.append([InlineKeyboardButton(
                f"{status_emoji} {l[1]} (осталось {remaining}/{l[3]}, {l[2]}₽/номер) | {l[6]}",
                callback_data=f'lottery_info_{l[0]}'
            )])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='admin_panel')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📋 Все розыгрыши:", reply_markup=reply_markup)

    elif data.startswith('lottery_info_'):
        lottery_id = int(data.split('_')[2])
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, price, total_tickets, sold_tickets, status, created_at FROM lotteries WHERE id=?", (lottery_id,))
        lottery = c.fetchone()
        conn.close()
        remaining = lottery[2] - lottery[3]
        text = f"📊 Розыгрыш: {lottery[0]}\nЦена: {lottery[1]}₽\nВсего: {lottery[2]}\nПродано: {lottery[3]}\nОсталось: {remaining}\nСтатус: {lottery[4]}\nСоздан: {lottery[5]}"
        keyboard = []
        if lottery[4] == 'active':
            keyboard.append([InlineKeyboardButton("🎲 Провести розыгрыш", callback_data=f'draw_{lottery_id}')])
            keyboard.append([InlineKeyboardButton("❌ Закрыть (не состоялся)", callback_data=f'archive_{lottery_id}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='all_lotteries')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(text, reply_markup=reply_markup)

    elif data.startswith('draw_'):
        lottery_id = int(data.split('_')[1])
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name FROM lotteries WHERE id=?", (lottery_id,))
        lottery_name = c.fetchone()[0]
        c.execute("SELECT number, user_id FROM tickets WHERE lottery_id=? AND status='sold'", (lottery_id,))
        sold = c.fetchall()
        if not sold:
            await query.edit_message_text("😔 Нет проданных номерков в этом розыгрыше.")
            conn.close()
            return
        winner = random.choice(sold)
        winning_number = winner[0]
        winner_id = winner[1]
        # Получаем username победителя
        try:
            winner_chat = await context.bot.get_chat(winner_id)
            winner_username = winner_chat.username
            if winner_username:
                winner_mention = f"@{winner_username}"
            else:
                winner_mention = f"ID: {winner_id}"
        except:
            winner_mention = f"ID: {winner_id}"
        c.execute("UPDATE lotteries SET status='completed' WHERE id=?", (lottery_id,))
        conn.commit()
        conn.close()
        await context.bot.send_message(
            update.effective_chat.id,
            f"🎉 ПОБЕДИТЕЛЬ РОЗЫГРЫША '{lottery_name}'!\nНомер: {winning_number}\nПоздравляем пользователя: {winner_mention}"
        )
        await query.edit_message_text(f"✅ Розыгрыш '{lottery_name}' проведен!\nПобедитель: номер {winning_number}\nПользователь: {winner_mention}")

    elif data.startswith('archive_'):
        lottery_id = int(data.split('_')[1])
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("UPDATE lotteries SET status='archived' WHERE id=?", (lottery_id,))
        conn.commit()
        conn.close()
        await query.edit_message_text(f"✅ Розыгрыш закрыт (архивирован).")

    elif data == 'back_to_main':
        await start(update, context)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений (для создания розыгрыша)"""
    if not update.message:
        return
    if 'admin_action' in context.user_data:
        action = context.user_data['admin_action']
        text = update.message.text
        if action == 'create_lottery_name':
            context.user_data['new_lottery_name'] = text
            context.user_data['admin_action'] = 'create_lottery_price'
            await update.message.reply_text("Введите цену одного номерка (в рублях):")
        elif action == 'create_lottery_price':
            try:
                price = int(text)
                if price <= 0:
                    raise ValueError
                context.user_data['new_lottery_price'] = price
                context.user_data['admin_action'] = 'create_lottery_total'
                await update.message.reply_text("Введите общее количество номерков:")
            except:
                await update.message.reply_text("Пожалуйста, введите целое положительное число.")
        elif action == 'create_lottery_total':
            try:
                total = int(text)
                if total <= 0:
                    raise ValueError
                name = context.user_data['new_lottery_name']
                price = context.user_data['new_lottery_price']
                conn = sqlite3.connect(DB_FILE)
                c = conn.cursor()
                now = datetime.now().strftime("%Y-%m-%d %H:%M")
                c.execute("INSERT INTO lotteries (name, price, total_tickets, created_at) VALUES (?, ?, ?, ?)",
                          (name, price, total, now))
                lottery_id = c.lastrowid
                for i in range(1, total+1):
                    c.execute("INSERT INTO tickets (lottery_id, number, user_id, status) VALUES (?, ?, ?, 'free')",
                              (lottery_id, i, 0))
                conn.commit()
                conn.close()
                await update.message.reply_text(
                    f"✅ Розыгрыш '{name}' создан!\nЦена: {price}₽\nВсего номерков: {total}\nДата создания: {now}"
                )
                del context.user_data['admin_action']
                del context.user_data['new_lottery_name']
                del context.user_data['new_lottery_price']
            except:
                await update.message.reply_text("Пожалуйста, введите целое положительное число.")
    else:
        # Не отвечаем на обычные сообщения
        pass

# --- Запуск бота ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("lottery", lottery))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.post_init = set_commands
    threading.Thread(target=run_web, daemon=True).start()
    logger.info("Бот запущен и готов к работе")
    app.run_polling()

if __name__ == "__main__":
    main()
