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
import boto3
from botocore.client import Config
import io
import requests
from urllib.parse import urlparse
import aiogram.exceptions
from datetime import timedelta  # Ensure this is imported at the top
from aiogram.types import InputMediaPhoto, InputMediaAnimation, InputMediaVideo  # Add these imports

# Настройка логирования 📝
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация Supabase 🗄️
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

# Конфигурация Yandex Cloud S3 ☁️
YANDEX_ACCESS_KEY = 'YCAJEDluWSn-XI0tyGyfwfnVL'
YANDEX_SECRET_KEY = 'YCPkR9H9Ucebg6L6eMGvtfKuFIcO_MK7gyiffY6H'
YANDEX_BUCKET_NAME = 'raffle'
YANDEX_ENDPOINT_URL = 'https://storage.yandexcloud.net'
YANDEX_REGION = 'ru-central1'

# Инициализация S3 клиента 📦
s3_client = boto3.client(
    's3',
    region_name=YANDEX_REGION,
    aws_access_key_id=YANDEX_ACCESS_KEY,
    aws_secret_access_key=YANDEX_SECRET_KEY,
    endpoint_url=YANDEX_ENDPOINT_URL,
    config=Config(signature_version='s3v4')
)

# Константы ⚙️
MAX_NAME_LENGTH = 100
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 5
MAX_WINNERS = 50

# Состояния FSM 🎛️
class GiveawayStates(StatesGroup):
    waiting_for_name = State()          # ✏️ Название
    waiting_for_description = State()   # 📜 Описание
    waiting_for_media_choice = State()  # 🖼️ Выбор медиа
    waiting_for_media_upload = State()  # 📤 Загрузка медиа
    waiting_for_end_time = State()      # ⏰ Время окончания
    waiting_for_winner_count = State()  # 🏆 Кол-во победителей

FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>
- Цитата
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
</blockquote>
"""

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    """Загружает файл в хранилище 📤"""
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой! Максимум: {MAX_MEDIA_SIZE_MB} МБ 😔"

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        try:
            s3_client.put_object(
                Bucket=YANDEX_BUCKET_NAME,
                Key=unique_filename,
                Body=io.BytesIO(file_content),
                ContentType="application/octet-stream"
            )
            public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"
            logger.info(f"✅ Файл загружен: {unique_filename}")
            return True, public_url

        except Exception as s3_error:
            logger.error(f"❌ Ошибка S3: {str(s3_error)}")
            presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={'Bucket': YANDEX_BUCKET_NAME, 'Key': unique_filename, 'ContentType': 'application/octet-stream'},
                ExpiresIn=3600
            )
            parsed_url = urlparse(presigned_url)
            headers = {'Content-Type': 'application/octet-stream', 'Host': parsed_url.netloc}
            response = requests.put(presigned_url, data=io.BytesIO(file_content), headers=headers)

            if response.status_code == 200:
                public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"
                logger.info(f"✅ Файл загружен через URL: {unique_filename}")
                return True, public_url
            else:
                logger.error(f"❌ Ошибка загрузки через URL: {response.status_code}")
                raise Exception(f"Не удалось загрузить: {response.status_code}")

    except Exception as e:
        logger.error(f"🚫 Ошибка: {str(e)}")
        return False, f"❌ Ошибка загрузки: {str(e)}"

async def save_giveaway(supabase, user_id: int, name: str, description: str, end_time: str, winner_count: int,
                        media_type: str = None, media_file_id: str = None):
    """Сохраняет розыгрыш в базе данных 💾"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = datetime.strptime(end_time, "%d.%m.%Y %H:%M")
    end_time_tz = moscow_tz.localize(end_time_dt)
    giveaway_data = {
        'user_id': user_id,
        'name': name,
        'description': description,
        'end_time': end_time_tz.isoformat(),
        'winner_count': winner_count,
        'is_active': 'false',
        'media_type': media_type,
        'media_file_id': media_file_id
    }
    try:
        response = supabase.table('giveaways').insert(giveaway_data).execute()
        if response.data:
            giveaway_id = response.data[0]['id']
            default_congrats_message = f"🎉 Поздравляем! Вы выиграли в розыгрыше \"{name}\"!"
            for place in range(1, winner_count + 1):
                supabase.table('congratulations').insert({
                    'giveaway_id': giveaway_id,
                    'place': place,
                    'message': default_congrats_message
                }).execute()
            return True, giveaway_id
        return False, None
    except Exception as e:
        logger.error(f"🚫 Ошибка сохранения: {str(e)}")
        return False, None

