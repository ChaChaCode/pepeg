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
import boto3
from botocore.client import Config
import requests
import re
from aiogram.types import CallbackQuery

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Bot configuration and initialization
BOT_TOKEN = '7924714999:AAFUbKWC--s-ff2DKe6g5Sk1C2Z7yl7hh0c'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

# Yandex Cloud S3 configuration
YANDEX_ACCESS_KEY = 'YCAJEDluWSn-XI0tyGyfwfnVL'
YANDEX_SECRET_KEY = 'YCPkR9H9Ucebg6L6eMGvtfKuFIcO_MK7gyiffY6H'
YANDEX_BUCKET_NAME = 'raffle'
YANDEX_ENDPOINT_URL = 'https://storage.yandexcloud.net'
YANDEX_REGION = 'ru-central1'

# Initialize S3 client for Yandex Cloud
s3_client = boto3.client(
    's3',
    region_name=YANDEX_REGION,
    aws_access_key_id=YANDEX_ACCESS_KEY,
    aws_secret_access_key=YANDEX_SECRET_KEY,
    endpoint_url=YANDEX_ENDPOINT_URL,
    config=Config(signature_version='s3v4')
)

user_selected_communities = {}
paid_users: Dict[int, str] = {}

# Constraints
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 5
MAX_WINNERS = 50
FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Цитата: текст
- Моноширинный: текст
- Скрытый (спойлер): <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a></blockquote>
"""

def strip_html_tags(text):
    """Удаляет HTML-теги из текста, оставляя только видимую часть."""
    # Регулярное выражение для удаления всех HTML-тегов
    clean_text = re.sub(r'<[^>]+>', '', text)
    return clean_text

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
    waiting_for_invite_quantity = State()


async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ"

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        # Use requests library for direct upload instead of boto3 to avoid hash mismatch
        try:
            # Generate a presigned URL for upload
            presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': YANDEX_BUCKET_NAME,
                    'Key': unique_filename,
                    'ContentType': 'application/octet-stream'
                },
                ExpiresIn=3600
            )

            # Upload using requests
            response = requests.put(
                presigned_url,
                data=file_content,  # Use the raw bytes directly
                headers={'Content-Type': 'application/octet-stream'}
            )

            if response.status_code == 200:
                # Generate public URL for the uploaded file
                public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"
                logging.info(f"File uploaded successfully: {unique_filename}")
                logging.info(f"Public URL: {public_url}")
                return True, public_url
            else:
                logging.error(f"Failed to upload using presigned URL: {response.status_code} - {response.text}")
                raise Exception(f"Failed to upload using presigned URL: {response.status_code}")

        except Exception as upload_error:
            logging.error(f"Upload error: {str(upload_error)}")
            raise Exception(f"Failed to upload file: {str(upload_error)}")

    except Exception as e:
        error_msg = str(e)
        logging.error(f"Storage upload error: {error_msg}")
        return False, error_msg


def register_created_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'created_giveaways' or c.data.startswith('created_giveaways_page:'))
    async def process_created_giveaways(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = 1
        if callback_query.data.startswith('created_giveaways_page:'):
            current_page = int(callback_query.data.split(':')[1])

        try:
            response = supabase.table('giveaways').select('*').eq('user_id', user_id).eq('is_active', False).execute()

            if not response.data:
                await bot.answer_callback_query(callback_query.id, text="У вас нет созданных розыгрышей.")
                return

            total_giveaways = len(response.data)
            total_pages = math.ceil(total_giveaways / ITEMS_PER_PAGE)

            start_idx = (current_page - 1) * ITEMS_PER_PAGE
            end_idx = start_idx + ITEMS_PER_PAGE
            current_giveaways = response.data[start_idx:end_idx]

            keyboard = InlineKeyboardBuilder()
            for giveaway in current_giveaways:
                # Очищаем название от HTML-тегов для отображения в кнопке
                clean_name = strip_html_tags(giveaway['name'])
                # Ограничиваем длину текста кнопки, если нужно (например, до 64 символов)
                if len(clean_name) > 64:
                    clean_name = clean_name[:61] + "..."
                keyboard.row(types.InlineKeyboardButton(
                    text=clean_name,
                    callback_data=f"view_created_giveaway:{giveaway['id']}"
                ))

            nav_buttons = []
            if current_page > 1:
                nav_buttons.append(types.InlineKeyboardButton(
                    text="←",
                    callback_data=f"created_giveaways_page:{current_page - 1}"
                ))
            if total_pages > 1:
                nav_buttons.append(types.InlineKeyboardButton(
                    text=f"{current_page}/{total_pages}",
                    callback_data="ignore"
                ))
            if current_page < total_pages:
                nav_buttons.append(types.InlineKeyboardButton(
                    text="→",
                    callback_data=f"created_giveaways_page:{current_page + 1}"
                ))

            if nav_buttons:
                keyboard.row(*nav_buttons)
            keyboard.row(types.InlineKeyboardButton(
                text="Назад",
                callback_data="back_to_main_menu"
            ))

            await bot.answer_callback_query(callback_query.id)
            message_text = f"Выберите розыгрыш для просмотра" + (
                f" (Страница {current_page} из {total_pages}):" if total_pages > 1 else ":")
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        except Exception as e:
            logging.error(f"Error in process_created_giveaways: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении розыгрышей.")

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

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Редактировать пост", callback_data=f"edit_post:{giveaway_id}")
            keyboard.button(text="Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
            keyboard.button(text="Опубликовать розыгрыш", callback_data=f"activate_giveaway:{giveaway_id}")
            keyboard.button(text="Задание пригласить друга", callback_data=f"add_invite_task:{giveaway_id}")
            keyboard.button(text="Сообщение победителям", callback_data=f"message_winners:{giveaway_id}")
            keyboard.button(text="Удалить розыгрыш", callback_data=f"delete_giveaway:{giveaway_id}")
            keyboard.button(text="Назад к списку", callback_data="created_giveaways")
            keyboard.adjust(1)

            invite_info = ""
            if giveaway.get('invite', False):
                invite_info = f"\nТребуется пригласить: {giveaway['quantity_invite']} друзей"

            # Формируем текст с сохранением HTML-форматирования
            giveaway_info = f"""
{giveaway['name']}

