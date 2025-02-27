from typing import List, Dict, Any
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import pytz
from supabase import create_client, Client
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardMarkup, InlineKeyboardButton
from utils import send_message_with_image
import aiogram.exceptions
import json
import asyncio
import math

# Bot configuration and initialization
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


def register_created_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'created_giveaways' or c.data.startswith('created_giveaways_page:'))
    async def process_created_giveaways(callback_query: types.CallbackQuery):
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        # Get page number from callback data
        current_page = 1
        if callback_query.data.startswith('created_giveaways_page:'):
            current_page = int(callback_query.data.split(':')[1])

        try:
            # Get all giveaways for pagination calculation
            response = supabase.table('giveaways').select('*').eq('user_id', user_id).eq('is_active', False).execute()

            if not response.data:
                await bot.answer_callback_query(callback_query.id, text="У вас нет созданных розыгрышей.")
                return

            total_giveaways = len(response.data)
            total_pages = math.ceil(total_giveaways / ITEMS_PER_PAGE)

            # Calculate slice indices for current page
            start_idx = (current_page - 1) * ITEMS_PER_PAGE
            end_idx = start_idx + ITEMS_PER_PAGE

            # Get giveaways for current page
            current_giveaways = response.data[start_idx:end_idx]

            # Generate keyboard with pagination
            keyboard = InlineKeyboardBuilder()

            # Add giveaway buttons (each in its own row)
            for giveaway in current_giveaways:
                keyboard.row(types.InlineKeyboardButton(
                    text=giveaway['name'],
                    callback_data=f"view_created_giveaway:{giveaway['id']}"
                ))

            # Create navigation row
            nav_buttons = []

            # Previous page button
            if current_page > 1:
                nav_buttons.append(types.InlineKeyboardButton(
                    text="←",
                    callback_data=f"created_giveaways_page:{current_page - 1}"
                ))

            # Page indicator - only show if there's more than one page
            if total_pages > 1:
                nav_buttons.append(types.InlineKeyboardButton(
                    text=f"{current_page}/{total_pages}",
                    callback_data="ignore"
                ))

            # Next page button
            if current_page < total_pages:
                nav_buttons.append(types.InlineKeyboardButton(
                    text="→",
                    callback_data=f"created_giveaways_page:{current_page + 1}"
                ))

            # Add navigation buttons in one row if there are any
            if nav_buttons:
                keyboard.row(*nav_buttons)

            # Add back button in its own row
            keyboard.row(types.InlineKeyboardButton(
                text="Назад",
                callback_data="back_to_main_menu"
            ))

            await bot.answer_callback_query(callback_query.id)

            # Update message with pagination info
            message_text = "Выберите розыгрыш для просмотра"
            if total_pages > 1:
                message_text += f" (Страница {current_page} из {total_pages}):"
            else:
                message_text += ":"

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )

        except Exception as e:
            logging.error(f"Error in process_created_giveaways: {str(e)}")
            await bot.answer_callback_query(
                callback_query.id,
                text="Произошла ошибка при получении розыгрышей."
            )

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

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
            keyboard.button(text="Редактировать пост", callback_data=f"edit_post:{giveaway_id}")
            keyboard.button(text="Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
            keyboard.button(text="Активировать розыгрыш", callback_data=f"activate_giveaway:{giveaway_id}")
            keyboard.button(text="Сообщение победителям", callback_data=f"message_winners:{giveaway_id}")
            keyboard.button(text="Удалить розыгрыш", callback_data=f"delete_giveaway:{giveaway_id}")
            keyboard.button(text="Назад к списку", callback_data="created_giveaways")
            keyboard.adjust(1)

            giveaway_info = f"""
{giveaway['name']}

{giveaway['description']}

Количество победителей: {giveaway['winner_count']}
Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}
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
                        await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info,
                                                        keyboard)
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
                        await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info,
                                                        keyboard)
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

    @dp.callback_query(lambda c: c.data.startswith('edit_post:'))
    async def process_edit_post(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    # Constants for validation
    MAX_NAME_LENGTH = 50
    MAX_DESCRIPTION_LENGTH = 2500
    MAX_MEDIA_SIZE_MB = 5
    MAX_WINNERS = 50

    async def _show_edit_menu(user_id: int, giveaway_id: str, message_id: int = None):
        # Fetch the giveaway data
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        if not response.data:
            await bot.send_message(user_id, "Розыгрыш не найден.")
            return

        giveaway = response.data

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_description:{giveaway_id}")
        keyboard.button(text="🏆 Кол-во победителей", callback_data=f"edit_winner_count:{giveaway_id}")
        keyboard.button(text="🗓 Дата завершения", callback_data=f"change_end_date:{giveaway_id}")
        keyboard.button(text="🖼 Медиа", callback_data=f"manage_media:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(2, 2, 1, 1)

        giveaway_info = f"""
📊 Текущая информация о розыгрыше: 

📝  Название:  {giveaway['name']}
📄  Описание:  {giveaway['description']}

🏆  Количество победителей:  {giveaway['winner_count']}
🗓  Дата завершения:  {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК

🖼  Медиа:  {'Прикреплено' if giveaway['media_type'] else 'Отсутствует'}
"""

        try:
            # Check if giveaway has media
            if giveaway['media_type'] and giveaway['media_file_id']:
                try:
                    if giveaway['media_type'] == 'photo':
                        await bot.edit_message_media(
                            chat_id=user_id,
                            message_id=message_id,
                            media=types.InputMediaPhoto(media=giveaway['media_file_id'], caption=giveaway_info),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'gif':
                        await bot.edit_message_media(
                            chat_id=user_id,
                            message_id=message_id,
                            media=types.InputMediaAnimation(media=giveaway['media_file_id'], caption=giveaway_info),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'video':
                        await bot.edit_message_media(
                            chat_id=user_id,
                            message_id=message_id,
                            media=types.InputMediaVideo(media=giveaway['media_file_id'], caption=giveaway_info),
                            reply_markup=keyboard.as_markup()
                        )
                except aiogram.exceptions.TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logging.warning(f"Message to edit not found: {e}")
                        # If message not found, send a new message
                        if giveaway['media_type'] == 'photo':
                            await bot.send_photo(user_id, photo=giveaway['media_file_id'], caption=giveaway_info,
                                                 reply_markup=keyboard.as_markup())
                        elif giveaway['media_type'] == 'gif':
                            await bot.send_animation(user_id, animation=giveaway['media_file_id'],
                                                     caption=giveaway_info, reply_markup=keyboard.as_markup())
                        elif giveaway['media_type'] == 'video':
                            await bot.send_video(user_id, video=giveaway['media_file_id'], caption=giveaway_info,
                                                 reply_markup=keyboard.as_markup())
                    else:
                        raise
            else:
                # If no media, use the default image
                await send_message_with_image(
                    bot,
                    user_id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=message_id
                )
        except Exception as e:
            logging.error(f"Error in _show_edit_menu: {str(e)}")
            await bot.send_message(
                chat_id=user_id,
                text="Произошла ошибка при отображении меню редактирования. Пожалуйста, попробуйте еще раз."
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_name:'))
    async def process_edit_name(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_name)  # Новое состояние для редактирования названия
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Введите новое название розыгрыша (максимум {MAX_NAME_LENGTH} символов): \n\nТекущее название будет заменено на введенный вами текст.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_description:'))
    async def process_edit_description(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_description)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Введите новое описание розыгрыша (максимум {MAX_DESCRIPTION_LENGTH} символов): \n\nТекущее описание будет заменено на введенный вами текст.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count:'))
    async def process_edit_winner_count(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_winner_count)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Введите новое количество победителей (максимум {MAX_WINNERS} победителей): \n\nВведите положительное целое число.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(GiveawayStates.waiting_for_edit_name)
    async def process_new_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_name = message.text

        # Check name length
        if len(new_name) > MAX_NAME_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Название слишком длинное. Максимальная длина: {MAX_NAME_LENGTH} символов. Текущая длина: {len(new_name)} символов. Пожалуйста, введите более короткое название.",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id']
            )
            return

        try:
            supabase.table('giveaways').update({'name': new_name}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except Exception as e:
            logging.error(f"Error updating giveaway name: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении названия розыгрыша.")

        await state.clear()

    @dp.message(GiveawayStates.waiting_for_edit_description)
    async def process_new_description(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_description = message.text

        # Check description length
        if len(new_description) > MAX_DESCRIPTION_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Описание слишком длинное. Максимальная длина: {MAX_DESCRIPTION_LENGTH} символов. Текущая длина: {len(new_description)} символов. Пожалуйста, введите более короткое описание.",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id']
            )
            return

        try:
            supabase.table('giveaways').update({'description': new_description}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except Exception as e:
            logging.error(f"Error updating giveaway description: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении описания розыгрыша.")

        await state.clear()

    @dp.message(GiveawayStates.waiting_for_edit_winner_count)
    async def process_new_winner_count(message: types.Message, state: FSMContext):
        # Delete user's message first
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        try:
            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Winner count must be positive")

            # Check winner count limit
            if new_winner_count > MAX_WINNERS:
                data = await state.get_data()
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"Слишком много победителей. Максимальное количество: {MAX_WINNERS}. Пожалуйста, введите меньшее число.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

            data = await state.get_data()
            giveaway_id = data['giveaway_id']

            # Show loading message
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Обновление количества победителей...",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

            # Get current winner count for comparison
            current_winner_count_response = supabase.table('giveaways').select('winner_count').eq('id',
                                                                                                  giveaway_id).single().execute()
            current_winner_count = current_winner_count_response.data['winner_count']

            # Update winner count
            supabase.table('giveaways').update({'winner_count': new_winner_count}).eq('id', giveaway_id).execute()

            # Handle congratulations messages
            if new_winner_count > current_winner_count:
                for place in range(current_winner_count + 1, new_winner_count + 1):
                    supabase.table('congratulations').insert({
                        'giveaway_id': giveaway_id,
                        'place': place,
                        'message': f"Поздравляем! Вы заняли {place} место в розыгрыше!"
                    }).execute()
            elif new_winner_count < current_winner_count:
                supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).gte('place',
                                                                                              new_winner_count + 1).execute()

            # Clear state and show edit menu
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            # Get state data for message update
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")

            # Update message with error
            await send_message_with_image(
                bot,
                message.chat.id,
                "Пожалуйста, введите положительное целое число для количества победителей.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )
            # Don't clear state to continue waiting for input

        except Exception as e:
            logging.error(f"Error updating winner count: {str(e)}")
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")

            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении количества победителей. Пожалуйста, попробуйте еще раз.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    async def send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard):
        if giveaway['media_type'] and giveaway['media_file_id']:
            media_type = giveaway['media_type']
            if media_type == 'photo':
                await bot.send_photo(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                     reply_markup=keyboard.as_markup())
            elif media_type == 'gif':
                await bot.send_animation(chat_id, animation=giveaway['media_file_id'],
                                         caption=giveaway_info, reply_markup=keyboard.as_markup())
            elif media_type == 'video':
                await bot.send_video(chat_id, video=giveaway['media_file_id'], caption=giveaway_info,
                                     reply_markup=keyboard.as_markup())
        else:
            await send_message_with_image(bot, chat_id, giveaway_info, reply_markup=keyboard.as_markup())

    @dp.callback_query(lambda c: c.data.startswith('manage_media:'))
    async def process_manage_media(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if giveaway['media_type']:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить медиа файл", callback_data=f"change_media:{giveaway_id}")
            keyboard.button(text="Удалить медиа файл", callback_data=f"delete_media:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")  # Changed this line
            keyboard.adjust(1)

            text = "Выберите действие, которое хотите сделать:"
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Да", callback_data=f"add_media:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")  # Changed this line
            keyboard.adjust(2)

            text = f"Хотите добавить фото, GIF или видео? (максимальный размер файла: {MAX_MEDIA_SIZE_MB} МБ)"

        message = await send_message_with_image(
            bot,
            callback_query.from_user.id,
            text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

        if message:
            await state.update_data(last_bot_message_id=message.message_id)
        else:
            logging.error("Failed to send or update message in process_manage_media")

        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('add_media:') or c.data.startswith('change_media:'))
    async def process_add_or_change_media(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id)
        await state.set_state(GiveawayStates.waiting_for_media_edit)

        data = await state.get_data()
        last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")]
        ])

        message = await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Пожалуйста, отправьте фото, GIF или видео (максимальный размер файла: {MAX_MEDIA_SIZE_MB} МБ).",
            reply_markup=keyboard,
            message_id=last_message_id
        )

        if message:
            await state.update_data(last_bot_message_id=message.message_id)
        else:
            logging.error("Failed to send or update message in process_add_or_change_media")

        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('back_to_edit_menu:'))
    async def process_back_to_edit_menu(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        data = await state.get_data()
        last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id

        await _show_edit_menu(callback_query.from_user.id, giveaway_id, last_message_id)
        await bot.answer_callback_query(callback_query.id)

    @dp.message(GiveawayStates.waiting_for_media_edit)
    async def process_media_edit(message: types.Message, state: FSMContext):
        try:
            data = await state.get_data()
            giveaway_id = data.get('giveaway_id')
            last_message_id = data.get('last_bot_message_id')

            if not giveaway_id:
                await message.reply("Произошла ошибка. Пожалуйста, попробуйте снова.")
                await state.clear()
                return

            # Show loading message
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")]
            ])
            await send_message_with_image(
                bot,
                message.from_user.id,
                "Загрузка...",
                reply_markup=keyboard,
                message_id=last_message_id
            )

            # Process media file
            if message.photo:
                file_id = message.photo[-1].file_id
                media_type = 'photo'
                file_ext = 'jpg'
            elif message.animation:
                file_id = message.animation.file_id
                media_type = 'gif'  # Keep the media type as 'gif' for identification
                file_ext = 'mp4'  # Change the extension to 'mp4' instead of 'gif'
            elif message.video:
                file_id = message.video.file_id
                media_type = 'video'
                file_ext = 'mp4'
            else:
                await message.reply("Пожалуйста, отправьте фото, GIF или видео.")
                return

            # Get file from Telegram
            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)

            # Check file size
            file_size_mb = file.file_size / (1024 * 1024)
            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await send_message_with_image(
                    bot,
                    message.from_user.id,
                    f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ. Текущий размер: {file_size_mb:.2f} МБ. Пожалуйста, отправьте файл меньшего размера.",
                    reply_markup=keyboard,
                    message_id=last_message_id
                )
                return

            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"

            # Upload to Supabase Storage
            response = supabase.storage.from_('pepeg').upload(
                path=filename,
                file=file_content.read(),
                file_options={"content-type": "application/octet-stream"}
            )

            if hasattr(response, 'error') and response.error:
                raise Exception(response.error)

            # Get public URL
            public_url = supabase.storage.from_('pepeg').get_public_url(filename)

            # Update database with new media info
            supabase.table('giveaways').update({
                'media_type': media_type,
                'media_file_id': public_url
            }).eq('id', giveaway_id).execute()

            # Delete user's message with media
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            # Clear state and return to edit menu
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, last_message_id)

        except Exception as e:
            logging.error(f"Error updating media: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении медиа файла.")
            await state.clear()

    @dp.callback_query(lambda c: c.data.startswith('delete_media:'))
    async def process_delete_media(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]

        try:
            # Update the giveaway to remove media
            supabase.table('giveaways').update({
                'media_type': None,
                'media_file_id': None
            }).eq('id', giveaway_id).execute()

            # Get the last message ID from state
            data = await state.get_data()
            last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id

            # Immediately show the updated edit menu
            await _show_edit_menu(callback_query.from_user.id, giveaway_id, last_message_id)

        except Exception as e:
            logging.error(f"Error in process_delete_media: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при удалении медиа файла.")

        finally:
            # Always answer the callback query to prevent the "loading" state on the button
            await bot.answer_callback_query(callback_query.id)

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

            # Delete related records from congratulations table
            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).execute()

            # Delete the giveaway from giveaways table
            response = supabase.table('giveaways').delete().eq('id', giveaway_id).execute()

            if response.data:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="В главное меню", callback_data="back_to_main_menu")
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    "Розыгрыш успешно удален.",
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )
            else:
                raise Exception("No data returned from giveaway deletion")

        except Exception as e:
            logging.error(f"Error deleting giveaway: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В главное меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "Произошла ошибка при удалении розыгрыша.",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )

    @dp.callback_query(lambda c: c.data.startswith('cancel_delete_giveaway:'))
    async def process_cancel_delete_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await process_view_created_giveaway(callback_query)

    @dp.callback_query(lambda c: c.data.startswith('change_end_date:'))
    async def process_change_end_date(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_new_end_time)
        await callback_query.answer()

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
Укажите новую дату завершения розыгрыша в формате ДД.ММ.ГГГГ ЧЧ:ММ

Текущая дата и время:
<code>{current_time}</code>
"""
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            html_message,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_new_end_time)
    async def process_new_end_time(message: types.Message, state: FSMContext):
        # Delete user's message first
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        if message.text.lower() == 'отмена':
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
            return

        try:
            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            moscow_tz = pytz.timezone('Europe/Moscow')
            new_end_time_tz = moscow_tz.localize(new_end_time)

            # Show loading message
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Обновление даты завершения...",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

            # Update the end time
            supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id',
                                                                                             giveaway_id).execute()

            # Clear state and show edit menu
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""
Вы ввели неправильный формат даты. Сообщение удалено.

