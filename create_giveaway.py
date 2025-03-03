from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from supabase import create_client, Client
from datetime import datetime
import pytz
from utils import send_message_with_image
import logging
import asyncio
import boto3
from botocore.client import Config
import io
import requests
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

# Yandex Cloud S3 configuration
YANDEX_ACCESS_KEY = 'YCAJEDluWSn-XI0tyGyfwfnVL'
YANDEX_SECRET_KEY = 'YCPkR9H9Ucebg6L6eMGvtfKuFIcO_MK7gyiffY6H'
YANDEX_BUCKET_NAME = 'raffle'
YANDEX_REGION = 'ru-central1'

s3_client = boto3.client(
    's3',
    aws_access_key_id=YANDEX_ACCESS_KEY,
    aws_secret_access_key=YANDEX_SECRET_KEY,
    region_name=YANDEX_REGION,
    endpoint_url='https://storage.yandexcloud.net',
    config=Config(region_name=YANDEX_REGION, signature_version='s3v4')
)

# Constraints
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 5
MAX_WINNERS = 50

class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_media_choice = State()
    waiting_for_media_upload = State()
    waiting_for_end_time = State()
    waiting_for_winner_count = State()

# Formatting instructions
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

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        # Check file size (5 MB limit)
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ"

        # Generate unique filename to avoid conflicts
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        try:
            # Upload directly to the bucket
            s3_client.put_object(
                Bucket=YANDEX_BUCKET_NAME,
                Key=unique_filename,
                Body=io.BytesIO(file_content),
                ContentType="application/octet-stream"
            )

            # Generate the correct public URL format for Yandex Cloud Storage
            public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"

            logger.info(f"File uploaded successfully: {unique_filename}")
            logger.info(f"Public URL: {public_url}")

            return True, public_url

        except Exception as s3_error:
            logger.error(f"Yandex Cloud S3 upload error: {str(s3_error)}")

            try:
                # Try alternative upload method with presigned URL
                presigned_url = s3_client.generate_presigned_url(
                    'put_object',
                    Params={
                        'Bucket': YANDEX_BUCKET_NAME,
                        'Key': unique_filename,
                        'ContentType': 'application/octet-stream'
                    },
                    ExpiresIn=3600
                )

                # Extract the hostname from the presigned URL
                parsed_url = urlparse(presigned_url)
                headers = {
                    'Content-Type': 'application/octet-stream',
                    'Host': parsed_url.netloc
                }

                response = requests.put(
                    presigned_url,
                    data=io.BytesIO(file_content),
                    headers=headers
                )

                if response.status_code == 200:
                    public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"
                    logger.info(f"File uploaded successfully using presigned URL: {unique_filename}")
                    return True, public_url
                else:
                    logger.error(f"Failed to upload using presigned URL: {response.status_code} - {response.text}")
                    raise Exception(f"Failed to upload using presigned URL: {response.status_code}")

            except Exception as alt_error:
                logger.error(f"Alternative upload method failed: {str(alt_error)}")
                raise Exception(f"Failed to upload to Yandex Cloud: {str(s3_error)}")

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Storage upload error: {error_msg}")
        return False, error_msg

async def save_giveaway(supabase, user_id: int, name: str, description: str, end_time: str, winner_count: int,
                        media_type: str = None, media_file_id: str = None):
    # (Your existing save_giveaway function remains unchanged)
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = datetime.strptime(end_time, "%d.%m.%Y %H:%M")
    end_time_tz = moscow_tz.localize(end_time_dt)
    giveaway_data = {
        'user_id': user_id,
        'name': name,
        'description': description,
        'end_time': end_time_tz.isoformat(),
        'winner_count': winner_count,
        'is_active': False,
        'media_type': media_type,
        'media_file_id': media_file_id
    }
    try:
        response = supabase.table('giveaways').insert(giveaway_data).execute()
        if response.data:
            giveaway_id = response.data[0]['id']
            default_congrats_message = f"Поздравляем! Вы выиграли в розыгрыше \"{name}\"!"
            for place in range(1, winner_count + 1):
                supabase.table('congratulations').insert({
                    'giveaway_id': giveaway_id,
                    'place': place,
                    'message': default_congrats_message
                }).execute()
            return True, giveaway_id
        return False, None
    except Exception as e:
        logger.error(f"Error saving giveaway: {str(e)}")
        return False, None