{giveaway['description']}

<b>Количество победителей:</b> {giveaway['winner_count']}
<b>Дата завершения:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}
{invite_info}
"""

            try:
                await bot.answer_callback_query(callback_query.id)
            except aiogram.exceptions.TelegramBadRequest as e:
                if "query is too old" in str(e):
                    logging.warning(f"Callback query is too old: {e}")
                else:
                    raise

            if giveaway['media_type'] and giveaway['media_file_id']:
                try:
                    if giveaway['media_type'] == 'photo':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaPhoto(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'gif':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaAnimation(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'video':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaVideo(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                except aiogram.exceptions.TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logging.warning(f"Message to edit not found: {e}")
                        await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info,
                                                        keyboard)
                    else:
                        raise
            else:
                try:
                    await send_message_with_image(
                        bot,
                        callback_query.from_user.id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=callback_query.message.message_id,
                        parse_mode='HTML'  # Добавляем поддержку HTML
                    )
                except aiogram.exceptions.TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logging.warning(f"Message to edit not found: {e}")
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
            await bot.send_message(
                chat_id=callback_query.from_user.id,
                text="Произошла ошибка при получении информации о розыгрыше. Пожалуйста, попробуйте еще раз."
            )

    @dp.callback_query(lambda c: c.data.startswith('add_invite_task:'))
    async def process_add_invite_task(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        response = supabase.table('giveaways').select('invite', 'quantity_invite').eq('id',
                                                                                      giveaway_id).single().execute()
        giveaway = response.data

        keyboard = InlineKeyboardBuilder()

        if giveaway.get('invite', False):
            # Если задание уже активировано
            keyboard.button(text="Изменить количество приглашений",
                            callback_data=f"change_invite_quantity:{giveaway_id}")
            keyboard.button(text="Убрать задание", callback_data=f"remove_invite_task:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)

            message_text = f"Задание 'Пригласить друга' активировано.\nЧтобы пользователь мог участвовать, ему нужно пригласить {giveaway['quantity_invite']} друга(ов)."
        else:
            # Если задание еще не добавлено
            keyboard.button(text="Да", callback_data=f"confirm_invite_task:{giveaway_id}")
            keyboard.button(text="Отмена", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(2)
            message_text = "Хотите ли добавить дополнительное задание приглашение друзей для участия в розыгрыше?"

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_invite_task:'))
    async def process_confirm_invite_task(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_invite_quantity)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Введите число сколько пользователь должен пригласить друзей для того чтобы он смог участвовать:",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('change_invite_quantity:'))
    async def process_change_invite_quantity(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_invite_quantity)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Введите новое число сколько пользователь должен пригласить друзей для участия:",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('remove_invite_task:'))
    async def process_remove_invite_task(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]

        try:
            supabase.table('giveaways').update({
                'invite': False,
                'quantity_invite': 0
            }).eq('id', giveaway_id).execute()

            await bot.answer_callback_query(callback_query.id, text="Задание 'Пригласить друга' удалено.")
            new_callback_query = types.CallbackQuery(
                id=callback_query.id,
                from_user=callback_query.from_user,
                chat_instance=callback_query.chat_instance,
                message=callback_query.message,
                data=f"view_created_giveaway:{giveaway_id}"
            )
            await process_view_created_giveaway(new_callback_query)
        except Exception as e:
            logging.error(f"Error removing invite task: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при удалении задания.")

    @dp.message(GiveawayStates.waiting_for_invite_quantity)
    async def process_invite_quantity(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']

        try:
            quantity = int(message.text)
            if quantity <= 0:
                raise ValueError("Количество должно быть положительным")

            supabase.table('giveaways').update({
                'invite': True,
                'quantity_invite': quantity
            }).eq('id', giveaway_id).execute()

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад к розыгрышу", callback_data=f"view_created_giveaway:{giveaway_id}")

            await send_message_with_image(
                bot,
                message.from_user.id,
                f"Вы успешно добавили/изменили задание 'Пригласить друга'. Теперь для участия пользователь должен пригласить {quantity} друга(ов) (и подписаться на ваши привязанные каналы).",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )

            await state.clear()

        except ValueError:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.from_user.id,
                "Пожалуйста, введите положительное целое число",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_post:'))
    async def process_edit_post(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    async def _show_edit_menu(user_id: int, giveaway_id: str, message_id: int = None):
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

            invite_info = ""
            if giveaway.get('invite', False):
                invite_info = f"\n👥 Требуется пригласить: {giveaway['quantity_invite']} друзей"

            # Обновляем отображение с поддержкой HTML
            giveaway_info = f"""
