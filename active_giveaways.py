from utils import end_giveaway, send_message_with_image
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import pytz
from supabase import Client
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiogram.exceptions
import json
import math
import boto3
from botocore.client import Config
import io

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

# Constants for validation
MAX_MEDIA_SIZE_MB = 5

class EditGiveawayStates(StatesGroup):
    waiting_for_new_name_active = State()
    waiting_for_new_description_active = State()
    waiting_for_new_winner_count_active = State()
    waiting_for_new_end_time_active = State()
    waiting_for_new_media_active = State()

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        # Check file size (5 MB limit)
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ"

        # Generate unique filename to avoid conflicts
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        # Upload file to Yandex Cloud S3
        try:
            # First, check if the bucket exists
            try:
                s3_client.head_bucket(Bucket=YANDEX_BUCKET_NAME)
                logging.info(f"Bucket {YANDEX_BUCKET_NAME} exists and is accessible")
            except Exception as bucket_error:
                logging.error(f"Bucket error: {str(bucket_error)}")
                # If the bucket doesn't exist, try to create it
                try:
                    logging.info(f"Attempting to create bucket {YANDEX_BUCKET_NAME}")
                    s3_client.create_bucket(
                        Bucket=YANDEX_BUCKET_NAME,
                        CreateBucketConfiguration={'LocationConstraint': YANDEX_REGION}
                    )
                    logging.info(f"Bucket {YANDEX_BUCKET_NAME} created successfully")
                except Exception as create_error:
                    logging.error(f"Failed to create bucket: {str(create_error)}")
                    raise Exception(f"Cannot access or create bucket: {str(create_error)}")

            # Try to upload the file
            logging.info(f"Uploading file {unique_filename} to bucket {YANDEX_BUCKET_NAME}")
            s3_client.put_object(
                Bucket=YANDEX_BUCKET_NAME,
                Key=unique_filename,
                Body=io.BytesIO(file_content),
                ContentType="application/octet-stream",
                ACL='public-read'  # Make the object publicly readable
            )

            # Generate public URL for the uploaded file
            public_url = f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/{unique_filename}"

            logging.info(f"File uploaded successfully to Yandex Cloud: {unique_filename}")
            logging.info(f"Public URL: {public_url}")

            return True, public_url

        except Exception as s3_error:
            logging.error(f"Yandex Cloud S3 upload error: {str(s3_error)}")
            raise Exception(f"Failed to upload to Yandex Cloud: {str(s3_error)}")

    except Exception as e:
        error_msg = str(e)
        logging.error(f"Storage upload error: {error_msg}")
        return False, error_msg

