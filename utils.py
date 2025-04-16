import logging
import re
import boto3
from botocore.client import Config
from aiogram import Bot
from aiogram.types import Message, LinkPreviewOptions, InputMediaPhoto, InlineKeyboardMarkup, InputMediaVideo, \
    InputMediaAnimation
import aiogram.exceptions
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any
from aiogram.enums import ChatMemberStatus
from datetime import datetime
import pytz
import json
import random
import string

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация Yandex Cloud S3
YANDEX_ACCESS_KEY = 'YCAJEDluWSn-XI0tyGyfwfnVL'
YANDEX_SECRET_KEY = 'YCPkR9H9Ucebg6L6eMGvtfKuFIcO_MK7gyiffY6H'
YANDEX_BUCKET_NAME = 'raffle'
YANDEX_ENDPOINT_URL = 'https://storage.yandexcloud.net'
YANDEX_REGION = 'ru-central1'

# Инициализация S3 клиента
s3_client = boto3.client(
    's3',
    region_name=YANDEX_REGION,
    aws_access_key_id=YANDEX_ACCESS_KEY,
    aws_secret_access_key=YANDEX_SECRET_KEY,
    endpoint_url=YANDEX_ENDPOINT_URL,
    config=Config(signature_version='s3v4')
)

# Инструкции форматирования
FORMATTING_GUIDE_INITIAL = """
<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Создайте описание для розыгрыша\n  
Добавьте медиафайл (фото, видео или GIF до 10 МБ), чтобы сделать пост ещё круче

Поддерживаемые форматы текста:
<blockquote expandable>> Цитаты для выделения
> Жирный: <b>текст</b>
> Кастомные эмодзи: <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
> Курсив: <i>текст</i>
> Подчёркнутый: <u>текст</u>
> Зачёркнутый: <s>текст</s>
> Моноширинный: <code>текст</code>
> Скрытый: <tg-spoiler>текст</tg-spoiler>
> Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a></blockquote>

Переменные:
> <code>{win}</code> — количество победителей  
> <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (по МСК)  
"""

FORMATTING_GUIDE_UPDATE = """Поддерживаемые форматы текста:
<blockquote expandable>> Цитаты для выделения
> Жирный: <b>текст</b>
> Кастомные эмодзи: <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
> Курсив: <i>текст</i>
> Подчёркнутый: <u>текст</u>
> Зачёркнутый: <s>текст</s>
> Моноширинный: <code>текст</code>
> Скрытый: <tg-spoiler>текст</tg-spoiler>
> Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a></blockquote>

Переменные:
- <code>{win}</code> — количество победителей  
- <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (по МСК)
"""

FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>> Цитаты для выделения
> Жирный: <b>текст</b>
> Кастомные эмодзи: <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
> Курсив: <i>текст</i>
> Подчёркнутый: <u>текст</u>
> Зачёркнутый: <s>текст</s>
> Моноширинный: <code>текст</code>
> Скрытый: <tg-spoiler>текст</tg-spoiler>
> Ссылка: <a href="https://t.me/snapi">текст</a></blockquote>
"""

FORMATTING_GUIDE2 = """
Поддерживаемые форматы текста:
<blockquote expandable>> Цитаты для выделения
> Жирный: <b>текст</b>
> Кастомные эмодзи: <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
> Курсив: <i>текст</i>
> Подчёркнутый: <u>текст</u>
> Зачёркнутый: <s>текст</s>
> Моноширинный: <code>текст</code>
> Скрытый: <tg-spoiler>текст</tg-spoiler>
> Ссылка: <a href="https://t.me/snapi">текст</a></blockquote>