def register_create_giveaway_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    """Регистрирует обработчики для создания розыгрыша 🎁"""

    @dp.callback_query(lambda c: c.data == 'create_giveaway')
    async def process_create_giveaway(callback_query: CallbackQuery, state: FSMContext):
        """Начинает создание розыгрыша 🚀"""
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Давайте придумаем название розыгрыша (до {MAX_NAME_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard,
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )
        await state.update_data(last_message_id=callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_name)
    async def process_name(message: types.Message, state: FSMContext):
        """Обрабатывает введённое название 🎯"""
        formatted_text = message.html_text if message.text else ""

        if len(formatted_text) > MAX_NAME_LENGTH:
            data = await state.get_data()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название длинное! Максимум {MAX_NAME_LENGTH} символов, сейчас {len(formatted_text)}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard,
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        await state.update_data(name=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_description)
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
        data = await state.get_data()
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await send_message_with_image(
            bot,
            message.chat.id,
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Теперь добавьте описание (до {MAX_DESCRIPTION_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard,
            message_id=data['last_message_id'],
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_description)
    async def process_description(message: types.Message, state: FSMContext):
        """Обрабатывает введённое описание 📜"""
        formatted_text = message.html_text if message.text else ""

        if len(formatted_text) > MAX_DESCRIPTION_LENGTH:
            data = await state.get_data()
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Описание длинное! Максимум {MAX_DESCRIPTION_LENGTH} символов, сейчас {len(formatted_text)}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard,
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        await state.update_data(description=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✅ Да", callback_data="add_media")
        keyboard.button(text="⏭️ Пропустить", callback_data="skip_media")
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        keyboard.adjust(2, 1)
        data = await state.get_data()
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await send_message_with_image(
            bot,
            message.chat.id,
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите добавить фото, GIF или видео? (до {MAX_MEDIA_SIZE_MB} МБ) 📎",
            reply_markup=keyboard.as_markup(),
            message_id=data['last_message_id'],
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data in ["add_media", "skip_media", "back_to_media_choice"])
    async def process_media_choice(callback_query: CallbackQuery, state: FSMContext):
        """Обрабатывает выбор медиа 🖼️"""
        try:
            await bot.answer_callback_query(callback_query.id)
        except aiogram.exceptions.TelegramBadRequest as e:
            logger.warning(f"Не удалось ответить на callback: {str(e)}")

        if callback_query.data == "add_media":
            await state.set_state(GiveawayStates.waiting_for_media_upload)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_media_choice")]])
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!",
                reply_markup=keyboard,
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )
        elif callback_query.data == "skip_media":
            await process_end_time_request(callback_query.from_user.id, state, callback_query.message.message_id)
        elif callback_query.data == "back_to_media_choice":
            await state.set_state(GiveawayStates.waiting_for_media_choice)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✅ Да", callback_data="add_media")
            keyboard.button(text="⏭️ Пропустить", callback_data="skip_media")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(2, 1)
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите добавить фото, GIF или видео? (до {MAX_MEDIA_SIZE_MB} МБ) 📎",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )

    @dp.message(GiveawayStates.waiting_for_media_upload)
    async def process_media_upload(message: types.Message, state: FSMContext):
        """Обрабатывает загрузку медиа 📤"""
        try:
            data = await state.get_data()
            last_message_id = data.get('last_message_id')
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_media_choice")]])
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Загружаем ваше медиа...",
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
                    "<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Отправьте фото, GIF или видео!",
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
                    f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл большой! Максимум {MAX_MEDIA_SIZE_MB} МБ, сейчас {file_size_mb:.2f} МБ 😔",
                    reply_markup=keyboard,
                    message_id=last_message_id,
                    parse_mode='HTML'
                )
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(result)

            await state.update_data(media_type=media_type, media_file_id=result)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await process_end_time_request(message.chat.id, state, last_message_id)

        except Exception as e:
            logger.error(f"🚫 Ошибка загрузки: {str(e)}")
            data = await state.get_data()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_media_choice")]])
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ой! Не удалось загрузить медиа 😔 Попробуйте ещё раз!",
                reply_markup=keyboard,
                message_id=data.get('last_message_id'),
                parse_mode='HTML'
            )

    async def process_end_time_request(chat_id: int, state: FSMContext, message_id: int = None):
        """Запрашивает время окончания розыгрыша ⏰"""
        await state.set_state(GiveawayStates.waiting_for_end_time)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
Когда завершится розыгрыш? Укажите дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
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
        """Обрабатывает время окончания ⏰"""
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
                f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Сколько будет победителей? Максимум {MAX_WINNERS}!",
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
<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Неверный формат! Используйте <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>
"""
            await send_message_with_image(
                bot,
                message.chat.id,
                html_message,
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

    async def send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard, message_id=None):
        """Updates an existing message or sends a new giveaway message with or without media."""
        try:
            if message_id:  # If message_id is provided, edit the existing message
                if giveaway['media_type'] and giveaway['media_file_id']:
                    media_types = {
                        'photo': InputMediaPhoto,
                        'gif': InputMediaAnimation,
                        'video': InputMediaVideo
                    }
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=message_id,
                        media=media_types[giveaway['media_type']](
                            media=giveaway['media_file_id'],
                            caption=giveaway_info,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                else:
                    # Use send_message_with_image with placeholder image when no media
                    await send_message_with_image(
                        bot,
                        chat_id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,
                        parse_mode='HTML'
                    )
            else:  # If no message_id, send a new message
                if giveaway['media_type'] and giveaway['media_file_id']:
                    if giveaway['media_type'] == 'photo':
                        await bot.send_photo(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                             reply_markup=keyboard.as_markup(), parse_mode='HTML')
                    elif giveaway['media_type'] == 'gif':
                        await bot.send_animation(chat_id, animation=giveaway['media_file_id'], caption=giveaway_info,
                                                 reply_markup=keyboard.as_markup(), parse_mode='HTML')
                    elif giveaway['media_type'] == 'video':
                        await bot.send_video(chat_id, video=giveaway['media_file_id'], caption=giveaway_info,
                                             reply_markup=keyboard.as_markup(), parse_mode='HTML')
                else:
                    # Use send_message_with_image with placeholder image when no media
                    await send_message_with_image(
                        bot,
                        chat_id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
        except Exception as e:
            logger.error(f"Ошибка при отправке/обновлении сообщения: {str(e)}")
            raise

    async def display_giveaway(bot: Bot, chat_id: int, giveaway_id: int, supabase: Client, message_id: int = None):
        """Displays the giveaway details by updating the existing message."""
        try:
            # Fetch giveaway details from Supabase
            response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            if not response.data:
                raise Exception("Giveaway not found in database")

            giveaway = response.data

            # Build the keyboard with all options
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Редактировать", callback_data=f"edit_post:{giveaway_id}")
            keyboard.button(text="👥 Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
            keyboard.button(text="📢 Опубликовать", callback_data=f"activate_giveaway:{giveaway_id}")
            keyboard.button(text="📩 Добавить приглашения", callback_data=f"add_invite_task:{giveaway_id}")
            keyboard.button(text="🎉 Сообщение победителям", callback_data=f"message_winners:{giveaway_id}")
            keyboard.button(text="👀 Предпросмотр", callback_data=f"preview_giveaway:{giveaway_id}")
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_giveaway:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            keyboard.adjust(1)

            # Include invite info if applicable
            invite_info = f"\n<tg-emoji emoji-id='5352899869369446268'>😊</tg-emoji> Приглашайте {giveaway['quantity_invite']} друзей для участия!" if giveaway.get('invite',
                                                                                                               False) else ""

            # Format the giveaway message
            end_time_msk = (datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime(
                '%d.%m.%Y %H:%M')
            giveaway_info = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}
<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {end_time_msk} (МСК)
{invite_info}
"""

            # Update the existing message with the giveaway details
            await send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard, message_id=message_id)

        except Exception as e:
            logger.error(f"🚫 Ошибка отображения розыгрыша: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            # Fallback to editing the message with an error if possible
            if message_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="❌ Ошибка загрузки розыгрыша 😔\n⚠️ Упс! Что-то пошло не так. Попробуйте снова!",
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            else:
                await send_message_with_image(
                    bot,
                    chat_id,
                    "❌ Ошибка загрузки розыгрыша 😔\n⚠️ Упс! Что-то пошло не так. Попробуйте снова!",
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        """Обрабатывает количество победителей 🏆"""
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        try:
            winner_count = int(message.text)
            if winner_count <= 0:
                raise ValueError("Количество должно быть положительным")
            if winner_count > MAX_WINNERS:
                data = await state.get_data()
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Слишком много! Максимум {MAX_WINNERS} победителей",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
                return

            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            # Send the "Creating..." message that will be updated
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Создаём ваш розыгрыш...",
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
                # Update the "Creating..." message with giveaway details
                await display_giveaway(bot, message.chat.id, giveaway_id, supabase,
                                       message_id=data.get('last_message_id'))
                await state.clear()
            else:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="🔄 Попробовать снова", callback_data="create_giveaway")
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                keyboard.adjust(1)
                # Update the "Creating..." message with an error
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=data.get('last_message_id'),
                    text="❌ Ой! Не удалось сохранить розыгрыш 😔 Давайте попробуем ещё раз?",
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
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число! Например, 3",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🔄 Попробовать снова", callback_data="create_giveaway")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(1)
            # Update the "Creating..." message with a general error
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data.get('last_message_id'),
                text="❌ Упс! Что-то пошло не так 😔 Попробуем ещё раз?",
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
