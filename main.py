import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from supabase import create_client, Client
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
from fastapi import FastAPI, Request, HTTPException
import uvicorn
from dotenv import load_dotenv
import os
from fastapi.middleware.cors import CORSMiddleware

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Загрузка переменных из .env
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg")
API_KEY = os.getenv("API_KEY", "snapisecretcodez117799")

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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

# Инициализация FastAPI
app = FastAPI()

# Настройка CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175", "http://207dd17b7bb2.vps.myjino.ru"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Эндпоинт для получения токена
@app.get("/api/bot-token")
async def get_bot_token(request: Request):
    client_api_key = request.headers.get("x-api-key")
    if client_api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Forbidden")
    return {"botToken": BOT_TOKEN}

# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="🎁 Создать розыгрыш", callback_data="create_giveaway")],
        [types.InlineKeyboardButton(text="📋 Мои розыгрыши", callback_data="created_giveaways")],
        [types.InlineKeyboardButton(text="🔥 Активные розыгрыши", callback_data="active_giveaways")],
        [types.InlineKeyboardButton(text="🎯 Мои участия", callback_data="my_participations")],
    ])
    await send_message_with_image(bot, message.chat.id, "<tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji> Добро пожаловать! Выберите действие:", reply_markup=keyboard)

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        help_text = (
            "<b><tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Как создать розыгрыш</b>\n"
            "<blockquote expandable>Первое, что вам нужно сделать, — это нажать в главном меню кнопку «🎁 Создать розыгрыш». После этого вам потребуется пошагово ввести:\n"
            "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Название розыгрыша\n"
            "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Описание\n"
            "<tg-emoji emoji-id='5381879959335738545'>3️⃣</tg-emoji> Медиафайл (если он необходим)\n"
            "<tg-emoji emoji-id='5382054253403577563'>4️⃣</tg-emoji> Дату завершения\n"
            "<tg-emoji emoji-id='5391197405553107640'>5️⃣</tg-emoji> Количество победителей</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Как опубликовать созданный розыгрыш</b>\n"
            "<blockquote expandable>Чтобы опубликовать розыгрыш, сначала привяжите каналы или группы. Для этого:\n"
            "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Перейдите в ваш созданный розыгрыш\n"
            "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Нажмите кнопку «Привязать сообщества»\n"
            "<tg-emoji emoji-id='5381879959335738545'>3️⃣</tg-emoji> Нажмите «➕ Новый паблик»\n"
            "<tg-emoji emoji-id='5382054253403577563'>4️⃣</tg-emoji> Добавьте бота в ваш канал или группу с правами администратора\n"
            "<tg-emoji emoji-id='5391197405553107640'>5️⃣</tg-emoji> Бот уведомит вас о успешной привязке ✅\n"
            "После привязки сообщества в разделе созданного розыгрыша нажмите кнопку «📢 Опубликовать розыгрыш» и выберите привязанные сообщества, в которых хотите разместить розыгрыш.</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> Дополнительные функции</b>\n"
            "<blockquote expandable>В созданном розыгрыше вы можете:\n"
            "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Редактировать название, описание, медиафайл и количество победителей\n"
            "<tg-emoji emoji-id='5443038326535759644'>💬</tg-emoji> Изменить сообщение для победителей\n"
            "<tg-emoji emoji-id='5397916757333654639'>➕</tg-emoji> Добавить задание «Пригласить друга» в условия участия</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5447410659077661506'>🌐</tg-emoji> Что можно делать, когда розыгрыш опубликован</b>\n"
            "<blockquote expandable>В главном меню перейдите в раздел «🔥 Активные розыгрыши», выберите нужный розыгрыш. В нем вы можете:\n"
            "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Полностью редактировать розыгрыш (все изменения отразятся в опубликованных постах)\n"
            "<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Принудительно завершить розыгрыш</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Что будет, когда розыгрыш завершится</b>\n"
            "<blockquote expandable>После окончания времени розыгрыша бот автоматически:\n"
            "<tg-emoji emoji-id='5436386989857320953'>🤑</tg-emoji> Рандомно определит победителей\n"
            "<tg-emoji emoji-id='5451882707875276247'>🕯</tg-emoji> Опубликует в привязанных сообществах пост о завершении с указанием победителей и кнопкой «Результаты» (при нажатии пользователи увидят график участия)\n"
            "<tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji> Отправит победителям поздравительное сообщение, заданное вами ранее</blockquote>"
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
    except Exception as e:
        logging.error(f"Ошибка в cmd_help: {e}")
        await message.reply("Произошла ошибка при выполнении команды /help.")

# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def back_to_main_menu(callback_query: CallbackQuery, state: FSMContext):
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

async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        await asyncio.sleep(60)  # Проверка каждую минуту

# Добавляем функцию для запуска FastAPI
async def run_fastapi():
    config = uvicorn.Config(app, host="0.0.0.0", port=49534, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

# Главная функция запуска бота
async def main():
    check_task = asyncio.create_task(check_and_end_giveaways(bot, supabase))
    username_check_task = asyncio.create_task(periodic_username_check())
    fastapi_task = asyncio.create_task(run_fastapi())  # Добавляем запуск FastAPI

    try:
        await dp.start_polling(bot)
    finally:
        check_task.cancel()
        username_check_task.cancel()
        fastapi_task.cancel()

if __name__ == '__main__':
    asyncio.run(main())
