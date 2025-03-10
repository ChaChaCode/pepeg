import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from fastapi import FastAPI, Request, HTTPException
from supabase import create_client, Client
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

# Инициализация бота
BOT_TOKEN = '7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot=bot, storage=storage)

# Инициализация FastAPI
app = FastAPI()

# Конфигурация Supabase
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

user_selected_communities = {}
paid_users = {}

# Регистрация обработчиков из других модулей
register_active_giveaways_handlers(dp, bot, supabase)
register_create_giveaway_handlers(dp, bot, supabase)
register_created_giveaways_handlers(dp, bot, supabase)
register_my_participations_handlers(dp, bot, supabase)
register_congratulations_messages(dp, bot, supabase)
register_congratulations_messages_active(dp, bot, supabase)
register_new_public(dp, bot, supabase)

# Функция проверки подписки
async def check_subscription(chat_id: str, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        status = member.status in ['member', 'administrator', 'creator']
        print(f"Проверка подписки: user_id={user_id}, chat_id={chat_id}, результат={status}")
        return status
    except Exception as e:
        logging.error(f"Ошибка проверки подписки: {e}")
        print(f"Ошибка при проверке подписки для user_id={user_id}, chat_id={chat_id}: {e}")
        return False

# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    print(f"Получена команда /start от user_id={message.from_user.id}, chat_id={message.chat.id}")
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎁 Создать розыгрыш", callback_data="create_giveaway")],
        [types.InlineKeyboardButton(text="📋 Мои розыгрыши", callback_data="created_giveaways")],
        [types.InlineKeyboardButton(text="🔥 Активные розыгрыши", callback_data="active_giveaways")],
        [types.InlineKeyboardButton(text="🎯 Мои участия", callback_data="my_participations")],
    ])
    await send_message_with_image(
        bot, message.chat.id, "<tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji> Добро пожаловать! Выберите действие:",
        reply_markup=keyboard
    )
    print(f"Отправлено приветственное сообщение в chat_id={message.chat.id}")

# Обработчик команды /help
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    print(f"Получена команда /help от user_id={message.from_user.id}, chat_id={message.chat.id}")
    try:
        help_text = (
            "<b><tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Как создать розыгрыш</b>\n"
            "<blockquote expandable>Первое, что вам нужно сделать, — это нажать в главном меню кнопку «🎁 Создать розыгрыш». После этого вам потребуется пошагово ввести:\n"
            "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Название розыгрыша\n"
            "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Описание\n"
            "<tg-emoji emoji-id='5381879959335738545'>3️⃣</tg-emoji> Медиафайл (если он необходим)\n"
            "<tg-emoji emoji-id='5382054253403577563'>4️⃣</tg-emoji> Дату завершения\n"
            "<tg-emoji emoji-id='5391197405553107640'>5️⃣</tg-emoji> Количество победителей</blockquote>\n\n"
            # (для краткости опущен остальной текст)
        )
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")
        keyboard.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
        keyboard.adjust(1)
        await bot.send_message(
            chat_id=message.chat.id,
            text=help_text,
            parse_mode="HTML",
            reply_markup=keyboard.as_markup()
        )
        print(f"Отправлен текст помощи в chat_id={message.chat.id}")
    except Exception as e:
        logging.error(f"Ошибка в cmd_help: {e}")
        print(f"Ошибка при отправке помощи в chat_id={message.chat.id}: {e}")
        await message.reply("Произошла ошибка при выполнении команды /help.")

# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def back_to_main_menu(callback_query: CallbackQuery, state: FSMContext):
    print(f"Получен callback back_to_main_menu от user_id={callback_query.from_user.id}")
    await state.clear()
    await bot.answer_callback_query(callback_query.id)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="🔥 Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="🎯 Мои участия", callback_data="my_participations")
    keyboard.adjust(1)
    await send_message_with_image(
        bot,
        callback_query.message.chat.id,
        "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите действие:",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )
    print(f"Отправлено главное меню в chat_id={callback_query.message.chat.id}")

# API-эндпоинт для проверки подписки через curl
@app.post("/check_subscription")
async def check_subscription_endpoint(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    chat_id = data.get("chat_id")
    if not user_id or not chat_id:
        raise HTTPException(status_code=400, detail="Требуются User ID и Chat ID")
    is_subscribed = await check_subscription(chat_id, int(user_id))
    print(f"API запрос /check_subscription: user_id={user_id}, chat_id={chat_id}, результат={is_subscribed}")
    return {"is_subscribed": is_subscribed}

# Периодическая проверка usernames
async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        print("Проверка usernames выполнена")
        await asyncio.sleep(60)  # Проверка каждую минуту

# Главная функция
async def main():
    # Запуск периодических задач
    check_task = asyncio.create_task(check_and_end_giveaways(bot, supabase))
    username_check_task = asyncio.create_task(periodic_username_check())

    # Запуск FastAPI в отдельной задаче
    import uvicorn
    config = uvicorn.Config(app=app, host="0.0.0.0", port=8000, log_level="info")
    server = uvicorn.Server(config)
    fastapi_task = asyncio.create_task(server.serve())

    try:
        print("Бот запускается через polling...")
        logging.info("Бот запущен!")
        await dp.start_polling(bot)  # Запуск бота через polling
    finally:
        check_task.cancel()
        username_check_task.cancel()
        fastapi_task.cancel()
        print("Бот остановлен.")
        logging.info("Бот остановлен.")

if __name__ == "__main__":
    asyncio.run(main())  # Запуск асинхронных задач
