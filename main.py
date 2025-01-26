from typing import List, Dict, Any
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, PreCheckoutQuery
from aiogram.filters import Command
from datetime import datetime, timedelta
import pytz
from supabase import create_client, Client
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image
from aiogram.enums import ChatMemberStatus
import aiogram.exceptions

# Configure logging
logging.basicConfig(level=logging.INFO)

# Initialize bot and dispatcher
BOT_TOKEN = '7924714999:AAFUbKWC--s-ff2DKe6g5Sk1C2Z7yl7hh0c'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

user_selected_communities = {}
paid_users: Dict[int, str] = {}


# States
class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_media_choice = State()
    waiting_for_media_upload = State()
    waiting_for_end_time = State()
    waiting_for_winner_count = State()
    waiting_for_community_name = State()
    waiting_for_new_end_time = State()
    waiting_for_media_edit = State()


# Helper functions
async def edit_or_send_message(chat_id: int, text: str, message_id: int = None, reply_markup=None):
    await send_message_with_image(bot, chat_id, text, reply_markup, message_id)


async def save_giveaway(user_id: int, name: str, description: str, end_time: str, winner_count: int,
                        media_type: str = None, media_file_id: str = None):
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = moscow_tz.localize(datetime.strptime(end_time, "%d.%m.%Y %H:%M") + timedelta(hours=3))

    giveaway_data = {
        'user_id': user_id,
        'name': name,
        'description': description,
        'end_time': end_time_dt.isoformat(),
        'winner_count': winner_count,
        'is_active': False,
        'media_type': media_type,
        'media_file_id': media_file_id
    }

    try:
        response = supabase.table('giveaways').insert(giveaway_data).execute()
        if response.data:
            logging.info(f"Giveaway saved successfully: {response.data}")
            return True
        else:
            logging.error(f"Unexpected response format: {response}")
            return False
    except Exception as e:
        logging.error(f"Error saving giveaway: {str(e)}")
        return False


async def check_ended_giveaways():
    while True:
        now = datetime.now(pytz.utc)
        try:
            response = supabase.table('giveaways').select('*').eq('is_active', True).execute()
            if response.data:
                for giveaway in response.data:
                    end_time = datetime.fromisoformat(giveaway['end_time'])
                    if end_time <= now:
                        await end_giveaway(giveaway['id'])
            else:
                logging.error(f"Unexpected response format: {response}")
        except Exception as e:
            logging.error(f"Error fetching active giveaways: {str(e)}")

        await asyncio.sleep(30)  # Check every 30 seconds


async def end_giveaway(giveaway_id: str):
    # Fetch giveaway details
    response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
    if not response.data:
        logging.error(f"Error fetching giveaway: Giveaway not found")
        return
    giveaway = response.data

    # Fetch participants
    response = supabase.table('participations').select('user_id').eq('giveaway_id', giveaway_id).execute()
    participants = response.data if response.data else []

    # Recheck participants
    valid_participants = await recheck_participants(giveaway_id, participants)

    # Select winners from valid participants
    winners = await select_random_winners(valid_participants, min(len(valid_participants), giveaway['winner_count']))

    # Update giveaway status
    supabase.table('giveaways').update({'is_active': False}).eq('id', giveaway_id).execute()

    # Save winners (if any)
    if winners:
        for winner in winners:
            supabase.table('giveaway_winners').insert({
                'giveaway_id': giveaway_id,
                'user_id': winner['user_id'],
                'username': winner['username']
            }).execute()

    # Notify winners and publish results
    await notify_winners_and_publish_results(giveaway, winners)


async def recheck_participants(giveaway_id: str, participants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid_participants = []
    giveaway_communities = await get_giveaway_communities(giveaway_id)

    for participant in participants:
        user_id = participant['user_id']
        is_valid = True

        for community in giveaway_communities:
            try:
                member = await bot.get_chat_member(chat_id=community['community_id'], user_id=user_id)
                if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR,
                                         ChatMemberStatus.CREATOR]:
                    is_valid = False
                    break
            except Exception as e:
                logging.error(
                    f"Error checking membership for user {user_id} in community {community['community_id']}: {str(e)}")
                is_valid = False
                break

        if is_valid:
            valid_participants.append(participant)
        else:
            # Remove invalid participant
            supabase.table('participations').delete().eq('giveaway_id', giveaway_id).eq('user_id', user_id).execute()

    return valid_participants


async def notify_winners_and_publish_results(giveaway, winners):
    response = supabase.table('giveaway_communities').select('community_id').eq('giveaway_id', giveaway['id']).execute()
    if not response.data:
        logging.error(f"Error fetching communities: No communities found")
        return
    communities = response.data

    if winners:
        winners_list = ', '.join([f"@{w['username']}" for w in winners])
        result_message = f"""
🎉 Розыгрыш завершен! 🎉

{giveaway['name']}

Победители: {winners_list}

Поздравляем победителей!
        """
    else:
        result_message = f"""
🎉 Розыгрыш завершен! 🎉

{giveaway['name']}

К сожалению, в этом розыгрыше не было участников.
        """

    if winners and len(winners) < giveaway['winner_count']:
        result_message += f"\n\nВнимание: Количество участников ({len(winners)}) было меньше, чем количество призовых мест ({giveaway['winner_count']}). Не все призовые места были распределены."

    # Create the inline keyboard with the "Результаты" button
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Результаты", url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway['id']}")

    for community in communities:
        try:
            if giveaway['media_type'] and giveaway['media_file_id']:
                if giveaway['media_type'] == 'photo':
                    await bot.send_photo(
                        chat_id=int(community['community_id']),
                        photo=giveaway['media_file_id'],
                        caption=result_message,
                        reply_markup=keyboard.as_markup()
                    )
                elif giveaway['media_type'] == 'gif':
                    await bot.send_animation(
                        chat_id=int(community['community_id']),
                        animation=giveaway['media_file_id'],
                        caption=result_message,
                        reply_markup=keyboard.as_markup()
                    )
                elif giveaway['media_type'] == 'video':
                    await bot.send_video(
                        chat_id=int(community['community_id']),
                        video=giveaway['media_file_id'],
                        caption=result_message,
                        reply_markup=keyboard.as_markup()
                    )
            else:
                await bot.send_message(
                    chat_id=int(community['community_id']),
                    text=result_message,
                    reply_markup=keyboard.as_markup()
                )
        except Exception as e:
            logging.error(f"Error publishing results in community @{community['community_id']}: {e}")

    for winner in winners:
        try:
            await bot.send_message(chat_id=winner['user_id'],
                                   text=f"Поздравляем! Вы выиграли в розыгрыше \"{giveaway['name']}\"!")
        except Exception as e:
            logging.error(f"Error notifying winner {winner['user_id']}: {e}")


print("Functions updated successfully!")