Пожалуйста, введите дату завершения розыгрыша в формате ДД.ММ.ГГГГ ЧЧ:ММ
текущая дата и время: 
<code>{current_time}</code>
"""
            await send_message_with_image(
                bot,
                message.chat.id,
                html_message,
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
            # Don't clear state to continue waiting for input

        except Exception as e:
            logging.error(f"Error updating end time: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении даты завершения розыгрыша. Пожалуйста, попробуйте еще раз.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    async def get_giveaway_creator(giveaway_id: str) -> int:
        response = supabase.table('giveaways').select('user_id').eq('id', giveaway_id).single().execute()
        if response.data:
            return int(response.data['user_id'])  # Убедимся, что возвращаемое значение — это int
        return -1  # Возвращаем значение по умолчанию

    async def get_bound_communities(user_id: int) -> List[Dict[str, Any]]:
        response = supabase.table('bound_communities').select('*').eq('user_id', user_id).execute()
        return response.data if response.data else []

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username):
        # Get the community details from bound_communities
        response = supabase.table('bound_communities').select('*').eq('community_id', community_id).execute()

        if response.data and len(response.data) > 0:
            # Use the first matching community if multiple exist
            community = response.data[0]
            data = {
                "giveaway_id": giveaway_id,
                "community_id": community_id,
                "community_username": community_username,
                "community_type": community['community_type'],
                "user_id": community['user_id'],
                "community_name": community['community_name']
            }
            supabase.table("giveaway_communities").insert(data).execute()
        else:
            logging.error(f"No community found with ID {community_id}")

    async def unbind_community_from_giveaway(giveaway_id, community_id):
        supabase.table("giveaway_communities").delete().eq("giveaway_id", giveaway_id).eq("community_id",
                                                                                          community_id).execute()

    @dp.callback_query(lambda c: c.data.startswith('bind_communities:'))
    async def process_bind_communities(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        await state.update_data(giveaway_id=giveaway_id)
        await bot.answer_callback_query(callback_query.id)

        # Fetch bound communities for the user
        bound_communities = await get_bound_communities(user_id)

        # Fetch communities already bound to this giveaway
        giveaway_communities = await get_giveaway_communities(giveaway_id)

        # Initialize the user's selected communities
        user_selected_communities[user_id] = {
            'giveaway_id': giveaway_id,
            'communities': set((comm['community_id'], comm['community_username']) for comm in giveaway_communities)
        }

        keyboard = InlineKeyboardBuilder()

        # Add buttons for bound communities
        for community in bound_communities:
            community_id = community['community_id']
            community_username = community['community_username']
            community_name = community['community_name']  # Get community_name
            is_selected = (community_id, community_username) in user_selected_communities[user_id]['communities']
            text = f"{community_name}"  # Use community_name instead of @{community_username}
            if is_selected:
                text += ' ✅'
            keyboard.button(
                text=text,
                callback_data=f"toggle_community:{giveaway_id}:{community_id}:{community_username}"
            )

        # Add buttons for other actions
        keyboard.button(text="Подтвердить выбор", callback_data=f"confirm_community_selection:{giveaway_id}")
        keyboard.button(text="Привязать новый паблик", callback_data=f"bind_new_community:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Выберите паблики для привязки, затем нажмите 'Подтвердить выбор':",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('toggle_community:'))
    async def process_toggle_community(callback_query: types.CallbackQuery):
        _, giveaway_id, community_id, community_username = callback_query.data.split(':')
        user_id = callback_query.from_user.id

        # Modified query to handle multiple results
        try:
            # Get community name from bound_communities - take the first result
            response = supabase.table('bound_communities').select('community_name').eq('community_id',
                                                                                       community_id).execute()
            community_name = response.data[0]['community_name'] if response.data else community_username

            # Ensure user_selected_communities is initialized
            if user_id not in user_selected_communities or user_selected_communities[user_id][
                'giveaway_id'] != giveaway_id:
                user_selected_communities[user_id] = {
                    'giveaway_id': giveaway_id,
                    'communities': set()
                }

            # Find the button that was clicked
            current_text = None
            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        current_text = button.text
                        # Toggle the selection
                        if '✅' in current_text:
                            new_text = f"{community_name}"  # Use community_name
                            user_selected_communities[user_id]['communities'].discard(
                                (community_id, community_username))
                            logging.info(f"Removing community {community_name} from selection")
                        else:
                            new_text = f"{community_name} ✅"  # Use community_name
                            user_selected_communities[user_id]['communities'].add((community_id, community_username))
                            logging.info(f"Adding community {community_name} to selection")
                        new_row.append(InlineKeyboardButton(text=new_text, callback_data=button.callback_data))
                    else:
                        new_row.append(button)
                new_keyboard.append(new_row)

            await bot.edit_message_reply_markup(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard)
            )
            await bot.answer_callback_query(callback_query.id)

        except Exception as e:
            logging.error(f"Error in process_toggle_community: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при обработке запроса.")

    async def get_giveaway_communities(giveaway_id):
        response = supabase.table("giveaway_communities").select("*").eq("giveaway_id", giveaway_id).execute()
        return response.data

    @dp.callback_query(lambda c: c.data.startswith('activate_giveaway:'))
    async def process_activate_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        try:
            response = supabase.table('giveaway_communities').select('community_id', 'community_username',
                                                                     'community_name').eq(
                'giveaway_id',
                giveaway_id).execute()
            communities = response.data

            if not communities:
                await bot.answer_callback_query(callback_query.id,
                                                text="К этому розыгрышу не привязано ни одного сообщества.")
                return

            keyboard = InlineKeyboardBuilder()
            for community in communities:
                keyboard.button(
                    text=f"{community['community_name']}",  # Use community_name
                    callback_data=f"toggle_activate_community:{giveaway_id}:{community['community_id']}:{community['community_username']}"
                )
            keyboard.button(text="Подтвердить выбор", callback_data=f"confirm_activate_selection:{giveaway_id}")
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

    @dp.callback_query(lambda c: c.data.startswith('toggle_activate_community:'))
    async def process_toggle_activate_community(callback_query: types.CallbackQuery):
        _, giveaway_id, community_id, community_username = callback_query.data.split(':')

        try:
            # Get community name - modified to handle multiple results
            response = supabase.table('bound_communities').select('community_name').eq('community_id',
                                                                                       community_id).execute()
            community_name = response.data[0]['community_name'] if response.data else community_username

            # Находим кнопку, на которую нажали
            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        # Переключаем состояние кнопки
                        if '✅' in button.text:
                            new_text = f"{community_name}"  # Use community_name
                        else:
                            new_text = f"{community_name} ✅"  # Use community_name
                        new_row.append(InlineKeyboardButton(text=new_text, callback_data=button.callback_data))
                    else:
                        new_row.append(button)
                new_keyboard.append(new_row)

            await bot.edit_message_reply_markup(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard)
            )
            await bot.answer_callback_query(callback_query.id)

        except Exception as e:
            logging.error(f"Error in process_toggle_activate_community: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при обработке запроса.")

    @dp.callback_query(lambda c: c.data.startswith('confirm_activate_selection:'))
    async def process_confirm_activate_selection(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id

        # Get selected communities with their names
        selected_communities = []
        for row in callback_query.message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data.startswith('toggle_activate_community:') and '✅' in button.text:
                    _, _, community_id, community_username = button.callback_data.split(':')
                    community_name = button.text.replace(' ✅', '')  # Get name from button text
                    selected_communities.append((community_id, community_username, community_name))

        if not selected_communities:
            await bot.answer_callback_query(callback_query.id, text="Выберите хотя бы одно сообщество для публикации.")
            return

        # Save selected communities
        user_selected_communities[user_id] = {
            'giveaway_id': giveaway_id,
            'communities': [(comm[0], comm[1]) for comm in selected_communities]  # Keep original structure
        }

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Активировать розыгрыш", callback_data=f"publish_giveaway:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"activate_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)
        community_names = [comm[2] for comm in selected_communities]  # Use community names
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Розыгрыш будет опубликован в следующих сообществах: {', '.join(community_names)}",
            keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_community_selection:'))
    async def process_confirm_community_selection(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id

        try:
            # Получаем текущие привязанные паблики для этого гивевея
            current_bound_communities = await get_giveaway_communities(giveaway_id)
            current_set = set(
                (str(comm['community_id']), comm['community_username']) for comm in current_bound_communities)

            # Получаем текущее состояние кнопок и их текст
            selected_set = set()
            button_texts = {}  # Словарь для хранения текущего текста кнопок
            for row in callback_query.message.reply_markup.inline_keyboard:
                for button in row:
                    if button.callback_data.startswith('toggle_community:'):
                        _, _, community_id, community_username = button.callback_data.split(':')
                        if '✅' in button.text:
                            selected_set.add((str(community_id), community_username))
                        # Сохраняем текущий текст кнопки
                        button_texts[(str(community_id), community_username)] = button.text

            # Находим паблики для добавления и удаления
            to_add = selected_set - current_set
            to_remove = current_set - selected_set

            changes_made = bool(to_add or to_remove)

            # Если есть изменения, применяем их
            if changes_made:
                # Добавляем новые привязки
                for community_id, community_username in to_add:
                    await bind_community_to_giveaway(giveaway_id, community_id, community_username)
                    logging.info(f"Added binding for community {community_username} to giveaway {giveaway_id}")

                # Удаляем старые привязки
                for community_id, community_username in to_remove:
                    await unbind_community_from_giveaway(giveaway_id, community_id)
                    logging.info(f"Removed binding for community {community_username} from giveaway {giveaway_id}")

            # Обновляем сообщение с сохранением текущего формата кнопок
            bound_communities = await get_bound_communities(user_id)
            giveaway_communities = await get_giveaway_communities(giveaway_id)

            keyboard = InlineKeyboardBuilder()

            for community in bound_communities:
                community_id = community['community_id']
                community_username = community['community_username']

                # Если кнопка уже существовала, используем её текущий текст
                if (str(community_id), community_username) in button_texts:
                    text = button_texts[(str(community_id), community_username)]
                else:
                    # Для новых кнопок используем community_name
                    is_selected = any(str(comm['community_id']) == str(community_id) for comm in giveaway_communities)
                    text = f"{community['community_name']}"
                    if is_selected:
                        text += ' ✅'

                keyboard.button(
                    text=text,
                    callback_data=f"toggle_community:{giveaway_id}:{community_id}:{community_username}"
                )

            keyboard.button(text="Подтвердить выбор", callback_data=f"confirm_community_selection:{giveaway_id}")
            keyboard.button(text="Привязать новый паблик", callback_data=f"bind_new_community:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)

            new_markup = keyboard.as_markup()

            try:
                # Обновляем клавиатуру
                await bot.edit_message_reply_markup(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    reply_markup=new_markup
                )

                if changes_made:
                    await bot.answer_callback_query(callback_query.id, text="Привязки пабликов обновлены!")

                    # Создаем новый объект CallbackQuery для просмотра созданного розыгрыша
                    new_callback_query = types.CallbackQuery(
                        id=callback_query.id,
                        from_user=callback_query.from_user,
                        chat_instance=callback_query.chat_instance,
                        message=callback_query.message,
                        data=f"view_created_giveaway:{giveaway_id}"
                    )

                    # Очищаем выбранные сообщества для этого пользователя
                    if user_id in user_selected_communities:
                        del user_selected_communities[user_id]

                    # Перенаправляем на просмотр созданного розыгрыша только если были изменения
                    await process_view_created_giveaway(new_callback_query)
                else:
                    await bot.answer_callback_query(callback_query.id, text="Список пабликов не изменился.")

            except aiogram.exceptions.TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    # Если сообщение не изменилось, это не ошибка
                    logging.info("Message not modified, as it's identical to the current one.")
                    if changes_made:
                        await bot.answer_callback_query(callback_query.id, text="Привязки пабликов обновлены!")

                        # Создаем новый объект CallbackQuery и перенаправляем только если были изменения
                        new_callback_query = types.CallbackQuery(
                            id=callback_query.id,
                            from_user=callback_query.from_user,
                            chat_instance=callback_query.chat_instance,
                            message=callback_query.message,
                            data=f"view_created_giveaway:{giveaway_id}"
                        )

                        # Очищаем выбранные сообщества для этого пользователя
                        if user_id in user_selected_communities:
                            del user_selected_communities[user_id]

                        await process_view_created_giveaway(new_callback_query)
                    else:
                        await bot.answer_callback_query(callback_query.id, text="Список пабликов не изменился.")
                else:
                    # Если возникла другая ошибка, логируем её
                    logging.error(f"Error updating keyboard: {e}")
                    await bot.answer_callback_query(callback_query.id,
                                                    text="Произошла ошибка при обновлении списка пабликов.")

        except Exception as e:
            logging.error(f"Error in process_confirm_community_selection: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при обработке запроса.")

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
        participant_counter_tasks = []

        # Show loading message first
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Отмена", callback_data=f"activate_giveaway:{giveaway_id}")
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Розыгрыш публикуется...",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

        # Check for selected communities
        user_data = user_selected_communities.get(user_id)
        if not user_data or user_data['giveaway_id'] != giveaway_id or not user_data.get('communities'):
            await bot.answer_callback_query(callback_query.id, text="Ошибка: нет выбранных сообществ для публикации.")
            return

        selected_communities = user_data['communities']

        try:
            # Fetch giveaway information
            giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            giveaway = giveaway_response.data

            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
                return

            # Get current participant count
            participant_count = await get_participant_count(giveaway_id, supabase)

            post_text = f"""
{giveaway['name']}