📊 <b>Текущая информация о розыгрыше:</b>

📝 <b>Название:</b> {giveaway['name']}
📄 <b>Описание:</b> {giveaway['description']}

🏆 <b>Количество победителей:</b> {giveaway['winner_count']}
🗓 <b>Дата завершения:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} по МСК

🖼 <b>Медиа:</b> {'Прикреплено' if giveaway['media_type'] else 'Отсутствует'}
{invite_info}
    """

            try:
                if giveaway['media_type'] and giveaway['media_file_id']:
                    if giveaway['media_type'] == 'photo':
                        await bot.edit_message_media(
                            chat_id=user_id,
                            message_id=message_id,
                            media=types.InputMediaPhoto(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'gif':
                        await bot.edit_message_media(
                            chat_id=user_id,
                            message_id=message_id,
                            media=types.InputMediaAnimation(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'video':
                        await bot.edit_message_media(
                            chat_id=user_id,
                            message_id=message_id,
                            media=types.InputMediaVideo(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                else:
                    await send_message_with_image(
                        bot,
                        user_id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,
                        parse_mode='HTML'  # Добавляем поддержку HTML
                    )
            except Exception as e:
                logging.error(f"Error in _show_edit_menu: {str(e)}")
                await bot.send_message(
                    chat_id=user_id,
                    text="Произошла ошибка при отображении меню редактирования. Пожалуйста, попробуйте еще раз.",
                    parse_mode='HTML'
                )

    @dp.callback_query(lambda c: c.data.startswith('edit_name:'))
    async def process_edit_name(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_name)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        message_text = f"Введите новое название розыгрыша (максимум {MAX_NAME_LENGTH} символов):\n{FORMATTING_GUIDE}"
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_description:'))
    async def process_edit_description(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_description)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        message_text = f"Введите новое описание розыгрыша (максимум {MAX_DESCRIPTION_LENGTH} символов):\n{FORMATTING_GUIDE}"
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
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
        # Получаем HTML-форматированный текст
        new_name = message.html_text if message.text else ""

        if len(new_name) > MAX_NAME_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Название слишком длинное. Максимальная длина: {MAX_NAME_LENGTH} символов. Текущая длина: {len(new_name)} символов. Пожалуйста, введите более короткое название.\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        try:
            supabase.table('giveaways').update({'name': new_name}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except Exception as e:
            logging.error(f"Error updating giveaway name: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении названия розыгрыша.",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
        await state.clear()

    @dp.message(GiveawayStates.waiting_for_edit_description)
    async def process_new_description(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        # Получаем HTML-форматированный текст
        new_description = message.html_text if message.text else ""

        if len(new_description) > MAX_DESCRIPTION_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Описание слишком длинное. Максимальная длина: {MAX_DESCRIPTION_LENGTH} символов. Текущая длина: {len(new_description)} символов. Пожалуйста, введите более короткое описание.\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        try:
            supabase.table('giveaways').update({'description': new_description}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except Exception as e:
            logging.error(f"Error updating giveaway description: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении описания розыгрыша.",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
        await state.clear()

    @dp.message(GiveawayStates.waiting_for_edit_winner_count)
    async def process_new_winner_count(message: types.Message, state: FSMContext):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        try:
            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Winner count must be positive")

            if new_winner_count > MAX_WINNERS:
                data = await state.get_data()
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"Слишком много победителей. Максимальное количество: {MAX_WINNERS}.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

            data = await state.get_data()
            giveaway_id = data['giveaway_id']

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Обновление количества победителей...",
                message_id=data.get('last_message_id'),
            )

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

            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Пожалуйста, введите положительное целое число для количества победителей.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logging.error(f"Error updating winner count: {str(e)}")
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении количества победителей.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    async def send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard):
        if giveaway['media_type'] and giveaway['media_file_id']:
            if giveaway['media_type'] == 'photo':
                await bot.send_photo(
                    chat_id,
                    giveaway['media_file_id'],
                    caption=giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'  # Добавляем поддержку HTML
                )
            elif giveaway['media_type'] == 'gif':
                await bot.send_animation(
                    chat_id,
                    animation=giveaway['media_file_id'],
                    caption=giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'  # Добавляем поддержку HTML
                )
            elif giveaway['media_type'] == 'video':
                await bot.send_video(
                    chat_id,
                    video=giveaway['media_file_id'],
                    caption=giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'  # Добавляем поддержку HTML
                )
        else:
            await send_message_with_image(
                bot,
                chat_id,
                giveaway_info,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'  # Добавляем поддержку HTML
            )

    @dp.callback_query(lambda c: c.data.startswith('manage_media:'))
    async def process_manage_media(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if giveaway['media_type']:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить медиа файл", callback_data=f"change_media:{giveaway_id}")
            keyboard.button(text="Удалить медиа файл", callback_data=f"delete_media:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")
            keyboard.adjust(1)
            text = "Выберите действие, которое хотите сделать:"
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Да", callback_data=f"add_media:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")
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

            if message.photo:
                file_id = message.photo[-1].file_id
                media_type = 'photo'
                file_ext = 'jpg'
            elif message.animation:
                file_id = message.animation.file_id
                media_type = 'gif'
                file_ext = 'gif'
            elif message.video:
                file_id = message.video.file_id
                media_type = 'video'
                file_ext = 'mp4'
            else:
                await message.reply("Пожалуйста, отправьте фото, GIF или видео.")
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)

            file_size_mb = file.file_size / (1024 * 1024)
            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await send_message_with_image(
                    bot,
                    message.from_user.id,
                    f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ.",
                    reply_markup=keyboard,
                    message_id=last_message_id
                )
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(f"Failed to upload to storage: {result}")

            supabase.table('giveaways').update({
                'media_type': media_type,
                'media_file_id': result
            }).eq('id', giveaway_id).execute()

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
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
            supabase.table('giveaways').update({
                'media_type': None,
                'media_file_id': None
            }).eq('id', giveaway_id).execute()
            data = await state.get_data()
            last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id
            await _show_edit_menu(callback_query.from_user.id, giveaway_id, last_message_id)
        except Exception as e:
            logging.error(f"Error in process_delete_media: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при удалении медиа файла.")
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
            supabase.table('giveaway_communities').delete().eq('giveaway_id', giveaway_id).execute()
            supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()
            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).execute()
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

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Обновление даты завершения...",
                message_id=data.get('last_message_id'),
            )

            supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id',
                                                                                             giveaway_id).execute()
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
        except Exception as e:
            logging.error(f"Error updating end time: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении даты завершения розыгрыша.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    async def get_giveaway_creator(giveaway_id: str) -> int:
        response = supabase.table('giveaways').select('user_id').eq('id', giveaway_id).single().execute()
        return int(response.data['user_id']) if response.data else -1

    async def get_bound_communities(user_id: int) -> List[Dict[str, Any]]:
        response = supabase.table('bound_communities').select('*').eq('user_id', user_id).execute()
        return response.data if response.data else []

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username):
        try:
            response = supabase.table('bound_communities').select('*').eq('community_id', community_id).execute()
            if not response.data:
                logging.error(f"No community found with ID {community_id}")
                return False

            community = response.data[0]
            actual_username = community_username if community_username != 'id' else (
                        community.get('community_username') or community.get('community_name'))

            data = {
                "giveaway_id": giveaway_id,
                "community_id": community_id,
                "community_username": actual_username,
                "community_type": community['community_type'],
                "user_id": community['user_id'],
                "community_name": community['community_name']
            }
            supabase.table("giveaway_communities").insert(data).execute()
            return True
        except Exception as e:
            logging.error(f"Error in bind_community_to_giveaway: {str(e)}")
            return False

    async def unbind_community_from_giveaway(giveaway_id, community_id):
        supabase.table("giveaway_communities").delete().eq("giveaway_id", giveaway_id).eq("community_id",
                                                                                          community_id).execute()

    @dp.callback_query(lambda c: c.data == 'bind_communities:' or c.data.startswith('bind_communities:'))
    async def process_bind_communities(callback_query: types.CallbackQuery, state: FSMContext):
        if callback_query.data == 'bind_communities:':
            await bot.answer_callback_query(callback_query.id, text="Неверный формат данных")
            return

        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        await state.update_data(giveaway_id=giveaway_id)
        await bot.answer_callback_query(callback_query.id)

        bound_communities = await get_bound_communities(user_id)
        giveaway_communities = await get_giveaway_communities(giveaway_id)

        user_selected_communities[user_id] = {
            'giveaway_id': giveaway_id,
            'communities': set((comm['community_id'], comm['community_username']) for comm in giveaway_communities)
        }

        keyboard = InlineKeyboardBuilder()
        for community in bound_communities:
            community_id = community['community_id']
            community_username = community['community_username']
            community_name = community['community_name']
            is_selected = (community_id, community_username) in user_selected_communities[user_id]['communities']

            display_name = truncate_name(community_name)
            text = f"{display_name}" + (' ✅' if is_selected else '')

            callback_data = f"toggle_community:{giveaway_id}:{community_id}:{community_username}"
            if len(callback_data.encode('utf-8')) > 60:
                callback_data = f"toggle_community:{giveaway_id}:{community_id}:id"

            keyboard.button(text=text, callback_data=callback_data)

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

    def truncate_name(name, max_length=20):
        return name if len(name) <= max_length else name[:max_length - 3] + "..."

    @dp.callback_query(lambda c: c.data.startswith('toggle_community:'))
    async def process_toggle_community(callback_query: types.CallbackQuery):
        user_id = callback_query.from_user.id
        parts = callback_query.data.split(':')
        if len(parts) >= 4:
            _, giveaway_id, community_id, community_username = parts
        else:
            await bot.answer_callback_query(callback_query.id, text="Invalid callback data")
            return

        try:
            response = supabase.table('bound_communities').select('community_name').eq('community_id',
                                                                                       community_id).execute()
            community_name = response.data[0]['community_name'] if response.data else community_username

            if user_id not in user_selected_communities or user_selected_communities[user_id][
                'giveaway_id'] != giveaway_id:
                user_selected_communities[user_id] = {'giveaway_id': giveaway_id, 'communities': set()}

            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        if '✅' in button.text:
                            new_text = f"{truncate_name(community_name)}"
                            user_selected_communities[user_id]['communities'].discard(
                                (community_id, community_username))
                        else:
                            new_text = f"{truncate_name(community_name)} ✅"
                            user_selected_communities[user_id]['communities'].add((community_id, community_username))
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
                                                                     'community_name').eq('giveaway_id',
                                                                                          giveaway_id).execute()
            communities = response.data

            if not communities:
                await bot.answer_callback_query(callback_query.id,
                                                text="К этому розыгрышу не привязано ни одного сообщества.")
                return

            keyboard = InlineKeyboardBuilder()
            for community in communities:
                display_name = truncate_name(community['community_name'])
                callback_data = f"toggle_activate_community:{giveaway_id}:{community['community_id']}:{community['community_username']}"
                if len(callback_data.encode('utf-8')) > 60:
                    callback_data = f"toggle_activate_community:{giveaway_id}:{community['community_id']}:id"
                keyboard.button(text=display_name, callback_data=callback_data)
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
            response = supabase.table('bound_communities').select('community_name').eq('community_id',
                                                                                       community_id).execute()
            community_name = response.data[0]['community_name'] if response.data else community_username

            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        new_text = f"{community_name}" if '✅' in button.text else f"{community_name} ✅"
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

        selected_communities = []
        for row in callback_query.message.reply_markup.inline_keyboard:
            for button in row:
                if button.callback_data.startswith('toggle_activate_community:') and '✅' in button.text:
                    _, _, community_id, community_username = button.callback_data.split(':')
                    community_name = button.text.replace(' ✅', '')
                    selected_communities.append((community_id, community_username, community_name))

        if not selected_communities:
            await bot.answer_callback_query(callback_query.id, text="Выберите хотя бы одно сообщество для публикации.")
            return

        user_selected_communities[user_id] = {
            'giveaway_id': giveaway_id,
            'communities': [(comm[0], comm[1]) for comm in selected_communities]
        }

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Активировать розыгрыш", callback_data=f"publish_giveaway:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"activate_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)
        community_names = [comm[2] for comm in selected_communities]
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
            current_bound_communities = await get_giveaway_communities(giveaway_id)
            current_set = set(
                (str(comm['community_id']), comm['community_username']) for comm in current_bound_communities)

            selected_set = set()
            for row in callback_query.message.reply_markup.inline_keyboard:
                for button in row:
                    if button.callback_data.startswith('toggle_community:') and '✅' in button.text:
                        parts = button.callback_data.split(':')
                        if len(parts) >= 3:
                            community_id = parts[2]
                            response = supabase.table('bound_communities').select('*').eq('community_id',
                                                                                          community_id).execute()
                            if response.data:
                                community = response.data[0]
                                community_username = community.get('community_username') or community.get(
                                    'community_name')
                                selected_set.add((str(community_id), community_username))

            to_add = selected_set - current_set
            to_remove = current_set - selected_set

            changes_made = bool(to_add or to_remove)

            if changes_made:
                for community_id, community_username in to_add:
                    success = await bind_community_to_giveaway(giveaway_id, community_id, community_username)
                    if not success:
                        logging.error(f"Failed to add binding for community {community_username}")
                for community_id, _ in to_remove:
                    await unbind_community_from_giveaway(giveaway_id, community_id)

                await bot.answer_callback_query(callback_query.id, text="Привязки пабликов обновлены!")
                new_callback_query = types.CallbackQuery(
                    id=callback_query.id,
                    from_user=callback_query.from_user,
                    chat_instance=callback_query.chat_instance,
                    message=callback_query.message,
                    data=f"view_created_giveaway:{giveaway_id}"
                )
                if user_id in user_selected_communities:
                    del user_selected_communities[user_id]
                await process_view_created_giveaway(new_callback_query)
            else:
                await bot.answer_callback_query(callback_query.id, text="Список пабликов не изменился")
        except Exception as e:
            logging.error(f"Error in process_confirm_community_selection: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при обновлении списка пабликов.")

    @dp.callback_query(lambda c: c.data.startswith('publish_giveaway:'))
    async def process_publish_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        participant_counter_tasks = []

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Отмена", callback_data=f"activate_giveaway:{giveaway_id}")
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "Розыгрыш публикуется...",
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

        user_data = user_selected_communities.get(user_id)
        if not user_data or user_data['giveaway_id'] != giveaway_id or not user_data.get('communities'):
            await bot.answer_callback_query(callback_query.id, text="Ошибка: нет выбранных сообществ для публикации.")
            return

        selected_communities = user_data['communities']

        try:
            giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            giveaway = giveaway_response.data

            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
                return

            participant_count = await get_participant_count(giveaway_id, supabase)

            invite_text = ""
            if giveaway.get('invite', False):
                invite_text = f"\nДля участия нужно пригласить {giveaway['quantity_invite']} друзей"

            # Формируем пост с сохранением HTML-форматирования
            post_text = f"""
{giveaway['name']}

