import os
import logging
import sqlite3
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

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
    c.execute('''CREATE TABLE IF NOT EXISTS lotteries
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  price INTEGER NOT NULL,
                  total_tickets INTEGER NOT NULL,
                  sold_tickets INTEGER DEFAULT 0,
                  status TEXT DEFAULT 'active')''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  lottery_id INTEGER NOT NULL,
                  number INTEGER NOT NULL,
                  user_id INTEGER NOT NULL,
                  status TEXT DEFAULT 'free',
                  FOREIGN KEY(lottery_id) REFERENCES lotteries(id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (user_id INTEGER PRIMARY KEY,
                  phone TEXT)''')
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def is_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Проверяет, является ли пользователь администратором группы"""
    chat_id = update.effective_chat.id
    user_id = update.effective_user.id
    try:
        chat_member = context.bot.get_chat_member(chat_id, user_id)
        return chat_member.status in ['administrator', 'creator']
    except:
        return False

# --- Команды бота ---

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

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки"""
    query = update.callback_query
    await query.answer()
    data = query.data

    # --- Участвовать ---
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

    # --- Выбор розыгрыша ---
    elif data.startswith('select_lottery_'):
        lottery_id = int(data.split('_')[2])
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, price FROM lotteries WHERE id=?", (lottery_id,))
        lottery = c.fetchone()
        c.execute("SELECT number FROM tickets WHERE lottery_id=? AND status='free'", (lottery_id,))
        free = [row[0] for row in c.fetchall()]
        conn.close()
        if not free:
            await query.edit_message_text("😔 Все номера в этом розыгрыше проданы!")
            return
        keyboard = []
        row = []
        for num in free[:50]:
            row.append(InlineKeyboardButton(str(num), callback_data=f'select_ticket_{lottery_id}_{num}'))
            if len(row) == 10:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("✓ Подтвердить выбор", callback_data=f'confirm_tickets_{lottery_id}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='participate')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"🎫 Розыгрыш: {lottery[0]}\nЦена номерка: {lottery[1]}₽\nВыберите номера (нажимайте, чтобы выбрать):", reply_markup=reply_markup)

    # --- Выбор билета (временное хранение в context.user_data) ---
    elif data.startswith('select_ticket_'):
        parts = data.split('_')
        lottery_id = int(parts[2])
        number = int(parts[3])
        if 'selected_tickets' not in context.user_data:
            context.user_data['selected_tickets'] = []
        if number not in context.user_data['selected_tickets']:
            context.user_data['selected_tickets'].append(number)
            await query.edit_message_text(f"✅ Номер {number} добавлен.\nВсего выбрано: {len(context.user_data['selected_tickets'])}")
        else:
            context.user_data['selected_tickets'].remove(number)
            await query.edit_message_text(f"❌ Номер {number} убран.\nВсего выбрано: {len(context.user_data['selected_tickets'])}")

    # --- Подтверждение покупки ---
    elif data.startswith('confirm_tickets_'):
        lottery_id = int(data.split('_')[2])
        if 'selected_tickets' not in context.user_data or not context.user_data['selected_tickets']:
            await query.edit_message_text("Вы не выбрали ни одного номера!")
            return
        numbers = context.user_data['selected_tickets']
        user_id = update.effective_user.id
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        placeholders = ','.join(['?'] * len(numbers))
        c.execute(f"SELECT number FROM tickets WHERE lottery_id=? AND number IN ({placeholders}) AND status='free'", (lottery_id, *numbers))
        free_numbers = [row[0] for row in c.fetchall()]
        if len(free_numbers) != len(numbers):
            occupied = [n for n in numbers if n not in free_numbers]
            await query.edit_message_text(f"❌ Номера {occupied} уже заняты! Выберите другие.")
            conn.close()
            return
        for num in numbers:
            c.execute("UPDATE tickets SET status='sold', user_id=? WHERE lottery_id=? AND number=?", (user_id, lottery_id, num))
        c.execute("UPDATE lotteries SET sold_tickets = sold_tickets + ? WHERE id=?", (len(numbers), lottery_id))
        conn.commit()
        conn.close()
        context.user_data['selected_tickets'] = []
        await query.edit_message_text(f"✅ Вы успешно купили номера: {numbers}\nСпасибо за участие!")

    # --- Админ панель ---
    elif data == 'admin_panel':
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        keyboard = [
            [InlineKeyboardButton("➕ Создать розыгрыш", callback_data='create_lottery')],
            [InlineKeyboardButton("📋 Мои розыгрыши", callback_data='my_lotteries')],
            [InlineKeyboardButton("← Назад", callback_data='back_to_main')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("⚙️ Админ панель:", reply_markup=reply_markup)

    # --- Создать розыгрыш ---
    elif data == 'create_lottery':
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        await query.edit_message_text("Введите название розыгрыша:")
        context.user_data['admin_action'] = 'create_lottery_name'

    # --- Мои розыгрыши ---
    elif data == 'my_lotteries':
        if not is_admin(update, context):
            await query.edit_message_text("⛔ У вас нет прав администратора.")
            return
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT id, name, price, total_tickets, sold_tickets, status FROM lotteries")
        lotteries = c.fetchall()
        conn.close()
        if not lotteries:
            await query.edit_message_text("У вас нет созданных розыгрышей.")
            return
        keyboard = []
        for l in lotteries:
            remaining = l[3] - l[4]
            status_emoji = "🟢" if l[5] == 'active' else "🔴"
            keyboard.append([InlineKeyboardButton(f"{status_emoji} {l[1]} (осталось {remaining}/{l[3]}, {l[2]}₽/номер)", callback_data=f'lott_info_{l[0]}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='admin_panel')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("📋 Ваши розыгрыши:", reply_markup=reply_markup)

    # --- Информация о розыгрыше (для админа) ---
    elif data.startswith('lott_info_'):
        lottery_id = int(data.split('_')[2])
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT name, price, total_tickets, sold_tickets, status FROM lotteries WHERE id=?", (lottery_id,))
        lottery = c.fetchone()
        conn.close()
        remaining = lottery[2] - lottery[3]
        keyboard = []
        if lottery[4] == 'active':
            keyboard.append([InlineKeyboardButton("🎲 Провести розыгрыш", callback_data=f'draw_lottery_{lottery_id}')])
        keyboard.append([InlineKeyboardButton("← Назад", callback_data='my_lotteries')])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"📊 Розыгрыш: {lottery[0]}\n"
            f"Цена номерка: {lottery[1]}₽\n"
            f"Всего номерков: {lottery[2]}\n"
            f"Продано: {lottery[3]}\n"
            f"Осталось: {remaining}\n"
            f"Статус: {'Активен' if lottery[4] == 'active' else 'Завершен'}",
            reply_markup=reply_markup
        )

    # --- Провести розыгрыш ---
    elif data.startswith('draw_lottery_'):
        lottery_id = int(data.split('_')[2])
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
        c.execute("UPDATE lotteries SET status='completed' WHERE id=?", (lottery_id,))
        conn.commit()
        conn.close()
        try:
            await context.bot.send_message(
                update.effective_chat.id,
                f"🎉 ПОБЕДИТЕЛЬ РОЗЫГРЫША '{lottery_name}'!\n"
                f"Номер: {winning_number}\n"
                f"Поздравляем пользователя! (ID: {winner_id})"
            )
        except:
            pass
        await query.edit_message_text(f"✅ Розыгрыш '{lottery_name}' проведен!\nПобедитель: номер {winning_number} (ID: {winner_id})")

    # --- Назад ---
    elif data == 'back_to_main':
        await start(update, context)

    # --- Мои билеты ---
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

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений (для создания розыгрыша)"""
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
                c.execute("INSERT INTO lotteries (name, price, total_tickets) VALUES (?, ?, ?)", (name, price, total))
                lottery_id = c.lastrowid
                for i in range(1, total+1):
                    c.execute("INSERT INTO tickets (lottery_id, number, user_id, status) VALUES (?, ?, ?, 'free')", (lottery_id, i, 0))
                conn.commit()
                conn.close()
                await update.message.reply_text(f"✅ Розыгрыш '{name}' создан!\nЦена: {price}₽\nВсего номерков: {total}")
                del context.user_data['admin_action']
                del context.user_data['new_lottery_name']
                del context.user_data['new_lottery_price']
            except:
                await update.message.reply_text("Пожалуйста, введите целое положительное число.")
    else:
        await update.message.reply_text("Используйте кнопки меню. Нажмите /start")

async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Извините, я не понимаю эту команду. Нажмите /start")

# --- Запуск бота ---
def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    app.add_handler(MessageHandler(filters.COMMAND, unknown))
    logger.info("Бот запущен и готов к работе")
    app.run_polling()

if __name__ == "__main__":
    main()
