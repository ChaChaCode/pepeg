from typing import List, Dict, Any
import asyncio
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command
from datetime import datetime, timedelta
import pytz
from supabase import create_client, Client
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image

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
    if not response.data:
        logging.error(f"Error fetching participants: No participants found")
        return
    participants = response.data

    # Select winners
    winners = await select_random_winners(participants, min(len(participants), giveaway['winner_count']))

    # Update giveaway status
    supabase.table('giveaways').update({'is_active': False}).eq('id', giveaway_id).execute()

    # Save winners
    for winner in winners:
        supabase.table('giveaway_winners').insert({
            'giveaway_id': giveaway_id,
            'user_id': winner['user_id'],
            'username': winner['username']
        }).execute()

    # Notify winners and publish results
    await notify_winners_and_publish_results(giveaway, winners)

    # Clear participants
    supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()


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


async def notify_winners_and_publish_results(giveaway, winners):
    response = supabase.table('giveaway_communities').select('community_id').eq('giveaway_id', giveaway['id']).execute()
    if not response.data:
        logging.error(f"Error fetching communities: No communities found")
        return
    communities = response.data

    winners_list = ', '.join([f"@{w['username']}" for w in winners])
    result_message = f"""
🎉 Розыгрыш завершен! 🎉

Название: {giveaway['name']}
Описание: {giveaway['description']}
Победители: {winners_list}

Поздравляем победителей!
    """

    if len(winners) < giveaway['winner_count']:
        result_message += f"\n\nВнимание: Количество участников ({len(winners)}) было меньше, чем количество призовых мест ({giveaway['winner_count']}). Не все призовые места были распределены."

    for community in communities:
        try:
            await bot.send_message(chat_id=int(community['community_id']), text=result_message)  # Changed to int
        except Exception as e:
            logging.error(f"Error publishing results in community @{community['community_id']}: {e}")

    for winner in winners:
        try:
            await bot.send_message(chat_id=winner['user_id'],
                                   text=f"Поздравляем! Вы выиграли в розыгрыше \"{giveaway['name']}\"!")
        except Exception as e:
            logging.error(f"Error notifying winner {winner['user_id']}: {e}")


# Command handlers
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="Созданные розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="Мои участия", callback_data="my_participations")
    keyboard.adjust(1)  # One button per row

    sent_message = await send_message_with_image(bot, message.chat.id, "Выберите действие:", keyboard.as_markup())
    await state.update_data(last_message_id=sent_message)


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
                "Розыгрыш успешно создан и сохранен!",
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

        keyboard = InlineKeyboardBuilder()
        for giveaway in response.data:
            keyboard.button(text=giveaway['name'], callback_data=f"view_created_giveaway:{giveaway['id']}")
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        keyboard.adjust(1)  # One button per row

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(bot, callback_query.from_user.id, "Выберите розыгрыш для просмотра:",
                                      keyboard.as_markup(), message_id=callback_query.message.message_id)

    except Exception as e:
        logging.error(f"Error in process_created_giveaways: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении розыгрышей.")


