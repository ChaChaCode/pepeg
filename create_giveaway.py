from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
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
from aiogram.types import InputMediaPhoto, InputMediaAnimation, InputMediaVideo
import re

# Настройка логирования 📝
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
MAX_CAPTION_LENGTH = 850
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 850
MAX_MEDIA_SIZE_MB = 10
MAX_WINNERS = 100


# Состояния FSM 🎛️
class GiveawayStates(StatesGroup):
    waiting_for_name = State()  # ✏️ Название
    waiting_for_description = State()  # 📜 Описание
    waiting_for_media_choice = State()  # 🖼️ Выбор медиа
    waiting_for_media_upload = State()  # 📤 Загрузка медиа
    waiting_for_end_time = State()  # ⏰ Время окончания
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

Примечание: Максимальное количество кастомных эмодзи, которое может отображать Telegram в одном сообщении, ограничено 100 эмодзи.</blockquote>
"""


def count_length_with_custom_emoji(text: str) -> int:
    """Подсчитывает длину текста, считая кастомные эмодзи как 1 символ."""
    emoji_pattern = r'<tg-emoji emoji-id="[^"]+">[^<]+</tg-emoji>'
    custom_emojis = re.findall(emoji_pattern, text)
    cleaned_text = text
    for emoji in custom_emojis:
        cleaned_text = cleaned_text.replace(emoji, ' ')
    return len(cleaned_text)


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
                Params={'Bucket': YANDEX_BUCKET_NAME, 'Key': unique_filename,
                        'ContentType': 'application/octet-stream'},
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


async def save_giveaway(conn, cursor, user_id: int, name: str, description: str, end_time: str, winner_count: int,
                        media_type: str = None, media_file_id: str = None):
    """Сохраняет розыгрыш в базе данных 💾"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = datetime.strptime(end_time, "%d.%m.%Y %H:%M")
    end_time_tz = moscow_tz.localize(end_time_dt)

    try:
        # Вставляем розыгрыш в таблицу giveaways
        cursor.execute(
            """
            INSERT INTO giveaways (user_id, name, description, end_time, winner_count, is_active, media_type, media_file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, name, description, end_time_tz, winner_count, False, media_type, media_file_id)
        )
        giveaway_id = cursor.fetchone()[0]

        # Добавляем записи в таблицу congratulations
        default_congrats_message = f"🎉 Поздравляем! Вы выиграли в розыгрыше \"{name}\"!"
        for place in range(1, winner_count + 1):
            cursor.execute(
                """
                INSERT INTO congratulations (giveaway_id, place, message)
                VALUES (%s, %s, %s)
                """,
                (giveaway_id, place, default_congrats_message)
            )

        conn.commit()
        return True, giveaway_id
    except Exception as e:
        logger.error(f"🚫 Ошибка сохранения: {str(e)}")
        conn.rollback()
        return False, None


def register_create_giveaway_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    """Регистрирует обработчики для создания розыгрыша 🎁"""

    @dp.callback_query(lambda c: c.data == 'create_giveaway')
    async def process_create_giveaway(callback_query: CallbackQuery, state: FSMContext):
        """Начинает создание розыгрыша 🚀"""
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
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

        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_NAME_LENGTH:
            data = await state.get_data()
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название длинное! Максимум {MAX_NAME_LENGTH} символов, сейчас {text_length}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard,
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        await state.update_data(name=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_description)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
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

        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_DESCRIPTION_LENGTH:
            data = await state.get_data()
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Описание длинное! Максимум {MAX_DESCRIPTION_LENGTH} символов, сейчас {text_length}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard,
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
        else:
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
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_media_choice")]])
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
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_media_choice")]])
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
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_media_choice")]])
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
            if message_id:
                if giveaway[7] and giveaway[8]:  # media_type — восьмой столбец, media_file_id — девятый столбец
                    media_types = {
                        'photo': InputMediaPhoto,
                        'gif': InputMediaAnimation,
                        'video': InputMediaVideo
                    }
                    await bot.edit_message_media(
                        chat_id=chat_id,
                        message_id=message_id,
                        media=media_types[giveaway[7]](
                            media=giveaway[8],
                            caption=giveaway_info,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                else:
                    await send_message_with_image(
                        bot,
                        chat_id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,
                        parse_mode='HTML'
                    )
            else:
                if giveaway[7] and giveaway[8]:
                    if giveaway[7] == 'photo':
                        await bot.send_photo(chat_id, giveaway[8], caption=giveaway_info,
                                             reply_markup=keyboard.as_markup(), parse_mode='HTML')
                    elif giveaway[7] == 'gif':
                        await bot.send_animation(chat_id, animation=giveaway[8], caption=giveaway_info,
                                                 reply_markup=keyboard.as_markup(), parse_mode='HTML')
                    elif giveaway[7] == 'video':
                        await bot.send_video(chat_id, video=giveaway[8], caption=giveaway_info,
                                             reply_markup=keyboard.as_markup(), parse_mode='HTML')
                else:
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

    async def display_giveaway(bot: Bot, chat_id: int, giveaway_id: int, conn, cursor, message_id: int = None):
        """Displays the giveaway details by updating the existing message."""
        try:
            # Получаем данные розыгрыша из базы
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                raise Exception("Giveaway not found in database")

            # Создаем клавиатуру
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

            # Формируем информацию о приглашениях
            invite_info = f"\n<tg-emoji emoji-id='5352899869369446268'>😊</tg-emoji> Приглашайте {giveaway['quantity_invite']} друзей для участия!" if giveaway.get(
                'invite') else ""

            # Обрабатываем время окончания
            end_time = giveaway['end_time']
            if not isinstance(end_time, datetime):
                raise ValueError(f"end_time is not a datetime object: {end_time}")
            end_time_msk = end_time.strftime('%d.%m.%Y %H:%M')

            # Формируем текст розыгрыша
            giveaway_info = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}
<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {end_time_msk} (МСК)
{invite_info}
"""

            # Обновляем существующее сообщение
            if message_id:
                if giveaway.get('media_type') and giveaway.get('media_file_id'):
                    # Если есть медиа в розыгрыше, используем его
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
                    # Если медиа нет, используем заглушку через send_message_with_image
                    await send_message_with_image(
                        bot,
                        chat_id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,  # Передаём message_id для редактирования
                        parse_mode='HTML'
                    )
            else:
                # Если message_id не передан (новое сообщение), отправляем с медиа или заглушкой
                if giveaway.get('media_type') and giveaway.get('media_file_id'):
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
                    await send_message_with_image(
                        bot,
                        chat_id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )

        except Exception as e:
            logger.error(f"🚫 Ошибка отображения розыгрыша: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            error_message = "❌ Ошибка загрузки розыгрыша 😔\n⚠️ Упс! Что-то пошло не так. Попробуйте снова!"
            if message_id:
                try:
                    # Пробуем отредактировать сообщение с ошибкой через send_message_with_image
                    await send_message_with_image(
                        bot,
                        chat_id,
                        error_message,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,
                        parse_mode='HTML'
                    )
                except Exception as edit_e:
                    logger.error(f"Не удалось отредактировать сообщение с ошибкой: {str(edit_e)}")
                    # Если редактирование не удалось, отправляем новое
                    await send_message_with_image(
                        bot,
                        chat_id,
                        error_message,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
            else:
                await send_message_with_image(
                    bot,
                    chat_id,
                    error_message,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        """Обрабатывает количество победителей 🏆"""
        # Удаляем сообщение пользователя с введённым количеством победителей
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        try:
            # Пробуем преобразовать введённое значение в целое число
            winner_count = int(message.text)
            # Проверяем, что количество победителей положительное
            if winner_count <= 0:
                raise ValueError("Количество должно быть положительным")
            # Проверяем, что количество победителей не превышает максимум
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

            # Получаем данные из состояния
            data = await state.get_data()
            # Создаём клавиатуру с кнопкой "В меню"
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            # Отправляем сообщение о том, что розыгрыш создаётся
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Создаём ваш розыгрыш...",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

            # Сохраняем розыгрыш в базе данных
            success, giveaway_id = await save_giveaway(
                conn,
                cursor,
                message.from_user.id,
                data['name'],
                data['description'],
                data['end_time'],
                winner_count,
                data.get('media_type'),
                data.get('media_file_id')
            )

            # Если розыгрыш успешно сохранён, отображаем его
            if success:
                await display_giveaway(bot, message.chat.id, giveaway_id, conn, cursor,
                                       message_id=data.get('last_message_id'))
                await state.clear()
            else:
                # Если сохранение не удалось, показываем ошибку
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="🔄 Попробовать снова", callback_data="create_giveaway")
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                keyboard.adjust(1)
                error_message = "❌ Ой! Не удалось сохранить розыгрыш 😔 Давайте попробуем ещё раз?"
                try:
                    # Пробуем отредактировать текст сообщения
                    await bot.edit_message_text(
                        chat_id=message.chat.id,
                        message_id=data.get('last_message_id'),
                        text=error_message,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                except aiogram.exceptions.TelegramBadRequest as te:
                    # Если сообщение не содержит текста (например, это медиа без подписи)
                    if "there is no text in the message to edit" in str(te):
                        try:
                            # Пробуем отредактировать подпись медиа
                            await bot.edit_message_caption(
                                chat_id=message.chat.id,
                                message_id=data.get('last_message_id'),
                                caption=error_message,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        except Exception as edit_caption_error:
                            # Если не удалось отредактировать подпись, отправляем новое сообщение
                            logger.error(f"Не удалось отредактировать подпись: {str(edit_caption_error)}")
                            await send_message_with_image(
                                bot,
                                message.chat.id,
                                error_message,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                    else:
                        # Если ошибка не связана с отсутствием текста, пробрасываем её дальше
                        raise te

        except ValueError:
            # Если введено некорректное число (например, не число или отрицательное)
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
            # Обработка остальных ошибок
            logger.error(f"🚫 Ошибка: {str(e)}")
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🔄 Попробовать снова", callback_data="create_giveaway")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(1)
            error_message = "❌ Упс! Что-то пошло не так 😔 Попробуем ещё раз?"
            try:
                # Пробуем отредактировать текст сообщения
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=data.get('last_message_id'),
                    text=error_message,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            except aiogram.exceptions.TelegramBadRequest as te:
                # Если сообщение не содержит текста (например, это медиа без подписи)
                if "there is no text in the message to edit" in str(te):
                    try:
                        # Пробуем отредактировать подпись медиа
                        await bot.edit_message_caption(
                            chat_id=message.chat.id,
                            message_id=data.get('last_message_id'),
                            caption=error_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    except Exception as edit_caption_error:
                        # Если не удалось отредактировать подпись, отправляем новое сообщение
                        logger.error(f"Не удалось отредактировать подпись: {str(edit_caption_error)}")
                        await send_message_with_image(
                            bot,
                            message.chat.id,
                            error_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                else:
                    # Если ошибка не связана с отсутствием текста, пробрасываем её дальше
                    raise te
