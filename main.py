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
from new_public import register_new_public
from aiogram.fsm.context import FSMContext

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Инициализация бота и диспетчера
BOT_TOKEN = '7924714999:AAFUbKWC--s-ff2DKe6g5Sk1C2Z7yl7hh0c'
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
register_new_public(dp, bot, supabase)


# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Создать розыгрыш", callback_data="create_giveaway")],
        [types.InlineKeyboardButton(text="Созданные розыгрыши", callback_data="created_giveaways")],
        [types.InlineKeyboardButton(text="Активные розыгрыши", callback_data="active_giveaways")],
        [types.InlineKeyboardButton(text="Мои участия", callback_data="my_participations")],
    ])
    await send_message_with_image(bot, message.chat.id, "Выберите действие:", reply_markup=keyboard)


# Обработчик команды /help
@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = (
        "<b>Как создать розыгрыш</b>\n"
        "<blockquote expandable>Первое, что вам нужно сделать, — это нажать в главном меню кнопку «Создать розыгрыш». После этого вам потребуется пошагово ввести:\n"
        "1. Название розыгрыша\n"
        "2. Описание\n"
        "3. Медиафайл (если он необходим)\n"
        "4. Дату завершения\n"
        "5. Количество победителей</blockquote>\n\n"

        "<b>Как опубликовать созданный розыгрыш</b>\n"
        "<blockquote expandable>Чтобы опубликовать розыгрыш, сначала привяжите каналы или группы. Для этого:\n"
        "1. Перейдите в ваш созданный розыгрыш\n"
        "2. Нажмите кнопку «Привязать сообщества»\n"
        "3. Нажмите «+ Новый паблик»\n"
        "4. Добавьте бота в ваш канал или группу с правами администратора\n"
        "5. Бот уведомит вас о успешной привязке\n"
        "После привязки сообщества в разделе созданного розыгрыша нажмите кнопку «Опубликовать розыгрыш» и выберите привязанные сообщества, в которых хотите разместить розыгрыш.</blockquote>\n\n"

        "<b>Дополнительные функции</b>\n"
        "<blockquote expandable>В созданном розыгрыше вы можете:\n"
        "- Редактировать название, описание, медиафайл и количество победителей\n"
        "- Изменить сообщение для победителей\n"
        "- Добавить задание «Пригласить друга» в условия участия</blockquote>\n\n"

        "<b>Что можно делать, когда розыгрыш опубликован</b>\n"
        "<blockquote expandable>В главном меню перейдите в раздел «Активные розыгрыши», выберите нужный розыгрыш. В нем вы можете:\n"
        "- Полностью редактировать розыгрыш (все изменения отразятся в опубликованных постах)\n"
        "- Принудительно завершить розыгрыш</blockquote>\n\n"

        "<b>Что будет, когда розыгрыш завершится</b>\n"
        "<blockquote expandable>После окончания времени розыгрыша бот автоматически:\n"
        "- Рандомно определит победителей\n"
        "- Опубликует в привязанных сообществах пост о завершении с указанием победителей и кнопкой «Результаты» (при нажатии пользователи увидят график участия)\n"
        "- Отправит победителям сообщение, заданное вами ранее</blockquote>"
    )

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="В меню", callback_data="back_to_main_menu")
    keyboard.adjust(1)

    await bot.send_message(
        chat_id=message.chat.id,
        text=help_text,
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )


# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def back_to_main_menu(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await bot.answer_callback_query(callback_query.id)

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="Созданные розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="Мои участия", callback_data="my_participations")
    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.message.chat.id,
        "Выберите действие:",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )


async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        await asyncio.sleep(60)  # Проверка каждую минуту


# Главная функция запуска бота
async def main():
    check_task = asyncio.create_task(check_and_end_giveaways(bot, supabase))
    username_check_task = asyncio.create_task(periodic_username_check())

    try:
        await dp.start_polling(bot)
    finally:
        check_task.cancel()
        username_check_task.cancel()


if __name__ == '__main__':
    asyncio.run(main())