@dp.callback_query(lambda c: c.data.startswith('view_created_giveaway:'))
async def process_view_created_giveaway(callback_query: types.CallbackQuery):
    try:
        giveaway_id = callback_query.data.split(':')[1]
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()

        if not response.data:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        giveaway = response.data

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
        keyboard.button(text="Изменить дату завершения", callback_data=f"change_end_date:{giveaway_id}")
        keyboard.button(text="Активировать розыгрыш", callback_data=f"activate_giveaway:{giveaway_id}")
        keyboard.button(text="Удалить розыгрыш", callback_data=f"delete_giveaway:{giveaway_id}")
        keyboard.button(text="Назад к списку", callback_data="created_giveaways")
        keyboard.adjust(1)

        giveaway_info = f"""
Название: {giveaway['name']}
Описание: {giveaway['description']}
Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК
Победителей: {giveaway['winner_count']}
        """

        await bot.answer_callback_query(callback_query.id)

        if giveaway['media_type'] and giveaway['media_file_id']:
            if giveaway['media_type'] == 'photo':
                await bot.send_photo(chat_id=callback_query.from_user.id, photo=giveaway['media_file_id'],
                                     caption=giveaway_info, reply_markup=keyboard.as_markup())
            elif giveaway['media_type'] == 'gif':
                await bot.send_animation(chat_id=callback_query.from_user.id, animation=giveaway['media_file_id'],
                                         caption=giveaway_info, reply_markup=keyboard.as_markup())
            elif giveaway['media_type'] == 'video':
                await bot.send_video(chat_id=callback_query.from_user.id, video=giveaway['media_file_id'],
                                     caption=giveaway_info, reply_markup=keyboard.as_markup())
        else:
            await send_message_with_image(bot, callback_query.from_user.id, giveaway_info, keyboard.as_markup(),
                                          message_id=callback_query.message.message_id)
    except Exception as e:
        logging.error(f"Error in process_view_created_giveaway: {str(e)}")
        await bot.answer_callback_query(
            callback_query.id,
            text="Произошла ошибка при получении информации о розыгрыше."
        )


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
        keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )


@dp.callback_query(lambda c: c.data.startswith('confirm_delete_giveaway:'))
async def process_confirm_delete_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    try:
        response = supabase.table('giveaways').delete().eq('id', giveaway_id).execute()
        if response.data:
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
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
    await state.set_state(GiveawayStates.waiting_for_new_end_time)
    await bot.answer_callback_query(callback_query.id)
    data = await state.get_data()
    await send_message_with_image(bot, callback_query.from_user.id,
                                  "Укажите новую дату завершения розыгрыша в формате 'ДД.ММ.ГГГГ ЧЧ:ММ'",
                                  message_id=data.get('last_message_id'))
    await state.update_data(last_message_id=None)


@dp.message(GiveawayStates.waiting_for_new_end_time)
async def process_new_end_time(message: types.Message, state: FSMContext):
    try:
        new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        moscow_tz = pytz.timezone('Europe/Moscow')
        new_end_time_tz = moscow_tz.localize(new_end_time)

        response = supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id',
                                                                                                    giveaway_id).execute()

        if response.data:
            await send_message_with_image(bot, message.chat.id, "Дата завершения розыгрыша успешно обновлена!")
        else:
            await send_message_with_image(bot, message.chat.id,
                                          "Произошла ошибка при обновлении даты завершения розыгрыша.")
    except ValueError:
        await send_message_with_image(bot, message.chat.id, "Неверный формат даты. Пожалуйста, попробуйте еще раз.")
    finally:
        await state.clear()


async def get_giveaway_creator(giveaway_id: str) -> int:
    response = supabase.table('giveaways').select('user_id').eq('id', giveaway_id).single().execute()
    if response.data:
        return response.data['user_id']
    return None


async def get_bound_communities(user_id: int) -> List[Dict[str, Any]]:
    response = supabase.table('bound_communities').select('*').eq('user_id', user_id).execute()
    return response.data if response.data else []


async def bind_community_to_giveaway(giveaway_id: str, channel_id: str, community_username: str):
    # Check if the community is already bound to the giveaway
    response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq('community_id',
                                                                                                    channel_id).execute()

    if not response.data:
        response = supabase.table('giveaway_communities').insert({
            'giveaway_id': giveaway_id,
            'community_id': channel_id,
            'community_username': community_username
        }).execute()

        if not response.data:
            logging.error(f"Error binding community to giveaway: {response}")
            raise Exception('Failed to bind community to giveaway')

        # Record the bound community only if it's not already recorded
        user_id = await get_giveaway_creator(giveaway_id)
        if user_id:
            await record_bound_community(user_id, community_username, channel_id)
    else:
        logging.info(f"Community {community_username} is already bound to giveaway {giveaway_id}")


