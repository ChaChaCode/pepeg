from typing import Dict
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image
from aiogram.enums import ChatMemberStatus

# Bot configuration and initialization
BOT_TOKEN = '7908502974:AAHypTBbfW-c9JR94HNYFLL9ZcN-2LaJFoU'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

user_selected_communities = {}
paid_users: Dict[int, str] = {}


# States for the FSM
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
    waiting_for_congrats_message = State()
    waiting_for_common_congrats_message = State()
    waiting_for_edit_name = State()
    waiting_for_edit_description = State()
    waiting_for_edit_winner_count = State()
    creating_giveaway = State()
    binding_communities = State()

def register_new_public(dp: Dispatcher, bot: Bot, supabase: Client):
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

    async def handle_successful_binding(message: types.Message, state: FSMContext, giveaway_id: str,
                                        channel_username: str,
                                        last_message_id: int):
        try:
            # Получаем информацию о канале
            chat = await bot.get_chat(f"@{channel_username}")
            channel_id = chat.id

            # Проверяем, не привязан ли уже этот паблик к розыгрышу
            response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq(
                'community_id',
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

    async def handle_not_admin(message: types.Message, state: FSMContext, giveaway_id: str, last_message_id: int,
                               last_error_message: str):
        new_error_message = f"Бот не является администратором в паблике \"{message.text}\". Пожалуйста, добавьте бота в администраторы и попробуйте снова."
        await update_error_message(message, state, giveaway_id, last_message_id, last_error_message, new_error_message)

    async def handle_channel_not_found(message: types.Message, state: FSMContext, giveaway_id: str,
                                       last_message_id: int,
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

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username):
        data = {
            "giveaway_id": giveaway_id,
            "community_id": community_id,
            "community_username": community_username
        }
        supabase.table("giveaway_communities").insert(data).execute()
