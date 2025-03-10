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
    allow_origins=["http://localhost:5175", "http://207dd17b7bb2.vps.myjino.ru"],  # Разрешите ваш локальный сервер и сам себя
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

# (Остальные обработчики, такие как /help и back_to_main_menu, оставьте как есть)

async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        await asyncio.sleep(60)

async def run_fastapi():
    config = uvicorn.Config(app, host="0.0.0.0", port=49534, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def main():
    check_task = asyncio.create_task(check_and_end_giveaways(bot, supabase))
    username_check_task = asyncio.create_task(periodic_username_check())
    fastapi_task = asyncio.create_task(run_fastapi())

    try:
        await dp.start_polling(bot)
    finally:
        check_task.cancel()
        username_check_task.cancel()
        fastapi_task.cancel()

if __name__ == '__main__':
    asyncio.run(main())