Переменные:
> <code>{win}</code> — количество победителей  
> <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (по МСК)  
"""

MAX_CONGRATS_LENGTH = 1000
MAX_CAPTION_LENGTH = 2500
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 10
MAX_WINNERS = 100
DEFAULT_IMAGE_URL = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'

async def get_file_url(bot: Bot, file_id: str) -> str:
    try:
        file = await bot.get_file(file_id)
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{file.file_path}"
        return file_url
    except Exception as e:
        logger.error(f"Ошибка получения URL файла: {str(e)}")
        raise

def count_length_with_custom_emoji(text: str) -> int:
    # Удаляем HTML-теги
    tag_pattern = r'<[^>]+>'
    cleaned_text = re.sub(tag_pattern, '', text)
    length = len(cleaned_text)
    length += text.count('{data}') * (16 - len('{data}'))

    return length

def generate_unique_code(cursor) -> str:
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cursor.execute("SELECT COUNT(*) FROM giveaways WHERE id = %s", (code,))
        if cursor.fetchone()[0] == 0:
            return code

def strip_html_tags(text: str) -> str:
    """Удаляет HTML-теги из текста"""
    return re.sub(r'<[^>]+>', '', text)

def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    if count_length_with_custom_emoji(text) <= max_length:
        return text

    tag_pattern = r'<[^>]+>'
    cleaned_text = re.sub(tag_pattern, '', text)

    if len(cleaned_text) <= max_length:
        return text

    truncated_cleaned = cleaned_text[:max_length - len(suffix)] + suffix

    result = ""
    current_cleaned_pos = 0
    tag_buffer = ""
    in_tag = False
    original_pos = 0

    while original_pos < len(text) and current_cleaned_pos < len(truncated_cleaned):
        char = text[original_pos]

        if char == '<':
            in_tag = True
            tag_buffer += char
        elif char == '>' and in_tag:
            in_tag = False
            tag_buffer += char
            result += tag_buffer
            tag_buffer = ""
        elif in_tag:
            tag_buffer += char
        else:
            if current_cleaned_pos < len(truncated_cleaned):
                result += char
                current_cleaned_pos += 1
        original_pos += 1

    if tag_buffer:
        result += tag_buffer

    return result

async def send_message_with_image(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None,
                                 parse_mode: str = 'HTML', entities=None, image_url: str = None,
                                 previous_message_type: str = None) -> Message | None:
    image_url = image_url or 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
    full_text = f"<a href=\"{image_url}\">\u200B</a>{text}"
    link_preview_options = LinkPreviewOptions(show_above_text=True)
    current_message_type = 'image'

    try:
        if message_id and previous_message_type and previous_message_type != current_message_type:
            logger.info(f"Тип медиа изменился для сообщения {message_id} на {current_message_type}, отправляем новое")
            return await bot.send_message(
                chat_id=chat_id,
                text=full_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                entities=entities,
                link_preview_options=link_preview_options
            )
        elif message_id:
            try:
                return await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=full_text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    entities=entities,
                    link_preview_options=link_preview_options
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e).lower():
                    logger.warning(f"Сообщение {message_id} не найдено для редактирования, отправляем новое")
                    return await bot.send_message(
                        chat_id=chat_id,
                        text=full_text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                        entities=entities,
                        link_preview_options=link_preview_options
                    )
                elif "there is no text in the message to edit" in str(e).lower():
                    logger.info(f"Сообщение {message_id} содержит медиа, отправляем новое")
                    return await bot.send_message(
                        chat_id=chat_id,
                        text=full_text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode,
                        entities=entities,
                        link_preview_options=link_preview_options
                    )
                elif "can't parse entities" in str(e).lower():
                    logger.error(f"HTML parsing error in message: {full_text}")
                    raise
                else:
                    raise
        else:
            return await bot.send_message(
                chat_id=chat_id,
                text=full_text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                entities=entities,
                link_preview_options=link_preview_options
            )
    except Exception as e:
        logger.error(f"Error in send_message_with_image: {str(e)}")
        return None

async def send_message_with_photo(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None,
                                 parse_mode: str = 'HTML', image_url: str = None,
                                 previous_message_type: str = None) -> Message | None:
    image_url = image_url or 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
    current_message_type = 'photo'

    try:
        if message_id:
            try:
                return await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=InputMediaPhoto(
                        media=image_url,
                        caption=text,
                        parse_mode=parse_mode
                    ),
                    reply_markup=reply_markup
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e).lower():
                    logger.warning(f"Сообщение {message_id} не найдено для редактирования, отправляем новое")
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                elif "message is not modified" in str(e).lower():
                    logger.info(f"Сообщение {message_id} не изменено, пропускаем")
                    return None
                elif "message media can be edited only to the media of the same type" in str(e).lower():
                    logger.info(f"Тип медиа изменился для сообщения {message_id} на {current_message_type}, отправляем новое")
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                else:
                    logger.error(f"Ошибка редактирования сообщения {message_id}: {str(e)}")
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
        else:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        logger.error(f"Error in send_message_with_photo: {str(e)}")
        return None

async def send_message_with_video(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None,
                                 parse_mode: str = 'HTML', video_url: str = None,
                                 previous_message_type: str = None) -> Message | None:
    video_url = video_url or 'https://storage.yandexcloud.net/raffle/snapi/snapi2.mp4'
    current_message_type = 'video'

    try:
        if message_id:
            try:
                return await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=InputMediaVideo(
                        media=video_url,
                        caption=text,
                        parse_mode=parse_mode
                    ),
                    reply_markup=reply_markup
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e).lower():
                    logger.warning(f"Сообщение {message_id} не найдено для редактирования, отправляем новое")
                    return await bot.send_video(
                        chat_id=chat_id,
                        video=video_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                elif "message is not modified" in str(e).lower():
                    logger.info(f"Сообщение {message_id} не изменено, пропускаем")
                    return None
                elif "message media can be edited only to the media of the same type" in str(e).lower():
                    logger.info(f"Тип медиа изменился для сообщения {message_id} на {current_message_type}, отправляем новое")
                    return await bot.send_video(
                        chat_id=chat_id,
                        video=video_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                else:
                    logger.error(f"Ошибка редактирования сообщения {message_id}: {str(e)}")
                    return await bot.send_video(
                        chat_id=chat_id,
                        video=video_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
        else:
            return await bot.send_video(
                chat_id=chat_id,
                video=video_url,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        logger.error(f"Error in send_message_with_video: {str(e)}")
        return None

async def send_message_with_animation(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None,
                                     parse_mode: str = 'HTML', animation_url: str = None,
                                     previous_message_type: str = None) -> Message | None:
    animation_url = animation_url or 'https://storage.yandexcloud.net/raffle/snapi/snapi2.mp4'
    current_message_type = 'animation'

    try:
        if message_id:
            try:
                return await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=InputMediaAnimation(
                        media=animation_url,
                        caption=text,
                        parse_mode=parse_mode
                    ),
                    reply_markup=reply_markup
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e).lower():
                    logger.warning(f"Сообщение {message_id} не найдено для редактирования, отправляем новое")
                    return await bot.send_animation(
                        chat_id=chat_id,
                        animation=animation_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                elif "message is not modified" in str(e).lower():
                    logger.info(f"Сообщение {message_id} не изменено, пропускаем")
                    return None
                elif "message media can be edited only to the media of the same type" in str(e).lower():
                    logger.info(f"Тип медиа изменился для сообщения {message_id} на {current_message_type}, отправляем новое")
                    return await bot.send_animation(
                        chat_id=chat_id,
                        animation=animation_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                else:
                    logger.error(f"Ошибка редактирования сообщения {message_id}: {str(e)}")
                    return await bot.send_animation(
                        chat_id=chat_id,
                        animation=animation_url,
                        caption=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
        else:
            return await bot.send_animation(
                chat_id=chat_id,
                animation=animation_url,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    except Exception as e:
        logger.error(f"Error in send_message_with_animation: {str(e)}")
        return None

async def send_message_auto(
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup = None,
    message_id: int = None,
    parse_mode: str = 'HTML',
    entities=None,
    image_url: str = None,
    media_type: str = None,
    previous_message_type: str = None
) -> Message | None:
    """
    Автоматический выбор между отправкой фото, видео, GIF или текста на основе типа медиа и длины текста.
    Если текст слишком длинный для медиа, переключается на image.
    Если отправка с медиа не удалась, отправляет без медиа.
    Удаляет предыдущее сообщение при переходе с photo/video/animation/gif (≤800) на image (1024-2500).

    Args:
        bot: Экземпляр бота.
        chat_id: ID чата.
        text: Текст сообщения.
        reply_markup: Клавиатура.
        message_id: ID сообщения для редактирования (если None, отправляется новое).
        parse_mode: Режим парсинга ('HTML', 'Markdown', None).
        entities: Сущности сообщения (для send_message_with_image).
        image_url: URL медиа.
        media_type: Тип медиа ('photo', 'video', 'animation', 'gif', None).
        previous_message_type: Тип предыдущего сообщения ('photo', 'video', 'animation', 'gif', 'image', None).

    Returns:
        Message | None: Отправленное сообщение или None при ошибке.
    """
    message_length = count_length_with_custom_emoji(text)
    caption_limit = 1024
    max_image_length = 2500

    # Map 'gif' to 'animation' for consistency
    normalized_media_type = 'animation' if media_type == 'gif' else media_type
    normalized_previous_type = 'animation' if previous_message_type == 'gif' else previous_message_type

    # Determine message type: prioritize image for long captions
    if normalized_media_type in ['photo', 'video', 'animation'] and message_length > caption_limit and message_length <= max_image_length:
        current_message_type = 'image'
        logger.info(f"Переключено на тип image для сообщения {message_id or 'нового'} из-за длинного текста ({message_length} > {caption_limit}, ≤ {max_image_length})")
    else:
        current_message_type = normalized_media_type or ('photo' if message_length <= 800 else 'image')

    logger.info(f"send_message_auto: chat_id={chat_id}, message_id={message_id}, image_url={image_url}, type={current_message_type}")

    # Delete previous message if switching from photo/video/animation/gif (≤800) to image (1024-2500)
    if (message_id and normalized_previous_type in ['photo', 'video', 'animation'] and
        current_message_type == 'image' and message_length > caption_limit and message_length <= max_image_length):
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            logger.info(f"Удалено сообщение {message_id} из-за смены типа на image для длинного текста ({message_length} символов)")
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение {message_id}: {str(e)}")

    try:
        if image_url:
            if current_message_type == 'photo':
                return await send_message_with_photo(
                    bot=bot,
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    message_id=message_id,
                    parse_mode=parse_mode,
                    image_url=image_url,
                    previous_message_type=normalized_previous_type
                )
            elif current_message_type == 'video':
                return await send_message_with_video(
                    bot=bot,
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    message_id=message_id,
                    parse_mode=parse_mode,
                    video_url=image_url,
                    previous_message_type=normalized_previous_type
                )
            elif current_message_type == 'animation':
                return await send_message_with_animation(
                    bot=bot,
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    message_id=message_id,
                    parse_mode=parse_mode,
                    animation_url=image_url,
                    previous_message_type=normalized_previous_type
                )
            elif current_message_type == 'image':
                return await send_message_with_image(
                    bot=bot,
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    message_id=message_id,
                    parse_mode=parse_mode,
                    entities=entities,
                    image_url=image_url,
                    previous_message_type=normalized_previous_type
                )
        else:
            if message_id:
                try:
                    return await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=message_id,
                        text=text,
                        reply_markup=reply_markup,
                        parse_mode=parse_mode
                    )
                except aiogram.exceptions.TelegramBadRequest as e:
                    if "message to edit not found" in str(e).lower():
                        logger.warning(f"Сообщение {message_id} не найдено для редактирования, отправляем новое")
                        return await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=parse_mode
                        )
                    elif "message is not modified" in str(e).lower():
                        logger.info(f"Сообщение {message_id} не изменено, пропускаем")
                        return None
                    elif "there is no text in the message to edit" in str(e).lower():
                        logger.info(f"Сообщение {message_id} содержит медиа, отправляем новое")
                        return await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=parse_mode
                        )
                    else:
                        logger.error(f"Ошибка редактирования сообщения {message_id}: {str(e)}")
                        return await bot.send_message(
                            chat_id=chat_id,
                            text=text,
                            reply_markup=reply_markup,
                            parse_mode=parse_mode
                        )
            else:
                return await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode
                )
    except Exception as e:
        logger.error(f"Ошибка в send_message_auto: {str(e)}")
        return None

async def check_and_end_giveaways(bot: Bot, conn, cursor):
    while True:
        now = datetime.now(pytz.utc)
        try:
            cursor.execute("SELECT * FROM giveaways WHERE is_active = %s", ('true',))
            giveaways = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            giveaways = [dict(zip(columns, row)) for row in giveaways]

            if giveaways:
                for giveaway in giveaways:
                    end_time = giveaway['end_time']
                    if isinstance(end_time, str):
                        try:
                            end_time = datetime.fromisoformat(end_time)
                        except ValueError as ve:
                            logger.error(f"Invalid end_time format for giveaway {giveaway['id']}: {str(ve)}")
                            continue
                    if end_time <= now:
                        try:
                            await end_giveaway(bot, giveaway['id'], conn, cursor)
                        except Exception as e:
                            logger.error(f"Error ending giveaway {giveaway['id']}: {str(e)}")
        except Exception as e:
            logger.error(f"Error fetching active giveaways: {str(e)}")

        await asyncio.sleep(30)

async def end_giveaway(bot: Bot, giveaway_id: str, conn, cursor, notify_creator: bool = True):
    try:
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = cursor.fetchone()
        if not giveaway:
            logger.error(f"Error fetching giveaway: Giveaway {giveaway_id} not found")
            return
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, giveaway))
        logger.debug(f"Ending giveaway {giveaway_id}: {giveaway}")

        participants = []
        limit = 1000
        offset = 0
        while True:
            cursor.execute(
                "SELECT user_id FROM participations WHERE giveaway_id = %s LIMIT %s OFFSET %s",
                (giveaway_id, limit, offset)
            )
            batch = cursor.fetchall()
            if not batch:
                break
            participants.extend([{'user_id': row[0]} for row in batch])
            offset += limit
            if len(batch) < limit:
                break

        logger.info(f"Total participants fetched for giveaway {giveaway_id}: {len(participants)}")

        winners = await select_random_winners(bot, participants,
                                              min(len(participants), giveaway['winner_count']),
                                              giveaway_id, conn, cursor)

        cursor.execute(
            "UPDATE giveaways SET is_active = %s, is_completed = %s WHERE id = %s",
            ('false', 'true', giveaway_id)
        )
        conn.commit()
        logger.info(f"Giveaway {giveaway_id} marked as completed (is_active = 'false', is_completed = 'true')")

        if winners:
            for index, winner in enumerate(winners, start=1):
                cursor.execute(
                    """
                    INSERT INTO giveaway_winners (giveaway_id, user_id, username, name, place)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (giveaway_id, winner['user_id'], winner['username'], winner.get('name', ''), index)
                )
            conn.commit()
            logger.info(f"Saved {len(winners)} winners for giveaway {giveaway_id}")

        await notify_winners_and_publish_results(bot, conn, cursor, giveaway, winners, notify_creator=notify_creator)

        new_giveaway = giveaway.copy()
        new_giveaway.pop('id', None)
        new_giveaway['is_active'] = 'false'
        new_giveaway['is_completed'] = 'false'
        new_giveaway['created_at'] = None
        new_giveaway['end_time'] = giveaway['end_time']

        for key, value in new_giveaway.items():
            if isinstance(value, (dict, list)):
                logger.debug(f"Converting field {key} to JSON string: {value}")
                new_giveaway[key] = json.dumps(value)

        logger.debug(f"Prepared new_giveaway for insertion: {new_giveaway}")

        new_giveaway_id = generate_unique_code(cursor)
        new_giveaway['id'] = new_giveaway_id

        columns = list(new_giveaway.keys())
        placeholders = ', '.join(['%s'] * len(columns))
        cursor.execute(
            f"INSERT INTO giveaways ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
            list(new_giveaway.values())
        )
        inserted_id = cursor.fetchone()[0]
        logger.info(f"Created new giveaway template with id {inserted_id} based on giveaway {giveaway_id}")

        cursor.execute("SELECT * FROM congratulations WHERE giveaway_id = %s", (giveaway_id,))
        congratulations = cursor.fetchall()
        if congratulations:
            congrats_columns = [desc[0] for desc in cursor.description]
            for congrat in congratulations:
                congrat_dict = dict(zip(congrats_columns, congrat))
                congrat_dict.pop('id', None)
                congrat_dict['giveaway_id'] = new_giveaway_id
                columns = list(congrat_dict.keys())
                placeholders = ', '.join(['%s'] * len(columns))
                cursor.execute(
                    f"INSERT INTO congratulations ({', '.join(columns)}) VALUES ({placeholders})",
                    list(congrat_dict.values())
                )
            conn.commit()
            logger.info(f"Copied congratulations to new giveaway template {new_giveaway_id}")

        logger.info(f"Giveaway {giveaway_id} ended and duplicated as template with new id {new_giveaway_id}")

    except Exception as e:
        logger.error(f"Error in end_giveaway for giveaway {giveaway_id}: {str(e)}")
        conn.rollback()