{giveaway['description']}

<b>Количество победителей:</b> {giveaway['winner_count']}
<b>Дата завершения:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}
{invite_text}

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

            for community_id, community_username in selected_communities:
                try:
                    sent_message = None
                    if giveaway['media_type'] and giveaway['media_file_id']:
                        if giveaway['media_type'] == 'photo':
                            sent_message = await bot.send_photo(
                                chat_id=int(community_id),
                                photo=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            )
                        elif giveaway['media_type'] == 'gif':
                            sent_message = await bot.send_animation(
                                chat_id=int(community_id),
                                animation=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            )
                        elif giveaway['media_type'] == 'video':
                            sent_message = await bot.send_video(
                                chat_id=int(community_id),
                                video=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            )
                    else:
                        sent_message = await bot.send_message(
                            chat_id=int(community_id),
                            text=post_text,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'  # Добавляем поддержку HTML
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
                    await asyncio.sleep(0.5)

                except aiogram.exceptions.TelegramRetryAfter as e:
                    retry_after = e.retry_after
                    logging.warning(f"Hit rate limit. Retrying after {retry_after} seconds.")
                    await asyncio.sleep(retry_after)
                    try:
                        if giveaway['media_type'] == 'photo':
                            sent_message = await bot.send_photo(
                                chat_id=int(community_id),
                                photo=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            )
                        elif giveaway['media_type'] == 'gif':
                            sent_message = await bot.send_animation(
                                chat_id=int(community_id),
                                animation=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            )
                        elif giveaway['media_type'] == 'video':
                            sent_message = await bot.send_video(
                                chat_id=int(community_id),
                                video=giveaway['media_file_id'],
                                caption=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
                            )
                        else:
                            sent_message = await bot.send_message(
                                chat_id=int(community_id),
                                text=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'  # Добавляем поддержку HTML
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
                except Exception as e:
                    error_count += 1
                    error_messages.append(f"Ошибка публикации в @{community_username}: {str(e)}")

            if success_count > 0:
                try:
                    supabase.table('giveaway_winners').delete().eq('giveaway_id', giveaway_id).execute()
                    supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()

                    moscow_tz = pytz.timezone('Europe/Moscow')
                    current_time = datetime.now(moscow_tz)

                    supabase.table('giveaways').update({
                        'is_active': True,
                        'created_at': current_time.isoformat(),
                        'published_messages': json.dumps(published_messages),
                        'participant_counter_tasks': json.dumps(participant_counter_tasks)
                    }).eq('id', giveaway_id).execute()

                    counter_tasks = []
                    for task_info in participant_counter_tasks:
                        task = asyncio.create_task(
                            start_participant_counter(bot, task_info['chat_id'], task_info['message_id'], giveaway_id,
                                                      supabase)
                        )
                        counter_tasks.append(task)

                    await bot.answer_callback_query(callback_query.id, text="Розыгрыш опубликован и активирован!")

                    keyboard = InlineKeyboardBuilder()
                    keyboard.button(text="Назад", callback_data="back_to_main_menu")

                    result_message = f"<b>✅ Розыгрыш успешно опубликован в {success_count} сообществах.</b>\n🔄 Счетчик участников будет обновляться каждую минуту."
                    if error_count > 0:
                        result_message += f"\n\n<b>❌ Ошибки публикации ({error_count}):</b>"
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
                        message_id=callback_query.message.message_id,
                        parse_mode='HTML'  # Добавляем поддержку HTML для сообщения результата
                    )
                except Exception as e:
                    logging.error(f"Error finalizing giveaway activation: {str(e)}")
                    await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при активации розыгрыша.")
            else:
                await bot.answer_callback_query(callback_query.id, text="Не удалось опубликовать розыгрыш.")
                error_keyboard = InlineKeyboardBuilder()
                error_keyboard.button(text="Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    f"<b>❌ Не удалось опубликовать розыгрыш.</b>\nОшибок: {error_count}\n\n<b>Подробности:</b>\n" + "\n".join(
                        error_messages),
                    reply_markup=error_keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML'  # Добавляем поддержку HTML для сообщения об ошибке
                )
        except Exception as e:
            logging.error(f"Error in process_publish_giveaway: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при публикации розыгрыша.")
        finally:
            user_selected_communities.pop(user_id, None)

    async def get_participant_count(giveaway_id: str, supabase: Client) -> int:
        try:
            response = supabase.table('participations').select('*', count='exact').eq('giveaway_id',
                                                                                      giveaway_id).execute()
            return response.count if hasattr(response, 'count') else 0
        except Exception as e:
            logging.error(f"Ошибка при получении количества участников: {str(e)}")
            return 0

    async def update_participant_button(bot: Bot, chat_id: int, message_id: int, giveaway_id: str, supabase: Client):
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
        while True:
            await update_participant_button(bot, chat_id, message_id, giveaway_id, supabase)
            await asyncio.sleep(60)
