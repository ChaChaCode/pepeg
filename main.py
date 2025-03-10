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
from aiohttp import web

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
BOT_TOKEN = "7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg"  # Фиксированный токен бота
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Конфигурация Supabase
SUPABASE_URL = "https://olbnxtiigxqcpailyecq.supabase.co"  # Фиксированный URL Supabase
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY"  # Фиксированный ключ Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

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

# Обработчик команды /help
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

# Функция проверки initData (для безопасности API)
import hmac
import hashlib
from urllib.parse import parse_qs

def verify_telegram_init_data(init_data: str, bot_token: str) -> bool:
    """
    Проверяет подлинность initData от Telegram Web Apps.
    """
    parsed_data = parse_qs(init_data)
    if "hash" not in parsed_data:
        return False
    
    data_check_string = "\n".join(f"{key}={parsed_data[key][0]}" for key in sorted(parsed_data.keys()) if key != "hash")
    secret_key = hmac.new("WebAppData".encode(), bot_token.encode(), hashlib.sha256).digest()
    calculated_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    return calculated_hash == parsed_data["hash"][0]

# API для фронтенда
async def check_subscription(request):
    data = await request.json()
    channel_id = data.get("channelId")
    user_id = data.get("userId")
    init_data = data.get("initData")

    if not channel_id or not user_id or not init_data:
        return web.json_response({"error": "channelId, userId, and initData are required"}, status=400)

    if not verify_telegram_init_data(init_data, BOT_TOKEN):
        return web.json_response({"error": "Invalid initData"}, status=403)

    try:
        chat_member = await bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        is_subscribed = chat_member.status in ["creator", "administrator", "member"]
        return web.json_response({"isSubscribed": is_subscribed, "error": None})
    except Exception as e:
        logging.error(f"Ошибка при проверке подписки: {e}")
        return web.json_response({"isSubscribed": False, "error": str(e)}, status=500)

async def get_invite_link(request):
    data = await request.json()
    channel_id = data.get("channelId")
    init_data = data.get("initData")

    if not channel_id or not init_data:
        return web.json_response({"error": "channelId and initData are required"}, status=400)

    if not verify_telegram_init_data(init_data, BOT_TOKEN):
        return web.json_response({"error": "Invalid initData"}, status=403)

    try:
        chat = await bot.get_chat(chat_id=channel_id)
        if hasattr(chat, "invite_link") and chat.invite_link:
            return web.json_response({"inviteLink": chat.invite_link, "error": None})

        invite_link = await bot.export_chat_invite_link(chat_id=channel_id)
        return web.json_response({"inviteLink": invite_link, "error": None})
    except Exception as e:
        logging.error(f"Ошибка при получении инвайт-ссылки: {e}")
        return web.json_response({"inviteLink": None, "error": str(e)}, status=500)

# Обработчик Webhook
async def handle_webhook(request):
    update = types.Update(**(await request.json()))
    await dp.feed_update(bot, update)
    return web.Response()

# Настройка веб-сервера
app = web.Application()
app.router.add_post("/api/check-subscription", check_subscription)
app.router.add_post("/api/get-invite-link", get_invite_link)
app.router.add_post("/webhook", handle_webhook)  # Добавляем маршрут для Webhook

async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        await asyncio.sleep(60)

async def set_webhook():
    webhook_url = "https://vite-react-raffle.vercel.app/webhook"  # Убедитесь, что путь соответствует маршруту
    await bot.set_webhook(webhook_url)
    logging.info(f"Webhook установлен на {webhook_url}")

async def on_startup():
    await set_webhook()
    asyncio.create_task(check_and_end_giveaways(bot, supabase))
    asyncio.create_task(periodic_username_check())

async def main():
    # Запускаем задачи и веб-сервер
    app.on_startup.append(lambda _: on_startup())
    await web.run_app(app, host="0.0.0.0", port=5000)  # Запускаем только веб-сервер

if __name__ == "__main__":
    asyncio.run(main())