async def check_participant(bot: Bot, user_id: int, communities: List[Dict[str, Any]]) -> bool:
    """Проверка подписки участника на все указанные каналы."""
    for community in communities:
        try:
            member = await bot.get_chat_member(chat_id=community['community_id'], user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return False
        except Exception as e:
            logger.error(
                f"Error checking membership for user {user_id} in community {community['community_id']}: {str(e)}")
            return False
    return True

async def select_random_winners(bot: Bot, participants: List[Dict[str, Any]], winner_count: int, giveaway_id: str,
                                conn, cursor) -> List[Dict[str, Any]]:
    random.seed(giveaway_id)

    giveaway_communities = await get_giveaway_communities(conn, cursor, giveaway_id)
    if not giveaway_communities:
        logger.warning(f"No communities found for giveaway {giveaway_id}, all participants considered valid")
        shuffled_participants = participants.copy()
        random.shuffle(shuffled_participants)
        winners = random.sample(shuffled_participants, min(winner_count, len(shuffled_participants)))
    else:
        tasks = [check_participant(bot, p['user_id'], giveaway_communities) for p in participants]
        results = await asyncio.gather(*tasks)
        valid_participants = [p for p, valid in zip(participants, results) if valid]

        logger.info(
            f"Found {len(valid_participants)} valid participants out of {len(participants)} for giveaway {giveaway_id}")

        if valid_participants:
            random.shuffle(valid_participants)
            winners = random.sample(valid_participants, min(winner_count, len(valid_participants)))
        else:
            winners = []
            logger.warning(f"No valid participants found for giveaway {giveaway_id}")

    winner_details = []
    for winner in winners:
        user_id = winner['user_id']
        try:
            user = await bot.get_chat_member(user_id, user_id)
            winner_details.append({
                'user_id': user_id,
                'username': user.user.username or f"user{user_id}",
                'name': user.user.first_name
            })
        except Exception as e:
            logger.error(f"Error fetching user details for {user_id}: {e}")
            winner_details.append({
                'user_id': user_id,
                'username': f"user{user_id}",
                'name': ""
            })

    logger.info(f"Selected winners for giveaway {giveaway_id}: {[w['user_id'] for w in winner_details]}")
    return winner_details

async def update_giveaway_status(conn, cursor, giveaway_id: str, is_active: str):
    try:
        cursor.execute(
            "UPDATE giveaways SET is_active = %s WHERE id = %s",
            (is_active, giveaway_id)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error updating giveaway status for giveaway {giveaway_id}: {str(e)}")
        conn.rollback()

async def get_giveaway_communities(conn, cursor, giveaway_id: str) -> List[Dict[str, Any]]:
    try:
        cursor.execute(
            "SELECT community_id FROM giveaway_communities WHERE giveaway_id = %s",
            (giveaway_id,)
        )
        rows = cursor.fetchall()
        return [{'community_id': row[0]} for row in rows]
    except Exception as e:
        logger.error(f"Error fetching giveaway communities for giveaway {giveaway_id}: {str(e)}")
        return []

async def notify_winners_and_publish_results(bot: Bot, conn, cursor, giveaway: Dict[str, Any],
                                             winners: List[Dict[str, Any]], notify_creator: bool = True):
    participant_counter_tasks = giveaway.get('participant_counter_tasks')
    target_chat_ids = []
    channel_links = []
    if participant_counter_tasks:
        try:
            tasks = participant_counter_tasks if isinstance(participant_counter_tasks, list) else []
            target_chat_ids = [task['chat_id'] for task in tasks if 'chat_id' in task]
            for chat_id in set(target_chat_ids):
                try:
                    chat = await bot.get_chat(chat_id)
                    channel_name = chat.title
                    invite_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                    channel_links.append(f"<a href=\"{invite_link}\">{channel_name}</a>")
                except Exception as e:
                    logger.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                    channel_links.append("Неизвестный канал")
        except Exception as e:
            logger.error(f"Error processing participant_counter_tasks for giveaway {giveaway['id']}: {str(e)}")

    if not target_chat_ids:
        logger.warning(f"No target chat_ids found in participant_counter_tasks for giveaway {giveaway['id']}. Results will not be published in channels.")

    if winners:
        winners_formatted = []
        for idx, winner in enumerate(winners, start=1):
            medal = ""
            if idx == 1:
                medal = "<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> "
            elif idx == 2:
                medal = "<tg-emoji emoji-id='5447203607294265305'>🥈</tg-emoji> "
            elif idx == 3:
                medal = "<tg-emoji emoji-id='5453902265922376865'>🥉</tg-emoji> "
            winners_formatted.append(
                f"{medal}{idx}. <a href='tg://user?id={winner['user_id']}'>@{winner['username']}</a>")

        winners_list = '\n'.join(winners_formatted)
        result_message = f"""<b>Розыгрыш завершен <tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji></b>

{giveaway['name']}

<b>Победители:</b> 
<blockquote expandable>
{winners_list}
</blockquote>
"""
    else:
        result_message = f"""<b>Розыгрыш завершен</b>

{giveaway['name']}

К сожалению, в этом розыгрыше не было участников.
"""

    if winners and len(winners) < giveaway['winner_count']:
        result_message += f"""
Не все призовые места были распределены.
"""

    if channel_links:
        result_message_for_creator = result_message + f"""<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Результаты опубликованы в:</b> {', '.join(channel_links)}
"""
    else:
        result_message_for_creator = result_message

    channel_keyboard = InlineKeyboardBuilder()
    channel_keyboard.button(text="Результаты", url=f"https://t.me/Snapi/app?startapp={giveaway['id']}")

    image_url = None
    if giveaway['media_type'] and giveaway['media_file_id']:
        image_url = giveaway['media_file_id']
        if not image_url.startswith('http'):
            image_url = await get_file_url(bot, giveaway['media_file_id'])

    for chat_id in target_chat_ids:
        try:
            if image_url:
                await send_message_auto(
                    bot,
                    chat_id=int(chat_id),
                    text=result_message,
                    reply_markup=channel_keyboard.as_markup(),
                    parse_mode='HTML',
                    image_url=image_url
                )
            else:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=result_message,
                    reply_markup=channel_keyboard.as_markup(),
                    parse_mode='HTML'
                )
        except aiogram.exceptions.TelegramBadRequest as e:
            logger.error(f"Error publishing results in chat {chat_id}: {e}")
        except Exception as e:
            logger.error(f"Error publishing results in chat {chat_id}: {e}")

    cursor.execute("SELECT place, message FROM congratulations WHERE giveaway_id = %s", (giveaway['id'],))
    congrats_rows = cursor.fetchall()
    congrats_messages = {row[0]: row[1] for row in congrats_rows}

    WINNER_EFFECT_ID = "5046509860389126442"

    for index, winner in enumerate(winners, start=1):
        try:
            congrats_message = congrats_messages.get(index,
                                                     f"<b>Поздравляем</b> Вы выиграли в розыгрыше \"<i>{giveaway['name']}</i>\"")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text="Результаты",
                url=f"https://t.me/Snapi/app?startapp={giveaway['id']}"
            )
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await bot.send_message(
                chat_id=winner['user_id'],
                text=congrats_message,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                message_effect_id=WINNER_EFFECT_ID
            )
            logger.info(f"Sent winning message with effect_id {WINNER_EFFECT_ID} to user {winner['user_id']}")
        except Exception as e:
            logger.error(f"Error notifying winner {winner['user_id']}: {e}")

    if notify_creator:
        creator_id = giveaway.get('user_id')
        if creator_id:
            creator_keyboard = InlineKeyboardBuilder()
            creator_keyboard.button(text="В меню", callback_data="back_to_main_menu")

            try:
                if image_url:
                    await send_message_auto(
                        bot,
                        chat_id=creator_id,
                        text=result_message_for_creator,
                        reply_markup=creator_keyboard.as_markup(),
                        parse_mode='HTML',
                        image_url=image_url
                    )
                else:
                    await bot.send_message(
                        chat_id=creator_id,
                        text=result_message_for_creator,
                        reply_markup=creator_keyboard.as_markup(),
                        parse_mode='HTML'
                    )
            except Exception as e:
                logger.error(f"Error notifying creator {creator_id}: {str(e)}")

