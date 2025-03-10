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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from hypercorn.config import Config
from hypercorn.asyncio import serve
import signal

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
BOT_TOKEN = '7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Конфигурация Supabase
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

user_selected_communities = {}
paid_users = {}

# Регистрация обработчиков
register_active_giveaways_handlers(dp, bot, supabase)
register_create_giveaway_handlers(dp, bot, supabase)
register_created_giveaways_handlers(dp, bot, supabase)
register_my_participations_handlers(dp, bot, supabase)
register_congratulations_messages(dp, bot, supabase)
register_congratulations_messages_active(dp, bot, supabase)
register_new_public(dp, bot, supabase)

# Инициализация FastAPI
app = FastAPI()

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Модель для запроса проверки подписки
class SubscriptionRequest(BaseModel):
    chat_id: int
    user_id: int

# Эндпоинты FastAPI
@app.post("/api/check-subscription")
async def check_subscription(request: SubscriptionRequest):
    try:
        chat_member = await bot.get_chat_member(chat_id=request.chat_id, user_id=request.user_id)
        is_subscribed = chat_member.status in ["creator", "administrator", "member"]
        return {"isSubscribed": is_subscribed, "error": None}
    except Exception as e:
        logging.error(f"Ошибка при проверке подписки: {e}")
        return {"isSubscribed": False, "error": str(e)}

@app.get("/api/get-invite-link/{chat_id}")
async def get_invite_link(chat_id: int):
    logging.info(f"Получен запрос для chat_id: {chat_id}")
    try:
        chat = await bot.get_chat(chat_id)
        logging.info(f"Chat info: {chat}")
        if chat.invite_link:
            logging.info(f"Возвращаем существующую ссылку: {chat.invite_link}")
            return {"inviteLink": chat.invite_link, "error": None}
        else:
            invite_link = await bot.export_chat_invite_link(chat_id)
            logging.info(f"Сгенерирована новая ссылка: {invite_link}")
            return {"inviteLink": invite_link, "error": None}
    except Exception as e:
        logging.error(f"Ошибка в get_invite_link: {e}")
        if "403" in str(e) or "400" in str(e):
            return {"inviteLink": None, "error": "Бот не имеет прав администратора или чат не существует"}
        return {"inviteLink": None, "error": str(e)}

# Обработчики команд
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
            # ... (остальной текст опущен для краткости, добавьте его обратно при необходимости)
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

# Периодическая проверка имен пользователей
async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        await asyncio.sleep(60)

# Главная функция
async def main():
    # Удаление webhook
    webhook_info = await bot.get_webhook_info()
    logging.info(f"Текущая информация о webhook: {webhook_info}")
    if webhook_info.url:
        await bot.delete_webhook(drop_pending_updates=True)
        logging.info("Webhook удален")

    # Создание задач
    check_task = asyncio.create_task(check_and_end_giveaways(bot, supabase))
    username_check_task = asyncio.create_task(periodic_username_check())
    polling_task = asyncio.create_task(dp.start_polling(bot))

    # Настройка Hypercorn
    config = Config()
    config.bind = ["0.0.0.0:8000"]
    api_task = asyncio.create_task(serve(app, config))

    # Обработка сигналов для graceful shutdown
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def signal_handler():
        logging.info("Получен сигнал завершения, инициируем shutdown...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    try:
        # Ожидаем выполнения задач или сигнала завершения
        await asyncio.gather(polling_task, api_task, return_exceptions=True)
        await stop_event.wait()  # Ждем сигнала завершения
    except asyncio.CancelledError:
        logging.info("Программа завершена через CancelledError")
    finally:
        # Корректное завершение всех задач
        logging.info("Завершаем все задачи...")
        check_task.cancel()
        username_check_task.cancel()
        polling_task.cancel()
        api_task.cancel()

        # Ожидаем завершения задач
        await asyncio.gather(
            check_task, username_check_task, polling_task, api_task,
            return_exceptions=True
        )
        await bot.session.close()  # Закрываем сессию бота
        logging.info("Все задачи завершены, сессия бота закрыта")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Программа остановлена пользователем")
    except Exception as e:
        logging.error(f"Ошибка при запуске: {e}")