def register_create_giveaway_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'create_giveaway')
    async def process_create_giveaway(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
        message_text = f"Напишите название розыгрыша (максимум {MAX_NAME_LENGTH} символов)\n{FORMATTING_GUIDE}"
        await send_message_with_image(bot, callback_query.from_user.id, message_text,
                                      reply_markup=keyboard,
                                      message_id=callback_query.message.message_id,
                                      parse_mode='HTML')
        await state.update_data(last_message_id=callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_name)
    async def process_name(message: types.Message, state: FSMContext):
        # Получаем HTML-форматированный текст
        formatted_text = message.html_text if message.text else ""

        if len(formatted_text) > MAX_NAME_LENGTH:
            data = await state.get_data()
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Название слишком длинное. Максимальная длина: {MAX_NAME_LENGTH} символов. Текущая длина: {len(formatted_text)} символов. Пожалуйста, введите более короткое название.\n{FORMATTING_GUIDE}",
                reply_markup=keyboard,
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        # Сохраняем HTML-форматированный текст
        await state.update_data(name=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_description)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
        data = await state.get_data()
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        message_text = f"Напишите описание для розыгрыша (максимум {MAX_DESCRIPTION_LENGTH} символов)\n{FORMATTING_GUIDE}"
        await send_message_with_image(
            bot,
            message.chat.id,
            message_text,
            reply_markup=keyboard,
            message_id=data['last_message_id'],
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_description)
    async def process_description(message: types.Message, state: FSMContext):
        # Получаем HTML-форматированный текст
        formatted_text = message.html_text if message.text else ""

        if len(formatted_text) > MAX_DESCRIPTION_LENGTH:
            data = await state.get_data()
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Описание слишком длинное. Максимальная длина: {MAX_DESCRIPTION_LENGTH} символов. Текущая длина: {len(formatted_text)} символов. Пожалуйста, введите более короткое описание.\n{FORMATTING_GUIDE}",
                reply_markup=keyboard,
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        # Сохраняем HTML-форматированный текст
        await state.update_data(description=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Да", callback_data="add_media")
        keyboard.button(text="Пропустить", callback_data="skip_media")
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        keyboard.adjust(2, 1)
        data = await state.get_data()
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await send_message_with_image(
            bot,
            message.chat.id,
            f"Хотите добавить фото, GIF или видео? (максимальный размер файла: {MAX_MEDIA_SIZE_MB} МБ)",
            reply_markup=keyboard.as_markup(),
            message_id=data['last_message_id'],
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data in ["add_media", "skip_media"])
    async def process_media_choice(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        if callback_query.data == "add_media":
            await state.set_state(GiveawayStates.waiting_for_media_upload)
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"Пожалуйста, отправьте фото, GIF или видео (максимальный размер файла: {MAX_MEDIA_SIZE_MB} МБ).",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]]),
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )
        else:
            await process_end_time_request(callback_query.from_user.id, state, callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_media_upload)
    async def process_media_upload(message: types.Message, state: FSMContext):
        # (Your existing process_media_upload function remains unchanged except for parse_mode)
        try:
            data = await state.get_data()
            last_message_id = data.get('last_message_id')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]
            ])
            await send_message_with_image(
                bot,
                message.chat.id,
                "Загрузка...",
                reply_markup=keyboard,
                message_id=last_message_id,
                parse_mode='HTML'
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
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Пожалуйста, отправьте фото, GIF или видео.",
                    reply_markup=keyboard,
                    message_id=last_message_id,
                    parse_mode='HTML'
                )
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)
            file_size_mb = file.file_size / (1024 * 1024)
            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"Файл слишком большой. Максимальный размер: {MAX_MEDIA_SIZE_MB} МБ. Текущий размер: {file_size_mb:.2f} МБ.",
                    reply_markup=keyboard,
                    message_id=last_message_id,
                    parse_mode='HTML'
                )
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(f"Failed to upload to storage: {result}")

            await state.update_data(media_type=media_type, media_file_id=result)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await process_end_time_request(message.chat.id, state, last_message_id)

        except Exception as e:
            logger.error(f"Error processing media upload: {str(e)}")
            data = await state.get_data()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                "Произошла ошибка при обработке медиафайла. Пожалуйста, попробуйте еще раз.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]]),
                message_id=data.get('last_message_id'),
                parse_mode='HTML'
            )

    async def process_end_time_request(chat_id: int, state: FSMContext, message_id: int = None):
        await state.set_state(GiveawayStates.waiting_for_end_time)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
Укажите новую дату завершения розыгрыша в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>

Текущая дата и время:
<code>{current_time}</code>
"""
        await send_message_with_image(
            bot,
            chat_id,
            html_message,
            reply_markup=keyboard.as_markup(),
            message_id=message_id,
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_end_time)
    async def process_end_time(message: types.Message, state: FSMContext):
        # (Your existing process_end_time function remains unchanged except for parse_mode)
        try:
            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            await state.update_data(end_time=message.text)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await state.set_state(GiveawayStates.waiting_for_winner_count)
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                message.chat.id,
                f"Укажите количество победителей (максимум {MAX_WINNERS} победителей)",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        except ValueError:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""
Вы ввели неправильный формат даты.

Пожалуйста, введите дату завершения розыгрыша в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>
Текущая дата и время:
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

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        # (Your existing process_winner_count function remains unchanged except for parse_mode)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        try:
            winner_count = int(message.text)
            if winner_count <= 0:
                raise ValueError("Winner count must be positive")
            if winner_count > MAX_WINNERS:
                data = await state.get_data()
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"Слишком много победителей. Максимальное количество: {MAX_WINNERS}. Пожалуйста, введите меньшее число.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
                return

            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Розыгрыш создается...",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

            success, giveaway_id = await save_giveaway(
                supabase,
                message.from_user.id,
                data['name'],
                data['description'],
                data['end_time'],
                winner_count,
                data.get('media_type'),
                data.get('media_file_id')
            )

            if success:
                await state.clear()
                await asyncio.sleep(1)
                from aiogram.types import Update
                update = Update(
                    update_id=0,
                    callback_query=types.CallbackQuery(
                        id="dummy_id",
                        from_user=message.from_user,
                        chat_instance="dummy_instance",
                        message=types.Message(
                            message_id=data.get('last_message_id'),
                            date=datetime.now(),
                            chat=message.chat,
                            from_user=message.from_user,
                            text=""
                        ),
                        data=f"view_created_giveaway:{giveaway_id}"
                    )
                )
                await dp.feed_update(bot=bot, update=update)
            else:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="Создать повторно", callback_data="create_giveaway")
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                keyboard.adjust(1)
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Произошла ошибка при сохранении розыгрыша. Пожалуйста, попробуйте еще раз.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )

        except ValueError:
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Пожалуйста, введите положительное целое число для количества победителей.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
