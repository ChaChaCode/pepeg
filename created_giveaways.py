from typing import List, Dict, Any, Union
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
from aiogram.enums import ChatMemberStatus
import aiogram.exceptions
import json
from postgrest import APIResponse

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


def register_created_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
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
            " Введите новое название розыгрыша: \n\nТекущее название будет заменено на введенный вами текст.",
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
            " Введите новое описание розыгрыша: \n\nТекущее описание будет заменено на введенный вами текст.",
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
            " Введите новое количество победителей: \n\nВведите положительное целое число.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(GiveawayStates.waiting_for_edit_name)
    async def process_new_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_name = message.text

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
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Winner count must be a positive integer")

            current_winner_count_response = supabase.table('giveaways').select('winner_count').eq('id',
                                                                                                  giveaway_id).single().execute()
            current_winner_count = current_winner_count_response.data['winner_count']

            supabase.table('giveaways').update({'winner_count': new_winner_count}).eq('id', giveaway_id).execute()

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

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except ValueError:
            await message.reply("❌ Пожалуйста, введите корректное положительное целое число.")
        except Exception as e:
            logging.error(f"Error updating winner count: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении количества победителей.")

        await state.clear()

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

            text = "Хотите добавить фото, GIF или видео?"

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
            "Пожалуйста, отправьте фото, GIF или видео.",
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
        last_message_id = data.get('last_bot_message_id')

        if not giveaway_id:
            await message.reply("Произошла ошибка. Пожалуйста, попробуйте снова.")
            await state.clear()
            return

        # Обновляем медиа файл в базе данных
        supabase.table('giveaways').update({
            'media_type': media_type,
            'media_file_id': file_id
        }).eq('id', giveaway_id).execute()

        # Удаляем сообщение пользователя с медиа
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        # Очищаем состояние и возвращаемся к меню редактирования
        await state.clear()
        await _show_edit_menu(message.from_user.id, giveaway_id, last_message_id)

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

