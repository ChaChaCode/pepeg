import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
import psycopg2
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image, check_and_end_giveaways, check_usernames
from active_giveaways import register_active_giveaways_handlers
from create_giveaway import register_create_giveaway_handlers
from created_giveaways import register_created_giveaways_handlers
from my_participations import register_my_participations_handlers
from congratulations_messages import register_congratulations_messages
from congratulations_messages_active import register_congratulations_messages_active
from new_public import register_new_public
from aiogram.fsm.context import FSMContext

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
BOT_TOKEN = '7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Конфигурация PostgreSQL
db_config = {
    "host": "195.200.26.251",
    "port": 5432,
    "database": "mydatabase",
    "user": "app_user",
    "password": "moxy1337"
}

# Подключение к PostgreSQL
try:
    conn = psycopg2.connect(**db_config)
    logging.info("Успешно подключились к PostgreSQL!")
except Exception as e:
    logging.error(f"Ошибка подключения к PostgreSQL: {e}")
    raise

# Создай курсор для выполнения запросов
cursor = conn.cursor()

# Переменные для хранения данных
user_selected_communities = {}
paid_users = {}

# Регистрация обработчиков из других модулей
register_active_giveaways_handlers(dp, bot, conn, cursor)
register_create_giveaway_handlers(dp, bot, conn, cursor)
register_created_giveaways_handlers(dp, bot, conn, cursor)
register_my_participations_handlers(dp, bot, conn, cursor)
register_congratulations_messages(dp, bot, conn, cursor)
register_congratulations_messages_active(dp, bot, conn, cursor)
register_new_public(dp, bot, conn, cursor)

# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    # Проверяем наличие любых розыгрышей пользователя
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s
        """,
        (user_id,)
    )
    has_any_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие активных розыгрышей, созданных пользователем
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_active = 'true'
        """,
        (user_id,)
    )
    has_active_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие участий в активных розыгрышах
    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM participations p
        JOIN giveaways g ON p.giveaway_id = g.id
        WHERE p.user_id = %s AND g.is_active = 'true'
        """,
        (user_id,)
    )
    has_active_participations = cursor.fetchone()[0] > 0
    logging.info(f"User {user_id} - has_active_participations: {has_active_participations}")

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")

    # Добавляем "Мои розыгрыши", если есть любые розыгрыши
    if has_any_giveaways:
        keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")

    # Добавляем "Мои участия", только если пользователь участвует в активных розыгрышах
    if has_active_participations:
        keyboard.button(text="🎯 Мои участия", callback_data="my_participations")

    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        message.chat.id,
        "<tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji> Добро пожаловать! Выберите действие:",
        reply_markup=keyboard.as_markup()
    )

# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def back_to_main_menu(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await bot.answer_callback_query(callback_query.id)

    user_id = callback_query.from_user.id

    # Проверяем наличие любых розыгрышей пользователя
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s
        """,
        (user_id,)
    )
    has_any_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие активных розыгрышей, созданных пользователем
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_active = 'true'
        """,
        (user_id,)
    )
    has_active_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие участий в активных розыгрышах
    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM participations p
        JOIN giveaways g ON p.giveaway_id = g.id
        WHERE p.user_id = %s AND g.is_active = 'true'
        """,
        (user_id,)
    )
    has_active_participations = cursor.fetchone()[0] > 0
    logging.info(f"User {user_id} - has_active_participations: {has_active_participations}")

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")

    # Добавляем "Мои розыгрыши", если есть любые розыгрыши
    if has_any_giveaways:
        keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")

    # Добавляем "Мои участия", только если пользователь участвует в активных розыгрышах
    if has_active_participations:
        keyboard.button(text="🎯 Мои участия", callback_data="my_participations")

    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.message.chat.id,
        "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите действие:",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )

# Остальной код остаётся без изменений
async def periodic_username_check():
    while True:
        await check_usernames(bot, conn, cursor)
        await asyncio.sleep(60)

async def main():
    check_task = asyncio.create_task(check_and_end_giveaways(bot, conn, cursor))
    username_check_task = asyncio.create_task(periodic_username_check())

    try:
        await dp.start_polling(bot)
    finally:
        check_task.cancel()
        username_check_task.cancel()
        cursor.close()
        conn.close()
        logging.info("Соединение с PostgreSQL закрыто.")

if __name__ == '__main__':
    asyncio.run(main())
