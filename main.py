import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from supabase import create_client, Client

from utils import send_message_with_image, check_and_end_giveaways, check_usernames
from active_giveaways import register_active_giveaways_handlers
from create_giveaway import register_create_giveaway_handlers
from created_giveaways import register_created_giveaways_handlers
from my_participations import register_my_participations_handlers
from congratulations_messages import register_congratulations_messages

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

# Обработчики команд
@dp.message(Command("pay"))
async def cmd_pay(message: types.Message):
    await bot.send_invoice(
        chat_id=message.chat.id,
        title="Участие в розыгрыше",
        description="Оплата за участие в розыгрыше",
        payload="{}",
        provider_token="",  # Оставьте пустым для Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(label="Участие в розыгрыше", amount=1)],  # 100 Stars
        start_parameter="giveaway_participation"
    )

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(lambda message: message.successful_payment is not None)
async def process_successful_payment(message: types.Message):
    if message.from_user:
        user_id = message.from_user.id
        payment_amount = message.successful_payment.total_amount / 100
        payment_currency = message.successful_payment.currency
        payment_status = 'paid'

        try:
            response = supabase.table('users').upsert({
                'user_id': user_id,
                'payment_status': payment_status,
                'payment_amount': payment_amount,
                'payment_currency': payment_currency,
                'subscription_status': 'active'
            }).execute()

            if response.data:
                logging.info(f"Payment data recorded for user {user_id}")
            else:
                logging.error(f"Failed to record payment data for user {user_id}")

        except Exception as e:
            logging.error(f"Error recording payment data: {str(e)}")

        paid_users[user_id] = message.successful_payment.telegram_payment_charge_id
        await message.answer("Спасибо за оплату! Вы успешно зарегистрированы для участия в розыгрыше.")
    logging.info(f"Successful payment: {message.successful_payment}")

@dp.message(Command("status"))
async def cmd_status(message: types.Message):
    if message.from_user:
        user_id = message.from_user.id
        try:
            response = supabase.table('users').select('payment_status').eq('user_id', user_id).single().execute()
            if response.data:
                payment_status = response.data['payment_status']
                if payment_status == 'paid':
                    await message.reply("Вы уже оплатили участие в розыгрыше.")
                elif payment_status == 'refunded':
                    await message.reply("Ваш платеж был возвращен.")
                else:
                    await message.reply("Вы еще не оплатили участие в розыгрыше.")
            else:
                await message.reply("Вы еще не оплатили участие в розыгрыше.")
        except Exception as e:
            logging.error(f"Error checking payment status: {str(e)}")
            await message.reply("Произошла ошибка при проверке статуса оплаты. Пожалуйста, попробуйте позже.")
    else:
        await message.reply("Не удалось определить пользователя.")

@dp.message(Command("refund"))
async def cmd_refund(message: types.Message):
    if message.from_user and message.from_user.id in paid_users:
        user_id = message.from_user.id
        charge_id = paid_users[user_id]
        try:
            await bot.refund_star_payment(user_id, charge_id)

            response = supabase.table('users').update({
                'payment_status': 'refunded',
                'payment_amount': 0,
                'subscription_status': 'inactive'
            }).eq('user_id', user_id).execute()

            if response.data:
                logging.info(f"Payment status updated to refunded for user {user_id}")
                del paid_users[user_id]
                await message.reply(
                    "Возврат средств выполнен успешно. Статус оплаты и подписки обновлен в базе данных.")
            else:
                logging.error(f"Failed to update payment status for user {user_id}")
                await message.reply("Возврат средств выполнен, но не удалось обновить статус в базе данных.")

        except Exception as e:
            logging.error(f"Refund failed: {str(e)}")
            await message.reply("Не удалось выполнить возврат средств. Пожалуйста, попробуйте позже.")
    else:
        await message.reply("У вас нет активных платежей для возврата.")

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Создать розыгрыш", callback_data="create_giveaway")],
        [types.InlineKeyboardButton(text="Созданные розыгрыши", callback_data="created_giveaways")],
        [types.InlineKeyboardButton(text="Активные розыгрыши", callback_data="active_giveaways")],
        [types.InlineKeyboardButton(text="Мои участия", callback_data="my_participations")],
    ])
    await send_message_with_image(bot, message.chat.id, "Выберите действие:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def process_back_to_main_menu(callback_query: types.CallbackQuery):
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text="Создать розыгрыш", callback_data="create_giveaway")],
        [types.InlineKeyboardButton(text="Созданные розыгрыши", callback_data="created_giveaways")],
        [types.InlineKeyboardButton(text="Активные розыгрыши", callback_data="active_giveaways")],
        [types.InlineKeyboardButton(text="Мои участия", callback_data="my_participations")],
    ])
    await send_message_with_image(bot, callback_query.from_user.id, "Выберите действие:",
                                  reply_markup=keyboard, message_id=callback_query.message.message_id)


async def periodic_username_check():
    while True:
        await check_usernames(bot, supabase)
        await asyncio.sleep(600)  # Проверка каждые 10 мин

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