Текущая дата и время: <code>{current_time}</code>
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
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        if message.text.lower() == 'отмена':
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            return

        try:
            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            moscow_tz = pytz.timezone('Europe/Moscow')
            new_end_time_tz = moscow_tz.localize(new_end_time)

            supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id',
                                                                                             giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
            await state.clear()
        except ValueError:
            # Просто удаляем сообщение пользователя с неверным форматом
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except Exception as e:
            logging.error(f"Error updating end time: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении даты завершения розыгрыша.")
            await state.clear()

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

    async def get_giveaway_communities(giveaway_id: str):
        response = supabase.table('giveaway_communities').select('community_id, community_username').eq('giveaway_id',
                                                                                                        giveaway_id).execute()
        return response.data

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

    @dp.callback_query(lambda c: c.data.startswith('activate_giveaway:'))
    async def process_activate_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            response = supabase.table('giveaway_communities').select('community_id', 'community_username').eq(
                'giveaway_id',
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

    @dp.callback_query(lambda c: c.data.startswith('message_winners:'))
    async def process_message_winners(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]

        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        winner_count = giveaway['winner_count']

        keyboard = InlineKeyboardBuilder()
        for place in range(1, winner_count + 1):
            keyboard.button(text=f"Место {place}", callback_data=f"congrats_message:{giveaway_id}:{place}")
        keyboard.button(text="Общее поздравление", callback_data=f"common_congrats:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        message_text = "Выберите место для редактирования поздравления или общее поздравление для всех победителей."

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('common_congrats:'))
    async def process_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        logging.info(f"Processing common congratulation for giveaway {giveaway_id}")

        try:
            response = supabase.table('congratulations').select('message', 'place').eq('giveaway_id',
                                                                                       giveaway_id).execute()
            logging.info(f"Fetched congratulations: {json.dumps(response.data, default=str)}")

            if not response.data:
                message_text = "В настоящее время поздравления не установлены."
            else:
                congratulations = {item['place']: item['message'] for item in response.data if
                                   'message' in item and 'place' in item}
                logging.info(f"Parsed congratulations: {congratulations}")

                if len(set(congratulations.values())) == 1:
                    common_message = next(iter(congratulations.values()))
                    message_text = f"Текущее общее поздравление:\n\n{common_message}"
                else:
                    message_text = "В настоящее время общее поздравление не установлено. Поздравления различаются для разных мест."

            logging.info(f"Final message_text: {message_text}")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )

        except Exception as e:
            logging.error(f"Error processing common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при обработке общего поздравления.")

        await callback_query.answer()

    def extract_message(obj: Union[str, Dict, List, APIResponse]) -> Union[str, None]:
        if isinstance(obj, APIResponse):
            return extract_message(obj.data)

        if isinstance(obj, str):
            try:
                parsed = json.loads(obj)
                return extract_message(parsed)
            except json.JSONDecodeError:
                return obj.strip()

        if isinstance(obj, dict):
            if 'data' in obj and isinstance(obj['data'], list):
                if obj['data'] and 'message' in obj['data'][0]:
                    return obj['data'][0]['message']
            if 'message' in obj:
                return obj['message']
            for value in obj.values():
                result = extract_message(value)
                if result:
                    return result

        if isinstance(obj, list):
            if obj and isinstance(obj[0], dict) and 'message' in obj[0]:
                return obj[0]['message']

        return None

    @dp.callback_query(lambda c: c.data.startswith('congrats_message:'))
    async def process_congrats_message(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id, place = callback_query.data.split(':')[1:]

        existing_message = None
        try:
            response = supabase.table('congratulations').select('message').eq('giveaway_id', giveaway_id).eq('place',
                                                                                                             place).execute()
            logging.info(f"Supabase response: {json.dumps(response, default=str)}")
            existing_message = extract_message(response)
            logging.info(f"Extracted message: {existing_message}")
        except Exception as e:
            logging.error(f"Error fetching congratulation for place {place}: {str(e)}")

        await state.update_data(giveaway_id=giveaway_id, place=place)
        await state.set_state(GiveawayStates.waiting_for_congrats_message)

        message_text = f"Напишите своё поздравление для победителя, занявшего {place} место."
        if existing_message:
            message_text += f"\n\nТекущее поздравление:\n{existing_message}"
        else:
            message_text += "\n\nТекущее поздравление отсутствует."

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад к выбору мест", callback_data=f"message_winners:{giveaway_id}")

        try:
            sent_message = await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )

            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)
            else:
                logging.error("Failed to send message with image")
        except Exception as e:
            logging.error(f"Error in send_message_with_image: {str(e)}")
            try:
                sent_message = await bot.send_message(
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup()
                )
                await state.update_data(original_message_id=sent_message.message_id)
            except Exception as e:
                logging.error(f"Error sending fallback message: {str(e)}")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_congrats_message)
    async def save_congrats_message(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        place = data['place']
        original_message_id = data.get('original_message_id')

        try:
            # Save the new congratulation message
            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).eq('place', place).execute()
            supabase.table('congratulations').insert({
                'giveaway_id': giveaway_id,
                'place': place,
                'message': message.text
            }).execute()

            await state.clear()

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад к выбору мест", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            updated_text = f"Поздравление для {place} места обновлено:\n\n{message.text}"

            if original_message_id:
                try:
                    # Try to edit the caption (for messages with images)
                    await bot.edit_message_caption(
                        chat_id=message.chat.id,
                        message_id=original_message_id,
                        caption=updated_text,
                        reply_markup=keyboard.as_markup()
                    )
                except Exception as edit_error:
                    logging.error(f"Error editing message: {str(edit_error)}")
                    # If editing fails, send a new message
                    await send_message_with_image(
                        bot,
                        message.chat.id,
                        updated_text,
                        reply_markup=keyboard.as_markup()
                    )
            else:
                # If we don't have the original message ID, send a new message
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    updated_text,
                    reply_markup=keyboard.as_markup()
                )

            # Delete the user's message
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        except Exception as e:
            logging.error(f"Error saving congratulation message: {str(e)}")
            await message.reply("Произошла ошибка при сохранении поздравления. Пожалуйста, попробуйте еще раз.")

    @dp.callback_query(lambda c: c.data == 'show_common_congrats')
    async def show_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            response = supabase.table('congratulations').select('message', 'place').eq('giveaway_id',
                                                                                       giveaway_id).execute()
            logging.info(f"Fetched congratulations: {json.dumps(response.data, default=str)}")

            if not response.data:
                message_text = "В настоящее время поздравления не установлены."
            else:
                congratulations = {item['place']: item['message'] for item in response.data if
                                   'message' in item and 'place' in item}
                logging.info(f"Parsed congratulations: {congratulations}")

                if len(set(congratulations.values())) == 1:
                    common_message = next(iter(congratulations.values()))
                    message_text = f"Текущее общее поздравление:\n\n{common_message}"
                else:
                    message_text = "В настоящее время общее поздравление не установлено. Поздравления различаются для разных мест."

            logging.info(f"Final message_text: {message_text}")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup()
            )

        except Exception as e:
            logging.error(f"Error fetching common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при получении общего поздравления.")

        await callback_query.answer()

    @dp.callback_query(lambda c: c.data.startswith('edit_common_congrats:'))
    async def edit_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        logging.info(f"Editing common congratulation for giveaway {giveaway_id}")

        try:
            response = supabase.table('congratulations').select('message').eq('giveaway_id', giveaway_id).execute()
            existing_messages = [item['message'] for item in response.data if 'message' in item]

            if existing_messages and len(set(existing_messages)) == 1:
                existing_message = existing_messages[0]
            else:
                existing_message = None

            await state.update_data(giveaway_id=giveaway_id)
            await state.set_state(GiveawayStates.waiting_for_common_congrats_message)

            message_text = "Напишите общее поздравление для всех победителей."
            if existing_message:
                message_text += f"\n\nТекущее общее поздравление:\n{existing_message}"

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Отмена", callback_data=f"common_congrats:{giveaway_id}")

            sent_message = await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )

            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)

        except Exception as e:
            logging.error(f"Error preparing to edit common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при подготовке к редактированию общего поздравления.")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_common_congrats_message)
    async def save_common_congrats_message(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        original_message_id = data.get('original_message_id')

        try:
            giveaway_response = supabase.table('giveaways').select('winner_count').eq('id',
                                                                                      giveaway_id).single().execute()
            winner_count = giveaway_response.data['winner_count']

            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).execute()

            congratulations = []
            for place in range(1, winner_count + 1):
                congratulations.append({
                    'giveaway_id': giveaway_id,
                    'place': place,
                    'message': message.text
                })

            supabase.table('congratulations').insert(congratulations).execute()

            await state.clear()

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            success_message = (
                "Общее поздравление сохранено и применено ко всем местам в розыгрыше.\n"
                f"Обновлено поздравлений: {winner_count} мест."
            )

            if original_message_id:
                try:
                    # Попытка отредактировать подпись (для сообщений с изображениями)
                    await bot.edit_message_caption(
                        chat_id=message.chat.id,
                        message_id=original_message_id,
                        caption=success_message,
                        reply_markup=keyboard.as_markup()
                    )
                except Exception as edit_error:
                    logging.error(f"Error editing message: {str(edit_error)}")
                    # Если редактирование не удалось, отправляем новое сообщение
                    await send_message_with_image(
                        bot,
                        message.chat.id,
                        success_message,
                        reply_markup=keyboard.as_markup()
                    )
            else:
                # Если у нас нет ID оригинального сообщения, отправляем новое сообщение
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    success_message,
                    reply_markup=keyboard.as_markup()
                )

            # Удаляем сообщение пользователя
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        except Exception as e:
            logging.error(f"Error saving common congratulation message: {str(e)}")
            await message.reply("Произошла ошибка при сохранении поздравлений. Пожалуйста, попробуйте еще раз.")