def register_active_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'active_giveaways' or c.data.startswith('active_giveaways_page:'))
    async def process_active_giveaways(callback_query: types.CallbackQuery):
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        # Get page number from callback data
        current_page = 1
        if callback_query.data.startswith('active_giveaways_page:'):
            current_page = int(callback_query.data.split(':')[1])

        try:
            # Get all active giveaways
            response = supabase.table('giveaways').select('*').eq('is_active', True).eq('user_id', user_id).order(
                'end_time').execute()

            if not response.data:
                await bot.answer_callback_query(callback_query.id, text="У вас нет активных розыгрышей.")
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
                    callback_data=f"view_active_giveaway:{giveaway['id']}"
                ))

            # Create navigation row
            nav_buttons = []

            # Previous page button
            if current_page > 1:
                nav_buttons.append(types.InlineKeyboardButton(
                    text="←",
                    callback_data=f"active_giveaways_page:{current_page - 1}"
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
                    callback_data=f"active_giveaways_page:{current_page + 1}"
                ))

            # Add navigation buttons in one row if there are any
            if nav_buttons:
                keyboard.row(*nav_buttons)

            # Add back button in its own row
            keyboard.row(types.InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="back_to_main_menu"
            ))

            await bot.answer_callback_query(callback_query.id)

            # Update message with pagination info
            message_text = "Выберите активный розыгрыш"
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
            logging.error(f"Error in process_active_giveaways: {str(e)}")
            await bot.answer_callback_query(
                callback_query.id,
                text="Произошла ошибка при получении активных розыгрышей."
            )

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('view_active_giveaway:'))
    async def process_view_active_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()

        if not response.data:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        giveaway = response.data

        # Получение количества участников
        participants_response = supabase.table('participations').select('count').eq('giveaway_id',
                                                                                    giveaway_id).execute()
        participants_count = participants_response.data[0]['count']

        giveaway_info = f"""
Активный розыгрыш:

Название: {giveaway['name']}
Описание: {giveaway['description']}

Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}
Количество победителей: {giveaway['winner_count']}
Участвуют: {participants_count}
"""

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Редактировать Пост", callback_data=f"edit_active_post:{giveaway_id}")
        keyboard.button(text="Принудительное завершение", callback_data=f"force_end_giveaway:{giveaway_id}")
        keyboard.button(
            text=f"Открыть",
            url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}"
        ),
        keyboard.button(text="◀️ Назад к списку", callback_data="active_giveaways")
        keyboard.adjust(1)

        try:
            await bot.answer_callback_query(callback_query.id)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "query is too old" in str(e):
                logging.warning(f"Callback query is too old: {e}")
            else:
                raise

        try:
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': types.InputMediaPhoto,
                    'gif': types.InputMediaAnimation,
                    'video': types.InputMediaVideo
                }
                media_class = media_types.get(giveaway['media_type'])
                if media_class:
                    await bot.edit_message_media(
                        chat_id=callback_query.message.chat.id,
                        message_id=callback_query.message.message_id,
                        media=media_class(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
                else:
                    raise ValueError(f"Unknown media type: {giveaway['media_type']}")
            else:
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
                await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
            else:
                raise

    @dp.callback_query(lambda c: c.data.startswith('edit_active_post:'))
    async def process_edit_active_post(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    # Constants for validation
    MAX_NAME_LENGTH = 50
    MAX_DESCRIPTION_LENGTH = 2500
    MAX_WINNERS = 50

    async def _show_edit_menu_active(user_id: int, giveaway_id: str, message_id: int = None):
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        if not response.data:
            await bot.send_message(user_id, "Розыгрыш не найден.")
            return

        giveaway = response.data

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name_active:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_name_active_active:{giveaway_id}")
        keyboard.button(text="🏆 Кол-во победителей", callback_data=f"edit_winner_count_active:{giveaway_id}")
        keyboard.button(text="🗓 Дата завершения", callback_data=f"change_end_date_active:{giveaway_id}")
        keyboard.button(text="🖼 Медиа", callback_data=f"view_manage_media:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_active_giveaway:{giveaway_id}")
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
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': types.InputMediaPhoto,
                    'gif': types.InputMediaAnimation,
                    'video': types.InputMediaVideo
                }
                media_class = media_types.get(giveaway['media_type'])
                if media_class:
                    await bot.edit_message_media(
                        chat_id=user_id,
                        message_id=message_id,
                        media=media_class(media=giveaway['media_file_id'], caption=giveaway_info),
                        reply_markup=keyboard.as_markup()
                    )
                else:
                    raise ValueError(f"Unknown media type: {giveaway['media_type']}")
            else:
                await send_message_with_image(
                    bot,
                    user_id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=message_id
                )
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message to edit not found" in str(e):
                logging.warning(f"Message to edit not found: {e}")
                await send_new_giveaway_message(user_id, giveaway, giveaway_info, keyboard)
            else:
                raise
        except Exception as e:
            logging.error(f"Error in _show_edit_menu_active: {str(e)}")
            await bot.send_message(
                chat_id=user_id,
                text="Произошла ошибка при отображении меню редактирования. Пожалуйста, попробуйте еще раз."
            )

    @dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
    async def process_force_end_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await bot.answer_callback_query(callback_query.id, text="Завершение розыгрыша...")

        await end_giveaway(bot=bot, giveaway_id=giveaway_id, supabase=supabase)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data="back_to_main_menu")
        await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                      message_id=callback_query.message.message_id,
                                      text="Розыгрыш успешно завершен. Результаты опубликованы в связанных сообществах.",
                                      reply_markup=keyboard.as_markup())

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

    async def update_published_posts_active(giveaway_id: str, new_giveaway_data: dict):
        try:
            giveaway_response = supabase.table('giveaways').select('published_messages').eq('id',
                                                                                            giveaway_id).single().execute()
            published_messages = json.loads(giveaway_response.data['published_messages'])

            for message in published_messages:
                chat_id = message['chat_id']
                message_id = message['message_id']

                new_post_text = f"""
{new_giveaway_data['name']}

{new_giveaway_data['description']}

Количество победителей: {new_giveaway_data['winner_count']}
Дата завершения: {(datetime.fromisoformat(new_giveaway_data['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}

Нажмите кнопку ниже, чтобы принять участие!
"""

                participants_response = supabase.table('participations').select('count').eq('giveaway_id',
                                                                                            giveaway_id).execute()
                participants_count = participants_response.data[0]['count']

                keyboard = InlineKeyboardBuilder()
                keyboard.button(
                    text=f"Участвовать ({participants_count})",
                    url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}"
                )

                try:
                    if new_giveaway_data['media_type'] and new_giveaway_data['media_file_id']:
                        media_types = {
                            'photo': types.InputMediaPhoto,
                            'gif': types.InputMediaAnimation,
                            'video': types.InputMediaVideo
                        }
                        media_class = media_types.get(new_giveaway_data['media_type'])
                        if media_class:
                            await bot.edit_message_media(
                                chat_id=chat_id,
                                message_id=message_id,
                                media=media_class(media=new_giveaway_data['media_file_id'], caption=new_post_text),
                                reply_markup=keyboard.as_markup()
                            )
                        else:
                            raise ValueError(f"Unknown media type: {new_giveaway_data['media_type']}")
                    else:
                        # If there's no media, we need to handle this case differently
                        try:
                            # First, try to edit the message text
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=new_post_text,
                                reply_markup=keyboard.as_markup()
                            )
                        except aiogram.exceptions.TelegramBadRequest as e:
                            if "there is no text in the message to edit" in str(e).lower():
                                # If there's no text to edit, it means we're dealing with a media-only message
                                # In this case, we need to send a new text message and delete the old media message
                                new_message = await bot.send_message(
                                    chat_id=chat_id,
                                    text=new_post_text,
                                    reply_markup=keyboard.as_markup()
                                )

                                # Try to delete the old message
                                try:
                                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                                except aiogram.exceptions.TelegramBadRequest:
                                    logging.warning(f"Could not delete old message {message_id} in chat {chat_id}")

                                # Update the message info in the database
                                updated_messages = [msg for msg in published_messages if
                                                    msg['message_id'] != message_id]
                                updated_messages.append({
                                    'chat_id': chat_id,
                                    'message_id': new_message.message_id
                                })
                                supabase.table('giveaways').update({
                                    'published_messages': json.dumps(updated_messages)
                                }).eq('id', giveaway_id).execute()
                            else:
                                raise
                except Exception as e:
                    logging.error(f"Error updating published message: {str(e)}")

        except Exception as e:
            logging.error(f"Error updating published posts: {str(e)}")

    @dp.callback_query(lambda c: c.data.startswith('edit_name_active:'))
    async def process_edit_name_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_name_active)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Введите новое название розыгрыша (максимум {MAX_NAME_LENGTH} символов): \n\nТекущее название будет заменено на введенный вами текст.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(EditGiveawayStates.waiting_for_new_name_active)
    async def process_new_name_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_name = message.text

        # Check name length
        if len(new_name) > MAX_NAME_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
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
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

            # Update published posts
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)
        except Exception as e:
            logging.error(f"Error updating giveaway name: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении названия розыгрыша.")

        await state.clear()


    @dp.callback_query(lambda c: c.data.startswith('edit_name_active_active:'))
    async def process_edit_description_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_description_active)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Введите новое описание розыгрыша (максимум {MAX_DESCRIPTION_LENGTH} символов): \n\nТекущее описание будет заменено на введенный вами текст.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(EditGiveawayStates.waiting_for_new_description_active)
    async def process_new_description_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_description = message.text

        # Check description length
        if len(new_description) > MAX_DESCRIPTION_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
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
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

            # Update published posts
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)
        except Exception as e:
            logging.error(f"Error updating giveaway description: {str(e)}")
            await message.reply("❌ Произошла ошибка при обновлении описания розыгрыша.")

        await state.clear()

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count_active:'))
    async def process_edit_winner_count_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_winner_count_active)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Введите новое количество победителей (максимум {MAX_WINNERS} победителей): \n\nВведите положительное целое число.",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(EditGiveawayStates.waiting_for_new_winner_count_active)
    async def process_new_winner_count_active(message: types.Message, state: FSMContext):
        # Delete user's message first
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            # Show loading message
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Обновление количества победителей...",
                message_id=data.get('last_message_id'),
            )

            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Количество победителей должно быть положительным числом.")

            # Check winner count limit
            if new_winner_count > MAX_WINNERS:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"Слишком много победителей. Максимальное количество: {MAX_WINNERS}. Пожалуйста, введите меньшее число.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

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

            # Update published posts
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)

            # Show success by updating menu
            await state.clear()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ошибка\n\nПожалуйста, введите положительное целое число для количества победителей.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )
            # Don't clear state to allow retry

        except Exception as e:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            logging.error(f"Error updating winner count: {str(e)}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении количества победителей. Пожалуйста, попробуйте еще раз.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('change_end_date_active:'))
    async def process_change_end_date_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_end_time_active)
        await callback_query.answer()

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

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

    @dp.message(EditGiveawayStates.waiting_for_new_end_time_active)
    async def process_new_end_time_active(message: types.Message, state: FSMContext):
        # Delete user's message first
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        if message.text.lower() == '◀️ Назад':
            await state.clear()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])
            return

        try:
            # Show loading message
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Обновление даты завершения...",
                message_id=data.get('last_message_id'),
            )

            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            moscow_tz = pytz.timezone('Europe/Moscow')
            new_end_time_tz = moscow_tz.localize(new_end_time)

            # Update the end time
            supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id',
                                                                                             giveaway_id).execute()

            # Update published posts
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)

            # Show success by updating menu
            await state.clear()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад", callback_data="_show_edit_menu_active")
            error_message = f"""
❌ Неверный формат даты.

Пожалуйста, введите дату завершения розыгрыша в формате ДД.ММ.ГГГГ ЧЧ:ММ
текущая дата и время: 
<code>{current_time}</code>
"""
            await send_message_with_image(
                bot,
                message.chat.id,
                error_message,
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
            # Don't clear state to allow retry

        except Exception as e:
            logging.error(f"Error updating end time: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении даты завершения розыгрыша. Пожалуйста, попробуйте еще раз.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )
            # Don't clear state to allow retry

    @dp.callback_query(lambda c: c.data.startswith('view_manage_media:'))
    async def process_view_manage_media_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if giveaway['media_type']:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить медиа файл", callback_data=f"change_media_active:{giveaway_id}")
            keyboard.button(text="Удалить медиа файл", callback_data=f"delete_media_active:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            keyboard.adjust(1)

            text = "Выберите действие, которое хотите сделать:"
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Да", callback_data=f"add_media_active:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            keyboard.adjust(2)

            text = f"Хотите добавить фото, GIF или видео? (максимальный размер файла: {MAX_MEDIA_SIZE_MB} МБ)"

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('add_media_active:') or c.data.startswith('change_media_active:'))
    async def process_add_or_change_media_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_media_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Пожалуйста, отправьте фото, GIF или видео (максимальный размер файла: {MAX_MEDIA_SIZE_MB} МБ).",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_media_active)
    async def process_new_media_active(message: types.Message, state: FSMContext):
        # Delete user's message first
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            # Show loading message
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Загрузка...",
                message_id=data.get('last_message_id'),
            )

            # Process media file
            if message.photo:
                file_id = message.photo[-1].file_id
                media_type = 'photo'
                file_ext = 'jpg'
            elif message.animation:
                file_id = message.animation.file_id
                media_type = 'gif'  # Keep the media type as 'gif' for identification
                file_ext = 'gif'  # Use gif extension
            elif message.video:
                file_id = message.video.file_id
                media_type = 'video'
                file_ext = 'mp4'
            else:
                # Update message with error
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Пожалуйста, отправьте фото, GIF или видео.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

            # Get file from Telegram
            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)

            # Check file size
            file_size_mb = file.file_size / (1024 * 1024)
            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await send_message_with_image(
                    bot,
                    message.from_user.id,
                    f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ. Текущий размер: {file_size_mb:.2f} МБ. Пожалуйста, отправьте файл меньшего размера.",
                    reply_markup=keyboard.as_markup(),
                    message_id=data.get('last_message_id')
                )
                return

            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"

            # Upload to Yandex Cloud Storage
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(f"Failed to upload to storage: {result}")

            # Update database with new media info
            supabase.table('giveaways').update({
                'media_type': media_type,
                'media_file_id': result
            }).eq('id', giveaway_id).execute()

            # Show success message and update menu
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

            # Update published posts
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).execute()
            if giveaway_data.data:
                await update_published_posts_active(giveaway_id, giveaway_data.data[0])

            await state.clear()

        except Exception as e:
            logging.error(f"Error updating media: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Произошла ошибка при обновлении медиа файла. Пожалуйста, попробуйте еще раз.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('delete_media_active:'))
    async def process_delete_media_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]

        try:
            # Обновляем данные розыгрыша в базе данных
            supabase.table('giveaways').update({
                'media_type': None,
                'media_file_id': None
            }).eq('id', giveaway_id).execute()

            # Получаем обновленные данные розыгрыша
            giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            giveaway_data = giveaway_response.data

            # Обновляем меню редактирования
            await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

            # Обновляем опубликованные посты
            await update_published_posts_active(giveaway_id, giveaway_data)

            await bot.answer_callback_query(callback_query.id, text="Медиа файл успешно удален.")
        except Exception as e:
            logging.error(f"Error deleting media: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при удалении медиа файла.")