async def select_random_winners(participants, winner_count):
    import random
    winners = random.sample(participants, min(winner_count, len(participants)))
    winner_details = []
    for winner in winners:
        try:
            user = await bot.get_chat_member(winner['user_id'], winner['user_id'])
            winner_details.append({
                'user_id': winner['user_id'],
                'username': user.user.username or f"user{winner['user_id']}"
            })
        except Exception as e:
            logging.error(f"Error fetching user details: {e}")
            winner_details.append({
                'user_id': winner['user_id'],
                'username': f"user{winner['user_id']}"
            })
    return winner_details


async def get_giveaway_communities(giveaway_id: str):
    response = supabase.table('giveaway_communities').select('community_id, community_username').eq('giveaway_id',
                                                                                                    giveaway_id).execute()
    return response.data


# Command handlers
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
        payment_amount = message.successful_payment.total_amount / 100  # Convert from cents to currency units
        payment_currency = message.successful_payment.currency
        payment_status = 'paid'

        # Record payment data in the database
        try:
            response = supabase.table('users').upsert({
                'user_id': user_id,
                'payment_status': payment_status,
                'payment_amount': payment_amount,
                'payment_currency': payment_currency,
                'subscription_status': 'active'  # Set subscription status to active
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

            # Update payment status in the database
            response = supabase.table('users').update({
                'payment_status': 'refunded',
                'payment_amount': 0,  # Set payment amount to 0 after refund
                'subscription_status': 'inactive'  # Set subscription status to inactive
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
async def cmd_start(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="Созданные розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="Мои участия", callback_data="my_participations")
    keyboard.button(text="Смотреть все розыгрыши", callback_data="view_all_giveaways")
    keyboard.adjust(1)  # One button per row

    sent_message = await send_message_with_image(bot, message.chat.id, "Выберите действие:", keyboard.as_markup())
    if sent_message is None:
        logging.error("Не удалось отправить сообщение с изображением.")
        return
    await state.update_data(last_message_id=sent_message.message_id)


@dp.callback_query(lambda c: c.data == 'view_all_giveaways')
async def process_view_all_giveaways(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id

    # Check if the user has an active subscription
    subscription_status = await check_subscription_status(user_id)

    if subscription_status:
        # User has an active subscription, show all giveaways
        await show_all_active_giveaways(callback_query)
    else:
        # User doesn't have an active subscription, offer to purchase
        await offer_subscription(callback_query)


async def check_subscription_status(user_id: int) -> bool:
    try:
        response = supabase.table('users').select('subscription_status').eq('user_id', user_id).single().execute()
        if response.data and response.data.get('subscription_status') == 'active':
            return True
    except Exception as e:
        logging.error(f"Error checking subscription status: {str(e)}")
    return False


async def offer_subscription(callback_query: types.CallbackQuery):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Купить подписку", callback_data="buy_subscription")
    keyboard.button(text="Назад", callback_data="back_to_main_menu")
    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Чтобы смотреть все активные розыгрыши, необходимо приобрести подписку за 1 звезду.",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )


@dp.callback_query(lambda c: c.data == 'buy_subscription')
async def process_buy_subscription(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id)
    await bot.send_invoice(
        chat_id=callback_query.from_user.id,
        title="Подписка на все розыгрыши",
        description="Доступ ко всем активным розыгрышам",
        payload="subscription_purchase",
        provider_token="",  # Оставьте пустым для Telegram Stars
        currency="XTR",
        prices=[LabeledPrice(label="Подписка", amount=1)],  # 1 Star
        start_parameter="subscription_purchase"
    )


@dp.message(
    lambda message: message.successful_payment is not None and message.successful_payment.invoice_payload == "subscription_purchase")
async def process_successful_subscription(message: types.Message):
    if message.from_user:
        user_id = message.from_user.id
        try:
            response = supabase.table('users').update({
                'subscription_status': 'active',
                'subscription_start_date': datetime.now().isoformat()
            }).eq('user_id', user_id).execute()

            if response.data:
                logging.info(f"Subscription activated for user {user_id}")
                await message.answer(
                    "Спасибо за покупку подписки! Теперь у вас есть доступ ко всем активным розыгрышам.")
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="Смотреть все розыгрыши", callback_data="view_all_giveaways")
            else:
                logging.error(f"Failed to activate subscription for user {user_id}")
                await message.answer("Произошла ошибка при активации подписки. Пожалуйста, обратитесь в поддержку.")

        except Exception as e:
            logging.error(f"Error activating subscription: {str(e)}")
            await message.answer("Произошла ошибка при активации подписки. Пожалуйста, обратитесь в поддержку.")


async def show_all_active_giveaways(callback_query: types.CallbackQuery):
    try:
        response = supabase.table('giveaways').select('*').eq('is_active', True).order('end_time').execute()
        giveaways = response.data
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        if not giveaways:
            await send_message_with_image(
                bot,
                callback_query.message.chat.id,
                "В данный момент нет активных розыгрышей.",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
            return

        keyboard = InlineKeyboardBuilder()
        user_id = callback_query.from_user.id
        for giveaway in giveaways:
            # Check if the user is participating in this giveaway
            participation_response = supabase.table('participations').select('*').eq('giveaway_id', giveaway['id']).eq(
                'user_id', user_id).execute()
            is_participating = len(participation_response.data) > 0

            button_text = f"{giveaway['name']}{'✅ ' if is_participating else ''}"
            keyboard.button(text=button_text, callback_data=f"view_giveaway:{giveaway['id']}")
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        await send_message_with_image(
            bot,
            callback_query.message.chat.id,
            "Список всех активных розыгрышей:",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )
    except Exception as e:
        logging.error(f"Error fetching all active giveaways: {str(e)}")
        await send_message_with_image(
            bot,
            callback_query.message.chat.id,
            "Произошла ошибка при получении списка активных розыгрышей. Пожалуйста, попробуйте позже.",
            message_id=callback_query.message.message_id
        )


@dp.callback_query(lambda c: c.data.startswith('view_giveaway:'))
async def process_view_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    try:
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = response.data

        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        giveaway_info = f"""
Название: {giveaway['name']}
Описание: {giveaway['description']}
Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК
Количество победителей: {giveaway['winner_count']}
        """

        # Check if the user is participating in this giveaway
        user_id = callback_query.from_user.id
        participation_response = supabase.table('participations').select('*').eq('giveaway_id', giveaway_id).eq(
            'user_id', user_id).execute()
        is_participating = len(participation_response.data) > 0

        keyboard = InlineKeyboardBuilder()
        participate_text = "✅ Участвую" if is_participating else "Участвовать"
        keyboard.button(text=participate_text, url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}")
        keyboard.button(text="Назад к списку", callback_data="view_all_giveaways")
        keyboard.adjust(1)

        try:
            await bot.answer_callback_query(callback_query.id)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "query is too old" in str(e):
                logging.warning(f"Callback query is too old: {e}")
            else:
                raise

            # Check if giveaway has media
        if giveaway['media_type'] and giveaway['media_file_id']:
            try:
                if giveaway['media_type'] == 'photo':
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=types.InputMediaPhoto(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
                elif giveaway['media_type'] == 'gif':
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=types.InputMediaAnimation(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
                elif giveaway['media_type'] == 'video':
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=types.InputMediaVideo(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise
        else:
            # If no media, use the default image
            try:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise

    except Exception as e:
        logging.error(f"Error in process_view_created_giveaway: {str(e)}")
        try:
            await bot.answer_callback_query(callback_query.id,
                                            text="Произошла ошибка при получении информации о розыгрыше.")
        except aiogram.exceptions.TelegramBadRequest:
            logging.warning("Failed to answer callback query due to timeout")

        # Send a new message with the error information
        await bot.send_message(
            chat_id=callback_query.from_user.id,
            text="Произошла ошибка при получении информации о розыгрыше. Пожалуйста, попробуйте еще раз."
        )


@dp.callback_query(lambda c: c.data == 'create_giveaway')
async def process_create_giveaway(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    await state.set_state(GiveawayStates.waiting_for_name)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
    await send_message_with_image(bot, callback_query.from_user.id, "Напишите название розыгрыша",
                                  reply_markup=keyboard, message_id=callback_query.message.message_id)
    await state.update_data(last_message_id=callback_query.message.message_id)


@dp.message(GiveawayStates.waiting_for_name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(GiveawayStates.waiting_for_description)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
    data = await state.get_data()
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    await send_message_with_image(bot, message.chat.id, "Напишите описание для розыгрыша", reply_markup=keyboard,
                                  message_id=data['last_message_id'])


@dp.message(GiveawayStates.waiting_for_description)
async def process_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await state.set_state(GiveawayStates.waiting_for_media_choice)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Да", callback_data="add_media")
    keyboard.button(text="Пропустить", callback_data="skip_media")
    keyboard.button(text="В меню", callback_data="back_to_main_menu")
    keyboard.adjust(2, 1)
    data = await state.get_data()
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    await send_message_with_image(bot, message.chat.id, "Хотите добавить фото, GIF или видео?",
                                  reply_markup=keyboard.as_markup(), message_id=data['last_message_id'])


@dp.callback_query(lambda c: c.data in ["add_media", "skip_media"])
async def process_media_choice(callback_query: types.CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    if callback_query.data == "add_media":
        await state.set_state(GiveawayStates.waiting_for_media_upload)
        await send_message_with_image(bot, callback_query.from_user.id, "Пожалуйста, отправьте фото, GIF или видео.",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                          [InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]]),
                                      message_id=callback_query.message.message_id)
    else:
        await process_end_time_request(callback_query.from_user.id, state, callback_query.message.message_id)


@dp.message(GiveawayStates.waiting_for_media_upload)
async def process_media_upload(message: types.Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.animation:
        file_id = message.animation.file_id
        media_type = 'gif'
    elif message.video:
        file_id = message.video.file_id
        media_type = 'video'
    else:
        await message.reply("Пожалуйста, отправьте фото, GIF или видео.")
        return

    await state.update_data(media_type=media_type, media_file_id=file_id)

    # Delete the user's media message
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # Update the previous bot message
    data = await state.get_data()
    previous_message_id = data.get('last_message_id')
    giveaway_id = data.get('giveaway_id')

    if giveaway_id:
        # Editing existing giveaway
        await send_message_with_image(
            bot,
            message.chat.id,
            "Медиафайл успешно обновлен для розыгрыша.",
            message_id=previous_message_id
        )
        await process_view_created_giveaway(types.CallbackQuery(
            id=str(message.message_id),
            from_user=message.from_user,
            chat_instance=str(message.chat.id),
            message=message,
            data=f"view_created_giveaway:{giveaway_id}"
        ))
    else:
        # New giveaway
        if previous_message_id:
            await send_message_with_image(
                bot,
                message.chat.id,
                "Медиафайл успешно добавлен к розыгрышу. Укажите время завершения розыгрыша в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'",
                message_id=previous_message_id
            )
        else:
            new_message = await send_message_with_image(
                bot,
                message.chat.id,
                "Медиафайл успешно добавлен к розыгрышу. Укажите время завершения розыгрыша в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'"
            )
            await state.update_data(last_message_id=new_message.message_id)

        await state.set_state(GiveawayStates.waiting_for_end_time)


async def process_end_time_request(chat_id: int, state: FSMContext, message_id: int = None):
    await state.set_state(GiveawayStates.waiting_for_end_time)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
    await send_message_with_image(bot, chat_id, "Укажите время завершения розыгрыша в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'",
                                  reply_markup=keyboard, message_id=message_id)


@dp.message(GiveawayStates.waiting_for_end_time)
async def process_end_time(message: types.Message, state: FSMContext):
    try:
        datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        await state.update_data(end_time=message.text)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await state.set_state(GiveawayStates.waiting_for_winner_count)
        data = await state.get_data()
        await send_message_with_image(
            bot,
            message.chat.id,
            "Укажите количество победителей",
            message_id=data.get('last_message_id')
        )
    except ValueError:
        await send_message_with_image(
            bot,
            message.chat.id,
            "Неверный формат даты. Пожалуйста, попробуйте еще раз.",
            message_id=message.message_id
        )


@dp.message(GiveawayStates.waiting_for_winner_count)
async def process_winner_count(message: types.Message, state: FSMContext):
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    try:
        winner_count = int(message.text)
        data = await state.get_data()
        success = await save_giveaway(
            message.from_user.id,
            data['name'],
            data['description'],
            data['end_time'],
            winner_count,
            data.get('media_type'),
            data.get('media_file_id')
        )

        if success:
            await send_message_with_image(
                bot,
                message.chat.id,
                "Розыгрыш успешно создан исохранен!",
                message_id=data.get('last_message_id')
            )
            # Переход к созданным розыгрышам
            await cmd_start(message, state)
        else:
            await send_message_with_image(
                bot,
                message.chat.id,
                "Произошла ошибка при сохранении розыгрыша. Пожалуйста, попробуйте еще раз.",
                message_id=data.get('last_message_id')
            )
    except ValueError:
        data = await state.get_data()
        await send_message_with_image(
            bot,
            message.chat.id,
            "Пожалуйста, введите целое число.",
            message_id=data.get('last_message_id')
        )
    finally:
        await state.clear()


@dp.callback_query(lambda c: c.data == 'created_giveaways')
async def process_created_giveaways(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        response = supabase.table('giveaways').select('*').eq('user_id', user_id).eq('is_active', False).execute()

        if not response.data:
            await bot.answer_callback_query(callback_query.id, text="У вас нет созданных розыгрышей.")
            return

        # Генерация клавиатуры
        keyboard = InlineKeyboardBuilder()
        for giveaway in response.data:
            keyboard.button(text=giveaway['name'], callback_data=f"view_created_giveaway:{giveaway['id']}")
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)

        # Обновление сообщения с изображением
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Выберите розыгрыш для просмотра:",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    except Exception as e:
        logging.error(f"Error in process_created_giveaways: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении розыгрышей.")


@dp.callback_query(lambda c: c.data.startswith('add_media:') or c.data.startswith('change_media:'))
async def process_add_or_change_media(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(GiveawayStates.waiting_for_media_edit)
    try:
        message = await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Пожалуйста, отправьте фото, GIF или видео.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]
            ]),
            message_id=callback_query.message.message_id
        )
        if message and hasattr(message, 'message_id'):
            await state.update_data(message_to_delete=message.message_id)
        else:
            logging.warning("send_message_with_image did not return a valid message object")
    except Exception as e:
        logging.error(f"Error in process_add_or_change_media: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка. Пожалуйста, попробуйте еще раз.")
    finally:
        await bot.answer_callback_query(callback_query.id)


@dp.message(GiveawayStates.waiting_for_media_edit)
async def process_media_edit(message: types.Message, state: FSMContext):
    if message.photo:
        file_id = message.photo[-1].file_id
        media_type = 'photo'
    elif message.animation:
        file_id = message.animation.file_id
        media_type = 'gif'
    elif message.video:
        file_id = message.video.file_id
        media_type = 'video'
    else:
        await message.reply("Пожалуйста, отправьте фото, GIF или видео.")
        return

    data = await state.get_data()
    giveaway_id = data.get('giveaway_id')

    if not giveaway_id:
        await message.reply("Произошла ошибка. Пожалуйста, попробуйте снова.")
        await state.clear()
        return

    # Обновляем медиа файл в базе данных
    supabase.table('giveaways').update({
        'media_type': media_type,
        'media_file_id': file_id
    }).eq('id', giveaway_id).execute()

    # Delete the message that asked for media upload
    message_to_delete = data.get('message_to_delete')
    if message_to_delete:
        await bot.delete_message(chat_id=message.chat.id, message_id=message_to_delete)

    # Удаляем сообщение пользователя с медиа
    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

    # Очищаем состояние и возвращаемся к просмотру розыгрыша
    await state.clear()
    await process_view_created_giveaway(types.CallbackQuery(
        id=str(message.message_id),
        from_user=message.from_user,
        chat_instance=str(message.chat.id),
        message=message,
        data=f"view_created_giveaway:{giveaway_id}"
    ))


@dp.callback_query(lambda c: c.data.startswith('view_created_giveaway:'))
async def process_view_created_giveaway(callback_query: types.CallbackQuery):
    try:
        giveaway_id = callback_query.data.split(':')[1]

        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        if not response.data:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        giveaway = response.data

        # Генерация клавиатуры
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
        keyboard.button(text="Изменить дату завершения", callback_data=f"change_end_date:{giveaway_id}")
        keyboard.button(text="Активировать розыгрыш", callback_data=f"activate_giveaway:{giveaway_id}")
        keyboard.button(text="Добавить медиа файл" if not giveaway['media_type'] else "Медиа файл",
                        callback_data=f"manage_media:{giveaway_id}")
        keyboard.button(text="Удалить розыгрыш", callback_data=f"delete_giveaway:{giveaway_id}")
        keyboard.button(text="Назад к списку", callback_data="created_giveaways")
        keyboard.adjust(1)

        giveaway_info = f"""
Название: {giveaway['name']}
Описание: {giveaway['description']}
Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК
Победителей: {giveaway['winner_count']}
        """

        try:
            await bot.answer_callback_query(callback_query.id)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "query is too old" in str(e):
                logging.warning(f"Callback query is too old: {e}")
            else:
                raise

        # Check if giveaway has media
        if giveaway['media_type'] and giveaway['media_file_id']:
            try:
                media_type = giveaway['media_type']
                if media_type == 'photo':
                    media = types.InputMediaPhoto(media=giveaway['media_file_id'], caption=giveaway_info)
                elif media_type == 'gif':
                    media = types.InputMediaAnimation(media=giveaway['media_file_id'], caption=giveaway_info)
                elif media_type == 'video':
                    media = types.InputMediaVideo(media=giveaway['media_file_id'], caption=giveaway_info)
                else:
                    raise ValueError(f"Unsupported media type: {media_type}")

                await bot.edit_message_media(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    media=media,
                    reply_markup=keyboard.as_markup()
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise
        else:
            # If no media, use the default image
            try:
                result = await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )
                if result is None:
                    logging.warning("Failed to send or edit message with image")
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise

    except Exception as e:
        logging.error(f"Error in process_view_created_giveaway: {str(e)}")
        try:
            await bot.answer_callback_query(callback_query.id,
                                            text="Произошла ошибка при получении информации о розыгрыше.")
        except aiogram.exceptions.TelegramBadRequest:
            logging.warning("Failed to answer callback query due to timeout")

        # Send a new message with the error information
        await bot.send_message(
            chat_id=callback_query.from_user.id,
            text="Произошла ошибка при получении информации о розыгрыше. Пожалуйста, попробуйте еще раз."
        )


async def send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard):
    if giveaway['media_type'] and giveaway['media_file_id']:
        media_type = giveaway['media_type']
        if media_type == 'photo':
            await bot.send_photo(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                 reply_markup=keyboard.as_markup())
        elif media_type == 'gif':
            await bot.send_animation(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                     reply_markup=keyboard.as_markup())
        elif media_type == 'video':
            await bot.send_video(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                 reply_markup=keyboard.as_markup())
    else:
        await send_message_with_image(bot, chat_id, giveaway_info, reply_markup=keyboard.as_markup())

@dp.callback_query(lambda c: c.data.startswith('manage_media:'))
async def process_manage_media(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
    giveaway = giveaway_response.data

    if giveaway['media_type']:
        # Если медиа существует, показываем опции изменения или удаления
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Изменить медиа файл", callback_data=f"change_media:{giveaway_id}")
        keyboard.button(text="Удалить медиа файл", callback_data=f"delete_media:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Выберите действие, которое хотите сделать:",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )
    else:
        # Если медиа нет, спрашиваем, хочет ли пользователь добавить
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Да", callback_data=f"add_media:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(2)

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Хотите добавить фото, GIF или видео?",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )


@dp.callback_query(lambda c: c.data.startswith('delete_media:'))
async def process_delete_media(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]

    # Update the giveaway to remove media
    supabase.table('giveaways').update({
        'media_type': None,
        'media_file_id': None
    }).eq('id', giveaway_id).execute()

    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Медиа файл удален.",
        message_id=callback_query.message.message_id
    )

    # Return to the giveaway view
    await process_view_created_giveaway(callback_query)


@dp.callback_query(lambda c: c.data.startswith('delete_giveaway:'))
async def process_delete_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Да", callback_data=f"confirm_delete_giveaway:{giveaway_id}")
    keyboard.button(text="Отмена", callback_data=f"cancel_delete_giveaway:{giveaway_id}")
    keyboard.adjust(2)

    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Вы уверены, что хотите удалить розыгрыш?",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )


@dp.callback_query(lambda c: c.data.startswith('confirm_delete_giveaway:'))
async def process_confirm_delete_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    try:
        # Delete related records from giveaway_communities table
        supabase.table('giveaway_communities').delete().eq('giveaway_id', giveaway_id).execute()

        # Delete related records from participations table
        supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()

        # Delete the giveaway from giveaways table
        response = supabase.table('giveaways').delete().eq('id', giveaway_id).execute()

        if response.data:
            await send_message_with_image(
                bot,
                callback_query.fromuser.id,
                "Розыгрыш успешно удален.",
                message_id=callback_query.message.message_id
            )
        else:
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "Произошла ошибка при удалении розыгрыша.",
                message_id=callback_query.message.message_id
            )
    except Exception as e:
        logging.error(f"Error deleting giveaway: {str(e)}")
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Произошла ошибка при удалении розыгрыша.",
            message_id=callback_query.message.message_id
        )


@dp.callback_query(lambda c: c.data.startswith('cancel_delete_giveaway:'))
async def process_cancel_delete_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    await process_view_created_giveaway(callback_query)


@dp.callback_query(lambda c: c.data.startswith('change_end_date:'))
async def process_change_end_date(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    await state.update_data(giveaway_id=giveaway_id)
    await state.update_data(instruction_message_id=callback_query.message.message_id)
    await state.set_state(GiveawayStates.waiting_for_new_end_time)

    # Редактирование сообщения с инструкцией
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
    await send_message_with_image(
        bot,
        callback_query.message.chat.id,
        "Укажите новую дату завершения розыгрыша в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )
    await bot.answer_callback_query(callback_query.id)


@dp.message(GiveawayStates.waiting_for_new_end_time)
async def process_new_end_time(message: types.Message, state: FSMContext):
    try:
        new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        instruction_message_id = data['instruction_message_id']

        moscow_tz = pytz.timezone('Europe/Moscow')
        new_end_time_tz = moscow_tz.localize(new_end_time)

        response = supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id',
                                                                                                    giveaway_id).execute()

        if response.data:
            # Delete the user's message
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            # Delete the instruction message
            await bot.delete_message(chat_id=message.chat.id, message_id=instruction_message_id)

            # Update the bot's message with the updated giveaway details
            await process_view_created_giveaway(types.CallbackQuery(
                id=str(message.message_id),
                from_user=message.from_user,
                chat_instance=str(message.chat.id),
                message=message,
                data=f"view_created_giveaway:{giveaway_id}"
            ))
            # Clear the state as we're done with date input
            await state.clear()
        else:
            await send_message_with_image(bot, message.chat.id,
                                          "Произошла ошибка при обновлении даты завершения розыгрыша.")
            # Keep the state active to allow retry
    except ValueError:
        # Delete the user's incorrect message
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        # Update the instruction message with the error text
        data = await state.get_data()
        instruction_message_id = data['instruction_message_id']
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{data['giveaway_id']}")

        # Use edit_message_text instead of send_message_with_image
        await bot.edit_message_text(
            "Вы ввели неправильный формат даты.\nУкажите новую дату завершения розыгрыша в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'",
            chat_id=message.chat.id,
            message_id=instruction_message_id,
            reply_markup=keyboard.as_markup()
        )
        # State remains active, allowing for retry


async def get_giveaway_creator(giveaway_id: str) -> int:
    response = supabase.table('giveaways').select('user_id').eq('id', giveaway_id).single().execute()
    if response.data:
        return int(response.data['user_id'])  # Убедимся, что возвращаемое значение — это int
    return -1  # Возвращаем значение по умолчанию


async def get_bound_communities(user_id: int) -> List[Dict[str, Any]]:
    response = supabase.table('bound_communities').select('*').eq('user_id', user_id).execute()
    return response.data if response.data else []


async def bind_community_to_giveaway(giveaway_id: str, community_id: str, community_username: str):
    try:
        response = supabase.table('giveaway_communities').insert({
            'giveaway_id': giveaway_id,
            'community_id': community_id,
            'community_username': community_username
        }).execute()
        if response.data:
            logging.info(f"Bound community recorded: {response.data}")
            return True
        else:
            logging.error(f"Unexpected response format: {response}")
            return False
    except Exception as e:
        logging.error(f"Error recording bound community: {str(e)}")
        return False


@dp.callback_query(lambda c: c.data.startswith('bind_communities:'))
async def process_bind_communities(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    await state.update_data(giveaway_id=giveaway_id)
    await bot.answer_callback_query(callback_query.id)

    # Fetch bound communities for the user
    bound_communities = await get_bound_communities(callback_query.from_user.id)

    # Fetch communities already bound to this giveaway
    giveaway_communities = await get_giveaway_communities(giveaway_id)
    giveaway_community_ids = set(comm['community_id'] for comm in giveaway_communities)

    keyboard = InlineKeyboardBuilder()

    # Add buttons for bound communities
    for community in bound_communities:
        community_id = community['community_id']
        community_username = community['community_username']
        is_bound = community_id in giveaway_community_ids
        checkmark = ' ✅' if is_bound else ''
        keyboard.button(
            text=f"@{community_username}{checkmark}",
            callback_data=f"select_community:{giveaway_id}:{community_id}:{community_username}"
        )

    # Add buttons for other actions
    keyboard.button(text="Привязать новый паблик", callback_data=f"bind_new_community:{giveaway_id}")
    keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Выберите паблик для привязки или отвязки, или добавьте новый:",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )


@dp.callback_query(lambda c: c.data.startswith('bind_new_community:'))
async def process_bind_new_community(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(GiveawayStates.waiting_for_community_name)
    await bot.answer_callback_query(callback_query.id)

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Назад", callback_data=f"bind_communities:{giveaway_id}")
    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Чтобы привязать паблик, вы должны добавить этого бота @PepeGift_Bot в администраторы вашего паблика. После этого скиньте имя паблика пример: @publik",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )
    await state.update_data(last_message_id=callback_query.message.message_id)


@dp.message(GiveawayStates.waiting_for_community_name)
async def process_community_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    giveaway_id = data['giveaway_id']
    last_message_id = data.get('last_message_id')
    last_error_message = data.get('last_error_message', '')

    if not message.text.startswith('@'):
        await handle_invalid_input(message, state, giveaway_id, last_message_id, last_error_message)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        return

    channel_username = message.text[1:]  # Remove "@" prefix

    try:
        chat = await bot.get_chat(f"@{channel_username}")
        bot_member = await bot.get_chat_member(chat.id, bot.id)

        if bot_member.status == ChatMemberStatus.ADMINISTRATOR:
            await handle_successful_binding(message, state, giveaway_id, channel_username, last_message_id)
        else:
            await handle_not_admin(message, state, giveaway_id, last_message_id, last_error_message)
    except ValueError:
        await handle_channel_not_found(message, state, giveaway_id, last_message_id, last_error_message)
    except Exception as e:
        await handle_general_error(message, state, giveaway_id, last_message_id, last_error_message)

    await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)


async def handle_invalid_input(message: types.Message, state: FSMContext, giveaway_id: str, last_message_id: int,
                               last_error_message: str):
    new_error_message = "Пожалуйста, введите имя, начиная с @. Попробуйте еще раз."
    await update_error_message(message, state, giveaway_id, last_message_id, last_error_message, new_error_message)


async def handle_successful_binding(message: types.Message, state: FSMContext, giveaway_id: str, channel_username: str,
                                    last_message_id: int):
    try:
        # Получаем информацию о канале
        chat = await bot.get_chat(f"@{channel_username}")
        channel_id = chat.id

        # Проверяем, не привязан ли уже этот паблик к розыгрышу
        response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq('community_id',
                                                                                                        str(channel_id)).execute()
        if response.data:
            new_error_message = f"Паблик \"{channel_username}\" уже привязан к этому розыгрышу."
            await update_error_message(message, state, giveaway_id, last_message_id, "", new_error_message)
            return

        # Привязываем сообщество к розыгрышу, используя ID канала
        await bind_community_to_giveaway(giveaway_id, str(channel_id), channel_username)

        # Record the bound community
        await record_bound_community(message.from_user.id, channel_username, str(channel_id))

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data=f"bind_communities:{giveaway_id}")
        keyboard.adjust(1)

        await send_message_with_image(
            bot,
            message.chat.id,
            f"Паблик \"{channel_username}\" успешно привязан к розыгрышу!",
            reply_markup=keyboard.as_markup(),
            message_id=last_message_id
        )
        await state.clear()
    except Exception as e:
        logging.error(f"Error in handle_successful_binding: {str(e)}")
        await handle_general_error(message, state, giveaway_id, last_message_id, "")


async def handle_not_admin(message: types.Message, state: FSMContext, giveaway_id: str, last_message_id: int,
                           last_error_message: str):
    new_error_message = f"Бот не является администратором в паблике \"{message.text}\". Пожалуйста, добавьте бота в администраторы и попробуйте снова."
    await update_error_message(message, state, giveaway_id, last_message_id, last_error_message, new_error_message)


async def handle_channel_not_found(message: types.Message, state: FSMContext, giveaway_id: str, last_message_id: int,
                                   last_error_message: str):
    new_error_message = "Не удалось найти паблик с таким именем. Пожалуйста, проверьте правильность ссылки и попробуйте снова."
    await update_error_message(message, state, giveaway_id, last_message_id, last_error_message, new_error_message)


async def handle_general_error(message: types.Message, state: FSMContext, giveaway_id: str, last_message_id: int,
                               last_error_message: str):
    new_error_message = "Скорее всего, указано неверное название паблика. Пожалуйста, проверьте правильность ввода и попробуйте снова."
    await update_error_message(message, state, giveaway_id, last_message_id, last_error_message, new_error_message)


async def update_error_message(message: types.Message, state: FSMContext, giveaway_id: str, last_message_id: int,
                               last_error_message: str, new_error_message: str):
    if new_error_message != last_error_message:
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data=f"bind_communities:{giveaway_id}")
        keyboard.adjust(1)
        await send_message_with_image(
            bot,
            message.chat.id,
            new_error_message,
            reply_markup=keyboard.as_markup(),
            message_id=last_message_id
        )
        await state.update_data(last_error_message=new_error_message)
    await state.set_state(GiveawayStates.waiting_for_community_name)


@dp.callback_query(lambda c: c.data.startswith('activate_giveaway:'))
async def process_activate_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    try:
        response = supabase.table('giveaway_communities').select('community_id', 'community_username').eq('giveaway_id',
                                                                                                          giveaway_id).execute()
        communities = response.data

        if not communities:
            await bot.answer_callback_query(callback_query.id,
                                            text="К этому розыгрышу не привязано ни одного сообщества.")
            return

        keyboard = InlineKeyboardBuilder()
        for community in communities:
            keyboard.button(text=community['community_username'],
                            callback_data=f"toggle_community:{giveaway_id}:{community['community_id']}:{community['community_username']}")
        keyboard.button(text="Подтвердить выбор", callback_data=f"confirm_communities:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Выберите сообщества для публикации розыгрыша (нажмите на сообщество для выбора/отмены):",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )
    except Exception as e:
        logging.error(f"Error in process_activate_giveaway: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении списка сообществ.")


@dp.callback_query(lambda c: c.data.startswith('toggle_community:'))
async def process_toggle_community(callback_query: types.CallbackQuery):
    _, giveaway_id, community_id, community_username = callback_query.data.split(':')

    # Инициализация временного хранилища для пользователя
    user_id = callback_query.from_user.id
    if user_selected_communities.get(user_id) is None:
        user_selected_communities[user_id] = {'giveaway_id': giveaway_id, 'communities': set()}

    # Добавление или удаление сообщества
    community_data = (community_id, community_username)
    if community_data in user_selected_communities[user_id]['communities']:
        user_selected_communities[user_id]['communities'].remove(community_data)
    else:
        user_selected_communities[user_id]['communities'].add(community_data)

    # Обновляем текст кнопки
    keyboard = callback_query.message.reply_markup
    for row in keyboard.inline_keyboard:
        for button in row:
            if button.callback_data == callback_query.data:
                if '✅' in button.text:
                    button.text = button.text.replace(' ✅', '')
                else:
                    button.text += ' ✅'
                break
        else:
            continue
        break

    await bot.answer_callback_query(callback_query.id)
    await bot.edit_message_reply_markup(
        chat_id=callback_query.message.chat.id,
        message_id=callback_query.message.message_id,
        reply_markup=keyboard
    )


@dp.callback_query(lambda c: c.data.startswith('select_community:'))
async def process_select_community(callback_query: types.CallbackQuery, state: FSMContext):
    _, giveaway_id, community_id, community_username = callback_query.data.split(':')

    # Check if the community is already bound to the giveaway
    is_bound = await is_community_bound(giveaway_id, community_id)

    if is_bound:
        # Unbind the community
        await unbind_community(giveaway_id, community_id)
        action_text = f"Паблик @{community_username} отвязан от розыгрыша."
    else:
        # Bind the community
        await bind_community(giveaway_id, community_id, community_username)
        action_text = f"Паблик @{community_username} привязан к розыгрышу."

    await bot.answer_callback_query(callback_query.id, text=action_text)

    # Refresh the communities list
    await process_bind_communities(callback_query, state)


async def is_community_bound(giveaway_id: str, community_id: str) -> bool:
    response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq('community_id',
                                                                                                    community_id).execute()
    return len(response.data) > 0


async def bind_community(giveaway_id: str, community_id: str, community_username: str):
    supabase.table('giveaway_communities').insert({
        'giveaway_id': giveaway_id,
        'community_id': community_id,
        'community_username': community_username
    }).execute()


async def unbind_community(giveaway_id: str, community_id: str):
    supabase.table('giveaway_communities').delete().eq('giveaway_id', giveaway_id).eq('community_id',
                                                                                      community_id).execute()

@dp.callback_query(lambda c: c.data.startswith('confirm_communities:'))
async def process_confirm_communities(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id

    # Проверка наличия данных в хранилище
    user_data = user_selected_communities.get(user_id)
    if not user_data or not user_data.get('communities'):
        await bot.answer_callback_query(callback_query.id, text="Выберите хотя бы одно сообщество для публикации.")
        return

    giveaway_id = user_data['giveaway_id']
    selected_communities = user_data['communities']

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Активировать розыгрыш", callback_data=f"publish_giveaway:{giveaway_id}")
    keyboard.button(text="Назад", callback_data=f"activate_giveaway:{giveaway_id}")
    keyboard.adjust(1)

    await bot.answer_callback_query(callback_query.id)
    community_usernames = [community[1] for community in selected_communities]
    await send_message_with_image(bot, callback_query.from_user.id,
                                  f"Розыгрыш будет опубликован в следующих сообществах: {', '.join(community_usernames)}",
                                  keyboard.as_markup(), message_id=callback_query.message.message_id)


@dp.callback_query(lambda c: c.data.startswith('publish_giveaway:'))
async def process_publish_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    user_id = callback_query.from_user.id

    # Проверяем временные данные пользователя
    user_data = user_selected_communities.get(user_id)
    if not user_data or 'communities' not in user_data:
        await bot.answer_callback_query(callback_query.id, text="Ошибка: нет выбранных сообществ для публикации.")
        return

    selected_communities = user_data['communities']

    try:
        # Получение информации о розыгрыше
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        post_text = f"""
{giveaway['name']}

{giveaway['description']}

Количество победителей: {giveaway['winner_count']}
Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК

Нажмите кнопку ниже, чтобы принять участие!
        """

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Участвовать", url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}")
        keyboard.adjust(1)
        success_count = 0
        error_count = 0
        error_messages = []

        # Публикация в выбранные сообщества
        for community_id, community_username in selected_communities:
            try:
                if giveaway['media_type'] and giveaway['media_file_id']:
                    if giveaway['media_type'] == 'photo':
                        await bot.send_photo(chat_id=int(community_id), photo=giveaway['media_file_id'],
                                             caption=post_text, reply_markup=keyboard.as_markup())
                    elif giveaway['media_type'] == 'gif':
                        await bot.send_animation(chat_id=int(community_id), animation=giveaway['media_file_id'],
                                                 caption=post_text, reply_markup=keyboard.as_markup())
                    elif giveaway['media_type'] == 'video':
                        await bot.send_video(chat_id=int(community_id), video=giveaway['media_file_id'],
                                             caption=post_text, reply_markup=keyboard.as_markup())
                else:
                    await bot.send_message(chat_id=int(community_id), text=post_text,
                                           reply_markup=keyboard.as_markup())
                success_count += 1
            except Exception as e:
                error_count += 1
                error_messages.append(f"Ошибка публикации в @{community_username}: {str(e)}")

        # Обработка результатов публикации
        if success_count > 0:
            try:
                # Clear previous winners first
                supabase.table('giveaway_winners').delete().eq('giveaway_id', giveaway_id).execute()
                # Then clear participants
                supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()
                # Finally activate the giveaway and set the created_at time
                moscow_tz = pytz.timezone('Europe/Moscow')
                current_time = datetime.now(moscow_tz)
                supabase.table('giveaways').update({
                    'is_active': True,
                    'created_at': current_time.isoformat()
                }).eq('id', giveaway_id).execute()
            except Exception as e:
                logging.error(f"Error clearing previous data or activating giveaway: {str(e)}")
                raise

            await bot.answer_callback_query(callback_query.id, text="Розыгрыш опубликован и активирован!")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад", callback_data="back_to_main_menu")

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"Розыгрыш успешно опубликован в {success_count} сообществах." +
                (f"\n\nПодробности ошибок:\n{chr(10).join(error_messages)}" if error_count > 0 else ""),
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        else:
            await bot.answer_callback_query(callback_query.id, text="Не удалось опубликовать розыгрыш.")
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"Не удалось опубликовать розыгрыш. Ошибок: {error_count}.\n\nПодробности ошибок:\n{chr(10).join(error_messages)}",
                message_id=callback_query.message.message_id
            )
    except Exception as e:
        logging.error(f"Error in process_publish_giveaway: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при публикации розыгрыша.")
    finally:
        # Удаляем временные данные
        user_selected_communities.pop(user_id, None)


@dp.callback_query(lambda c: c.data == 'active_giveaways')
async def process_active_giveaways(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        response = supabase.table('giveaways').select('*').eq('is_active', True).eq('user_id', user_id).order(
            'end_time').execute()
        giveaways = response.data

        if not giveaways:
            await bot.answer_callback_query(callback_query.id, text="У вас нет активных розыгрышей.")
            return

        keyboard = InlineKeyboardBuilder()
        for giveaway in giveaways:
            keyboard.button(text=giveaway['name'], callback_data=f"view_active_giveaway:{giveaway['id']}")
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                      message_id=callback_query.message.message_id, text="Выберите активный розыгрыш:",
                                      reply_markup=keyboard.as_markup())
    except Exception as e:
        logging.error(f"Error in process_active_giveaways: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении активных розыгрышей.")


@dp.callback_query(lambda c: c.data.startswith('view_active_giveaway:'))
async def process_view_active_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()

    if not response.data:
        await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
        return

    giveaway = response.data

    # Получение количества участников
    participants_response = supabase.table('participations').select('count').eq('giveaway_id', giveaway_id).execute()
    participants_count = participants_response.data[0]['count']

    # Add the participants count to the giveaway_info
    giveaway_info = f"""
Активный розыгрыш:

Название: {giveaway['name']}
Описание: {giveaway['description']}
Дата {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК
Количество победителей: {giveaway['winner_count']}
Участвуют: {participants_count}
    """

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Принудительное завершение", callback_data=f"force_end_giveaway:{giveaway_id}")
    keyboard.button(text="Назад к списку", callback_data="active_giveaways")
    keyboard.adjust(1)

    await bot.answer_callback_query(callback_query.id)

    try:
        await bot.answer_callback_query(callback_query.id)
    except aiogram.exceptions.TelegramBadRequest as e:
        if "query is too old" in str(e):
            logging.warning(f"Callback query is too old: {e}")
        else:
            raise

        # Check if giveaway has media
    if giveaway['media_type'] and giveaway['media_file_id']:
        try:
            if giveaway['media_type'] == 'photo':
                await bot.edit_message_media(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    media=types.InputMediaPhoto(media=giveaway['media_file_id'], caption=giveaway_info),
                    reply_markup=keyboard.as_markup()
                )
            elif giveaway['media_type'] == 'gif':
                await bot.edit_message_media(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    media=types.InputMediaAnimation(media=giveaway['media_file_id'], caption=giveaway_info),
                    reply_markup=keyboard.as_markup()
                )
            elif giveaway['media_type'] == 'video':
                await bot.edit_message_media(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    media=types.InputMediaVideo(media=giveaway['media_file_id'], caption=giveaway_info),
                    reply_markup=keyboard.as_markup()
                )
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message to edit not found" in str(e):
                logging.warning(f"Message to edit not found: {e}")
                # Fallback: send a new message
                await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
            else:
                raise
    else:
        # If no media, use the default image
        try:
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message to edit not found" in str(e):
                logging.warning(f"Message to edit not found: {e}")
                # Fallback: send a new message
                await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
            else:
                raise



@dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
async def process_force_end_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    await bot.answer_callback_query(callback_query.id, text="Завершение розыгрыша...")
    await end_giveaway(giveaway_id)
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Назад", callback_data="back_to_main_menu")
    await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                  message_id=callback_query.message.message_id,
                                  text="Розыгрыш успешно завершен. Результаты опубликованы в связанных сообществах.",
                                  reply_markup=keyboard.as_markup())


# Обработчик кнопки "Мои участия"
@dp.callback_query(lambda c: c.data == 'my_participations')
async def process_my_participations(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    try:
        response = supabase.table('participations').select('*, giveaways(*)').eq('user_id', user_id).execute()
        participations = response.data

        if not participations:
            await bot.answer_callback_query(callback_query.id, text="Вы не участвуете ни в одном розыгрыше.")
            return

        # Создаем клавиатуру с розыгрышами
        keyboard = InlineKeyboardBuilder()
        for participation in participations:
            giveaway = participation['giveaways']
            keyboard.button(text=giveaway['name'], callback_data=f"giveaway_{giveaway['id']}")
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        # Обновляем сообщение
        await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                      message_id=callback_query.message.message_id,
                                      text="Список розыгрышей, в которых вы участвуете:",
                                      reply_markup=keyboard.as_markup())
    except Exception as e:
        logging.error(f"Error in process_my_participations: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении ваших участий.")

# Обработчик кнопки с названием розыгрыша
@dp.callback_query(lambda c: c.data.startswith('giveaway_'))
async def process_giveaway_details(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split('_')[1]
    try:
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = response.data

        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        # Детали розыгрыша
        giveaway_info = (f"Название: {giveaway['name']}\n"
                         f"Описание: {giveaway['description']}\n"
                         f"Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК")

        # Клавиатура с кнопкой назад
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад к списку", callback_data="my_participations")

        try:
            await bot.answer_callback_query(callback_query.id)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "query is too old" in str(e):
                logging.warning(f"Callback query is too old: {e}")
            else:
                raise

        # Check if giveaway has media
        if giveaway['media_type'] and giveaway['media_file_id']:
            try:
                if giveaway['media_type'] == 'photo':
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=types.InputMediaPhoto(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
                elif giveaway['media_type'] == 'gif':
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=types.InputMediaAnimation(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
                elif giveaway['media_type'] == 'video':
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=types.InputMediaVideo(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise
        else:
            # If no media, use the default image
            try:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise

    except Exception as e:
        logging.error(f"Error in process_giveaway_details: {str(e)}")
        try:
            await bot.answer_callback_query(callback_query.id,
                                            text="Произошла ошибка при получении информации о розыгрыше.")
        except aiogram.exceptions.TelegramBadRequest:
            logging.warning("Failed to answer callback query due to timeout")

        # Send a new message with the error information
        await bot.send_message(
            chat_id=callback_query.from_user.id,
            text="Произошла ошибка при получении информации о розыгрыше. Пожалуйста, попробуйте еще раз."
        )


# Обработчик кнопки "Назад в главное меню"
@dp.callback_query(lambda c: c.data == 'back_to_main_menu')
async def process_back_to_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="Созданные розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="Мои участия", callback_data="my_participations")
    keyboard.button(text="Смотреть все розыгрыши", callback_data="view_all_giveaways") #New button
    keyboard.adjust(1)

    await send_message_with_image(bot, callback_query.from_user.id, "Выберите действие:", keyboard.as_markup(),
                                  message_id=callback_query.message.message_id)


@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def process_back_to_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="Созданные розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="Мои участия", callback_data="my_participations")
    keyboard.button(text="Смотреть все розыгрыши", callback_data="view_all_giveaways") #New button
    keyboard.adjust(1)
    await send_message_with_image(bot, callback_query.from_user.id, "Выберите действие:", keyboard.as_markup(),
                                  message_id=callback_query.message.message_id)


async def check_and_update_usernames():
    try:
        response = supabase.table('bound_communities').select('*').execute()
        communities = response.data

        for community in communities:
            try:
                chat = await bot.get_chat(int(community['community_id']))
                current_username = chat.username

                if current_username != community['community_username']:
                    logging.info(
                        f"Username changed for community {community['community_id']}: {community['community_username']} -> {current_username}")

                    # Update bound_communities table
                    supabase.table('bound_communities').update({
                        'community_username': current_username
                    }).eq('community_id', community['community_id']).execute()

                    # Update giveaway_communities table
                    supabase.table('giveaway_communities').update({
                        'community_username': current_username
                    }).eq('community_id', community['community_id']).execute()

                    logging.info(f"Updated username for community {community['community_id']} in both tables")
            except Exception as e:
                logging.error(f"Error checking community {community['community_id']}: {str(e)}")

    except Exception as e:
        logging.error(f"Error in check_and_update_usernames: {str(e)}")


async def periodic_username_check():
    while True:
        await check_and_update_usernames()
        await asyncio.sleep(1800)  # Check every hour

async def record_bound_community(user_id: int, community_username: str, community_id: str):
    try:
        # Check if community is already recorded for the user
        response = supabase.table('bound_communities').select('*').eq('user_id', user_id).eq('community_id',
                                                                                             community_id).execute()
        if response.data:
            logging.info(f"Community {community_username} is already recorded for user {user_id}")
            return True

        response = supabase.table('bound_communities').insert({
            'user_id': user_id,
            'community_username': community_username,
            'community_id': community_id
        }).execute()
        if response.data:
            logging.info(f"Bound community recorded: {response.data}")
            return True
        else:
            logging.error(f"Unexpected response format: {response}")
            return False
    except Exception as e:
        logging.error(f"Error recording bound community: {str(e)}")
        return False

# Главная функция запуска бота
async def main():
    # Запускаем проверку завершившихся розыгрышей
    check_task = asyncio.create_task(check_ended_giveaways())

    # Запускаем периодическую проверку имен пользователей
    username_check_task = asyncio.create_task(periodic_username_check())

    try:
        # Запускаем бота
        await dp.start_polling(bot)
    finally:
        # Отменяем задачи при остановке бота
        check_task.cancel()
        username_check_task.cancel()


if __name__ == '__main__':
    asyncio.run(main())