async def check_usernames(bot: Bot, conn, cursor):
    try:
        cursor.execute("SELECT user_id, telegram_username FROM users")
        users = cursor.fetchall()
        users = [{'user_id': row[0], 'telegram_username': row[1]} for row in users]

        for user in users:
            try:
                chat = await bot.get_chat(user['user_id'])
                current_username = chat.username

                if current_username != user.get('telegram_username'):
                    cursor.execute(
                        "UPDATE users SET telegram_username = %s WHERE user_id = %s",
                        (current_username, user['user_id'])
                    )
                    conn.commit()
                    logger.info(
                        f"Updated username for user {user['user_id']}: {user.get('telegram_username')} -> {current_username}")
            except Exception as e:
                logger.error(f"Error checking user {user['user_id']}: {str(e)}")

        cursor.execute("SELECT community_id, community_username, community_name FROM bound_communities")
        communities = cursor.fetchall()
        communities = [
            {'community_id': row[0], 'community_username': row[1], 'community_name': row[2]}
            for row in communities
        ]

        for community in communities:
            try:
                chat = await bot.get_chat(community['community_id'])
                current_username = chat.username or chat.title
                current_name = chat.title

                if (current_username != community.get('community_username') or
                        current_name != community.get('community_name')):
                    cursor.execute(
                        """
                        UPDATE bound_communities 
                        SET community_username = %s, community_name = %s 
                        WHERE community_id = %s
                        """,
                        (current_username, current_name, community['community_id'])
                    )
                    cursor.execute(
                        """
                        UPDATE giveaway_communities 
                        SET community_username = %s, community_name = %s 
                        WHERE community_id = %s
                        """,
                        (current_username, current_name, community['community_id'])
                    )
                    conn.commit()

                    logger.info(
                        f"Обновлены данные для сообщества {community['community_id']}:\n"
                        f"Username: {community.get('community_username')} -> {current_username}\n"
                        f"Name: {community.get('community_name')} -> {current_name}"
                    )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    community_name = community.get('community_username', 'Неизвестное сообщество')
                    logger.warning(f"Нет доступа к сообществу {community_name} (ID: {community['community_id']}). "
                                    f"Возможно, бот был удален из администраторов или сообщество было удалено.")
                else:
                    logger.error(f"Неожиданная ошибка при проверке сообщества {community['community_id']}: {str(e)}")
            except Exception as e:
                logger.error(f"Ошибка при проверке сообщества {community['community_id']}: {str(e)}")

    except Exception as e:
        logger.error(f"Ошибка в функции check_usernames: {str(e)}")
        conn.rollback()