async def record_bound_community(user_id: int, community_username: str, community_id: str):
    try:
        # Check if the community is already recorded for this user
        response = supabase.table('bound_communities').select('*').eq('user_id', user_id).eq('community_id',
                                                                                             community_id).execute()

        if not response.data:
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
        else:
            logging.info(f"Community {community_username} is already recorded for user {user_id}")
            return True
    except Exception as e:
        logging.error(f"Error recording bound community: {str(e)}")
        return False


@dp.callback_query(lambda c: c.data.startswith('bind_communities:'))
async def process_bind_communities(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(GiveawayStates.waiting_for_community_name)
    await bot.answer_callback_query(callback_query.id)
    data = await state.get_data()

    # Fetch bound communities for the user
    bound_communities = await get_bound_communities(callback_query.from_user.id)

    keyboard = InlineKeyboardBuilder()

    # Add buttons for bound communities
    for community in bound_communities:
        keyboard.button(text=f"@{community['community_username']}",
                        callback_data=f"select_community:{giveaway_id}:{community['community_id']}:{community['community_username']}")

    # Add buttons for other actions
    keyboard.button(text="Привязать новый паблик", callback_data=f"bind_new_community:{giveaway_id}")
    keyboard.button(text="Назад", callback_data="created_giveaways")
    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Выберите паблик для привязки или добавьте новый:",
        reply_markup=keyboard.as_markup(),
        message_id=data.get('last_message_id')
    )


@dp.callback_query(lambda c: c.data.startswith('select_community:'))
async def process_select_community(callback_query: types.CallbackQuery):
    _, giveaway_id, community_id, community_username = callback_query.data.split(':')

    # Check if the community is already bound to the giveaway
    response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq('community_id',
                                                                                                    community_id).execute()

    if not response.data:
        # If not bound, then bind the community to the giveaway
        await bind_community_to_giveaway(giveaway_id, community_id, community_username)
        await bot.answer_callback_query(callback_query.id, text="Паблик успешно привязан к розыгрышу!")
    else:
        await bot.answer_callback_query(callback_query.id, text="Этот паблик уже привязан к розыгрышу.")

    await process_view_created_giveaway(callback_query)


@dp.callback_query(lambda c: c.data.startswith('bind_new_community:'))
async def process_bind_new_community(callback_query: types.CallbackQuery, state: FSMContext):
    giveaway_id = callback_query.data.split(':')[1]
    await state.update_data(giveaway_id=giveaway_id)
    await state.set_state(GiveawayStates.waiting_for_community_name)
    await bot.answer_callback_query(callback_query.id)
    await send_message_with_image(
        bot,
        callback_query.from_user.id,
        "Чтобы привязать паблик, вы должны добавить этого бота @PepeGift_Bot в администраторы вашего паблика. После этого скиньте имя паблика пример: @publik",
        message_id=callback_query.message.message_id
    )