{giveaway['description']}

Количество победителей: {giveaway['winner_count']}
Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}

Нажмите кнопку ниже, чтобы принять участие!
"""

            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"Участвовать ({participant_count})",
                url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}"
            )
            keyboard.adjust(1)

            success_count = 0
            error_count = 0
            error_messages = []
            published_messages = []

            # Publish to selected communities
            for community_id, community_username in selected_communities:
                try:
                    sent_message = None

                    # Send message based on media type
                    if giveaway['media_type'] and giveaway['media_file_id']:
                        if giveaway['media_type'] == 'photo':
                            sent_message = await bot.send_photo(
                                chat_id=int(community_id),
                                photo=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup()
                            )
                        elif giveaway['media_type'] == 'gif':
                            sent_message = await bot.send_animation(
                                chat_id=int(community_id),
                                animation=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup()
                            )
                        elif giveaway['media_type'] == 'video':
                            sent_message = await bot.send_video(
                                chat_id=int(community_id),
                                video=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup()
                            )
                    else:
                        sent_message = await bot.send_message(
                            chat_id=int(community_id),
                            text=post_text,
                            reply_markup=keyboard.as_markup()
                        )

                    if sent_message:
                        # Save message information
                        published_messages.append({
                            'chat_id': sent_message.chat.id,
                            'message_id': sent_message.message_id
                        })

                        # Save information for participant counter tasks
                        participant_counter_tasks.append({
                            'chat_id': sent_message.chat.id,
                            'message_id': sent_message.message_id
                        })

                        success_count += 1
                    await asyncio.sleep(0.5)  # Add a delay between publishing to communities

                except aiogram.exceptions.TelegramRetryAfter as e:
                    retry_after = e.retry_after
                    logging.warning(f"Hit rate limit. Retrying after {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    try:
                        # Retry sending the message
                        if giveaway['media_type'] == 'photo':
                            sent_message = await bot.send_photo(
                                chat_id=int(community_id),
                                photo=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup()
                            )
                        elif giveaway['media_type'] == 'gif':
                            sent_message = await bot.send_animation(
                                chat_id=int(community_id),
                                animation=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup()
                            )
                        elif giveaway['media_type'] == 'video':
                            sent_message = await bot.send_video(
                                chat_id=int(community_id),
                                video=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup()
                            )
                        else:
                            sent_message = await bot.send_message(
                                chat_id=int(community_id),
                                text=post_text,
                                reply_markup=keyboard.as_markup()
                            )

                        if sent_message:
                            published_messages.append({
                                'chat_id': sent_message.chat.id,
                                'message_id': sent_message.message_id
                            })
                            participant_counter_tasks.append({
                                'chat_id': sent_message.chat.id,
                                'message_id': sent_message.message_id
                            })
                            success_count += 1
                    except Exception as retry_error:
                        error_count += 1
                        error_messages.append(
                            f"Ошибка публикации в @{community_username} после повторной попытки: {str(retry_error)}")
                        logging.error(
                            f"Error publishing to community @{community_username} after retry: {str(retry_error)}")
                except Exception as e:
                    error_count += 1
                    error_messages.append(f"Ошибка публикации в @{community_username}: {str(e)}")
                    logging.error(f"Error publishing to community @{community_username}: {str(e)}")

            # Handle publication results
            if success_count > 0:
                try:
                    # Clear previous winners and participants
                    supabase.table('giveaway_winners').delete().eq('giveaway_id', giveaway_id).execute()
                    supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()

                    # Activate giveaway and set creation time
                    moscow_tz = pytz.timezone('Europe/Moscow')
                    current_time = datetime.now(moscow_tz)

                    # Update the giveaway with the new information
                    supabase.table('giveaways').update({
                        'is_active': True,
                        'created_at': current_time.isoformat(),
                        'published_messages': json.dumps(published_messages),
                        'participant_counter_tasks': json.dumps(participant_counter_tasks)
                    }).eq('id', giveaway_id).execute()

                    # Start the participant counter tasks
                    counter_tasks = []
                    for task_info in participant_counter_tasks:
                        task = asyncio.create_task(
                            start_participant_counter(
                                bot,
                                task_info['chat_id'],
                                task_info['message_id'],
                                giveaway_id,
                                supabase
                            )
                        )
                        counter_tasks.append(task)

                    await bot.answer_callback_query(callback_query.id, text="Розыгрыш опубликован и активирован!")

                    keyboard = InlineKeyboardBuilder()
                    keyboard.button(text="Назад", callback_data="back_to_main_menu")

                    result_message = (
                        f"✅ Розыгрыш успешно опубликован в {success_count} сообществах.\n"
                        "🔄 Счетчик участников будет обновляться каждую минуту."
                    )

                    if error_count > 0:
                        result_message += f"\n\n❌ Ошибки публикации ({error_count}):"
                        for error in error_messages:
                            if "Telegram server says - Forbidden: bot is not a member of the channel chat" in error:
                                community = error.split('@')[1].split(':')[0]
                                result_message += f"\nОшибка публикации в @{community}: В данном паблике бот был удален как администратор или сам паблик удален."
                            else:
                                result_message += f"\n{error}"

                    await send_message_with_image(
                        bot,
                        callback_query.from_user.id,
                        result_message,
                        reply_markup=keyboard.as_markup(),
                        message_id=callback_query.message.message_id
                    )

                except Exception as e:
                    logging.error(f"Error finalizing giveaway activation: {str(e)}")
                    await bot.answer_callback_query(
                        callback_query.id,
                        text="Произошла ошибка при активации розыгрыша."
                    )
            else:
                await bot.answer_callback_query(callback_query.id, text="Не удалось опубликовать розыгрыш.")

                error_keyboard = InlineKeyboardBuilder()
                error_keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")

                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    f"❌ Не удалось опубликовать розыгрыш.\nОшибок: {error_count}\n\nПодробности:\n" +
                    "\n".join(error_messages),
                    reply_markup=error_keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )

        except Exception as e:
            logging.error(f"Error in process_publish_giveaway: {str(e)}")
            await bot.answer_callback_query(
                callback_query.id,
                text="Произошла ошибка при публикации розыгрыша."
            )
        finally:
            # Clear user's temporary data
            user_selected_communities.pop(user_id, None)

    async def get_participant_count(giveaway_id: str, supabase: Client) -> int:
        """Get the current number of participants for a giveaway"""
        try:
            response = supabase.table('participations').select('id').eq('giveaway_id', giveaway_id).execute()
            return len(response.data) if response.data else 0
        except Exception as e:
            logging.error(f"Error getting participant count: {str(e)}")
            return 0

    async def update_participant_button(bot: Bot, chat_id: int, message_id: int, giveaway_id: str, supabase: Client):
        """Update the button text with current participant count"""
        try:
            count = await get_participant_count(giveaway_id, supabase)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"Участвовать ({count})",
                url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}"
            )
            keyboard.adjust(1)

            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logging.error(f"Error updating participant button: {str(e)}")

    async def start_participant_counter(bot: Bot, chat_id: int, message_id: int, giveaway_id: str, supabase: Client):
        """Start periodic updates of participant count"""
        while True:
            await update_participant_button(bot, chat_id, message_id, giveaway_id, supabase)
            await asyncio.sleep(60)