@dp.message(GiveawayStates.waiting_for_community_name)
async def process_community_name(message: types.Message, state: FSMContext):
    if not message.text.startswith('@'):
        await send_message_with_image(bot, message.chat.id, "Пожалуйста, введите имя, начиная с @")
        return

    channel_username = message.text[1:]  # Убираем префикс "@"
    data = await state.get_data()
    giveaway_id = data['giveaway_id']

    try:
        # Пытаемся получить информацию о чате и статус бота в нем
        chat = await bot.get_chat(f"@{channel_username}")
        bot_member = await bot.get_chat_member(chat.id, bot.id)

        if bot_member.status == 'administrator':  # Проверяем, является ли бот администратором
            # Сохраняем ID канала/группы и username
            await bind_community_to_giveaway(giveaway_id, str(chat.id), channel_username)
            await send_message_with_image(bot, message.chat.id,
                                          f"Паблик \"{message.text}\" успешно привязан к розыгрышу!")
        else:
            await send_message_with_image(bot, message.chat.id,
                                          f"Бот не является администратором в паблике \"{message.text}\". Пожалуйста, добавьте бота в администраторы и попробуйте снова.")
    except ValueError:
        # Если канал не найден или имя канала неверное
        await send_message_with_image(bot, message.chat.id,
                                      "Не удалось найти паблик с таким именем. Пожалуйста, проверьте правильность ссылки и попробуйте снова.")
    except Exception as e:
        await send_message_with_image(bot, message.chat.id,
                                      f"Произошла ошибка при проверке статуса бота. Пожалуйста, убедитесь, что вы указали правильное имя паблика и попробуйте снова.\nОшибка: {str(e)}")

    await state.clear()  # Очищаем состояние


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
        keyboard.button(text="Назад", callback_data="created_giveaways")  # Кнопка "Назад"
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(bot, callback_query.from_user.id,
                                      "Выберите сообщества для публикации розыгрыша (нажмите на сообщество для выбора/отмены):",
                                      keyboard.as_markup(), message_id=callback_query.message.message_id)
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
    keyboard.button(text="Назад", callback_data="created_giveaways")  # Кнопка "Назад"
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
            supabase.table('giveaways').update({'is_active': True}).eq('id', giveaway_id).execute()
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш опубликован и активирован!")
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"Розыгрыш успешно опубликован в {success_count} сообществах." +
                (f"\n\nПодробности ошибок:\n{chr(10).join(error_messages)}" if error_count > 0 else ""),
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

    if giveaway['media_type'] and giveaway['media_file_id']:
        if giveaway['media_type'] == 'photo':
            await bot.send_photo(chat_id=callback_query.from_user.id, photo=giveaway['media_file_id'],
                                 caption=giveaway_info, reply_markup=keyboard.as_markup())
        elif giveaway['media_type'] == 'gif':
            await bot.send_animation(chat_id=callback_query.from_user.id, animation=giveaway['media_file_id'],
                                     caption=giveaway_info, reply_markup=keyboard.as_markup())
        elif giveaway['media_type'] == 'video':
            await bot.send_video(chat_id=callback_query.from_user.id, video=giveaway['media_file_id'],
                                 caption=giveaway_info, reply_markup=keyboard.as_markup())
    else:
        await send_message_with_image(bot, chat_id=callback_query.from_user.id, text=giveaway_info,
                                      reply_markup=keyboard.as_markup(), message_id=callback_query.message.message_id)


@dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
async def process_force_end_giveaway(callback_query: types.CallbackQuery):
    giveaway_id = callback_query.data.split(':')[1]
    await bot.answer_callback_query(callback_query.id, text="Завершение розыгрыша...")
    await end_giveaway(giveaway_id)
    await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                  message_id=callback_query.message.message_id,
                                  text="Розыгрыш успешно завершен. Результаты опубликованы в связанных сообществах.")


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
        text = (f"Название: {giveaway['name']}\n"
                f"Описание: {giveaway['description']}\n"
                f"Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК")

        # Клавиатура с кнопкой назад
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Назад к списку", callback_data="my_participations")]
            ]
        )

        # Обновляем сообщение
        await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                      message_id=callback_query.message.message_id, text=text, reply_markup=keyboard)
    except Exception as e:
        logging.error(f"Error in process_giveaway_details: {str(e)}")
        await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении деталей розыгрыша.")


# Обработчик кнопки "Назад в главное меню"
@dp.callback_query(lambda c: c.data == 'back_to_main_menu')
async def process_back_to_main_menu(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="Созданные розыгрыши", callback_data="created_giveaways")
    keyboard.button(text="Активные розыгрыши", callback_data="active_giveaways")
    keyboard.button(text="Мои участия", callback_data="my_participations")
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
        await asyncio.sleep(3600)  # Check every hour


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

