from utils import end_giveaway, send_message_with_image
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiogram.exceptions
import json
import boto3
from botocore.client import Config
import requests
import re
from aiogram.types import CallbackQuery, FSInputFile
from typing import Dict, List, Tuple, Any

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

FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>- Цитата
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji></blockquote>
"""

FORMATTING_GUIDE2 = """
Поддерживаемые форматы текста:
<blockquote expandable>- Цитата
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji></blockquote>

<b>Переменные</b>
Используйте их для автоматической подстановки данных:  
- <code>{win}</code> — количество победителей  
- <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (МСК)  

<b>Примечание</b>
Максимальное количество кастомных эмодзи в одном сообщении — 100. Превышение этого лимита может привести к некорректному отображению.
"""

def strip_html_tags(text: str) -> str:
    """Удаляет HTML-теги из текста 🧹"""
    return re.sub(r'<[^>]+>', '', text)

def count_length_with_custom_emoji(text: str) -> int:
    """Подсчитывает длину текста, считая кастомные эмодзи как 1 символ."""
    emoji_pattern = r'<tg-emoji emoji-id="[^"]+">[^<]+</tg-emoji>'
    custom_emojis = re.findall(emoji_pattern, text)
    cleaned_text = text
    for emoji in custom_emojis:
        cleaned_text = cleaned_text.replace(emoji, ' ')
    return len(cleaned_text)

def fetch_giveaway_data(cursor: Any, query: str, params: Tuple) -> List[Dict[str, Any]]:
    """Извлекает данные из базы и преобразует их в список словарей."""
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

class EditGiveawayStates(StatesGroup):
    waiting_for_new_name_active = State()  # ✏️
    waiting_for_new_description_active = State()  # 📜
    waiting_for_new_winner_count_active = State()  # 🏆
    waiting_for_new_end_time_active = State()  # ⏰
    waiting_for_new_media_active = State()  # 🖼️

async def upload_to_storage(file_content: bytes, filename: str) -> Tuple[bool, str]:
    """Загружает файл в хранилище 📤"""
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой! Максимум: {MAX_MEDIA_SIZE_MB} МБ 😔"

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        presigned_url = s3_client.generate_presigned_url(
            'put_object',
            Params={
                'Bucket': YANDEX_BUCKET_NAME,
                'Key': unique_filename,
                'ContentType': 'application/octet-stream'
            },
            ExpiresIn=3600
        )

        response = requests.put(
            presigned_url,
            data=file_content,
            headers={'Content-Type': 'application/octet-stream'}
        )

        if response.status_code == 200:
            public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"
            logger.info(f"✅ Файл загружен: {unique_filename}")
            return True, public_url
        else:
            logger.error(f"❌ Ошибка загрузки: {response.status_code}")
            raise Exception(f"Не удалось загрузить: {response.status_code}")

    except Exception as e:
        logger.error(f"🚫 Ошибка: {str(e)}")
        return False, f"❌ Ошибка загрузки: {str(e)}"

def get_json_field(cursor, query, params):
    """Вспомогательная функция для безопасного извлечения JSON-полей из базы."""
    cursor.execute(query, params)
    result = cursor.fetchone()[0]
    if result is None:
        return []
    if isinstance(result, (str, bytes, bytearray)):
        return json.loads(result)
    if isinstance(result, list):
        return result  # Если данные уже список, используем их как есть
    raise ValueError(f"Неподдерживаемый тип данных для JSON-поля: {type(result)}")

def register_active_giveaways_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    """Регистрирует обработчики для управления активными розыгрышами 🎁"""

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('view_active_giveaway:'))
    async def process_view_active_giveaway(callback_query: CallbackQuery):
        """Просмотр активного розыгрыша 👀"""
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                return

            cursor.execute("SELECT COUNT(*) FROM participations WHERE giveaway_id = %s", (giveaway_id,))
            participants_count = cursor.fetchone()[0]

            published_messages = get_json_field(cursor, "SELECT published_messages FROM giveaways WHERE id = %s", (giveaway_id,)) if giveaway['published_messages'] else []
            channel_info = ""
            if published_messages:
                channel_links = []
                unique_chat_ids = set(msg['chat_id'] for msg in published_messages)
                for chat_id in unique_chat_ids:
                    try:
                        chat = await bot.get_chat(chat_id)
                        channel_name = chat.title
                        invite_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                        channel_links.append(f"<a href=\"{invite_link}\">{channel_name}</a>")
                    except Exception as e:
                        logger.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                        channel_links.append("Неизвестный канал")
                channel_info = f"\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Опубликовано в:</b> {', '.join(channel_links)}"

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = (giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M (МСК)')
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            giveaway_info = f"""
{giveaway['name']}

{formatted_description}

<tg-emoji emoji-id='5451882707875276247'>🕯</tg-emoji> <b>Участников:</b> {participants_count}
{channel_info}
"""

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Редактировать", callback_data=f"edit_active_post:{giveaway_id}")
            keyboard.button(text="🎉 Сообщение победителям", callback_data=f"message_winners_active:{giveaway_id}")
            keyboard.button(text="⏹️ Завершить", callback_data=f"confirm_force_end_giveaway:{giveaway_id}")
            keyboard.button(text="📱 Открыть", url=f"https://t.me/Snapi/app?startapp={giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            keyboard.adjust(1)

            await bot.answer_callback_query(callback_query.id)
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': types.InputMediaPhoto,
                    'gif': types.InputMediaAnimation,
                    'video': types.InputMediaVideo
                }
                await bot.edit_message_media(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    media=media_types[giveaway['media_type']](
                        media=giveaway['media_file_id'],
                        caption=giveaway_info,
                        parse_mode='HTML'
                    ),
                    reply_markup=keyboard.as_markup()
                )
            else:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка загрузки розыгрыша 😔")

    @dp.callback_query(lambda c: c.data.startswith('confirm_force_end_giveaway:'))
    async def process_confirm_force_end_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✅ Да", callback_data=f"force_end_giveaway:{giveaway_id}")
        keyboard.button(text="❌ Нет", callback_data=f"view_active_giveaway:{giveaway_id}")
        keyboard.adjust(2)

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> Вы уверены, что хотите завершить розыгрыш?",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
    async def process_force_end_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Завершаем розыгрыш...",
                message_id=callback_query.message.message_id
            )
            await end_giveaway(bot=bot, giveaway_id=giveaway_id, conn=conn, cursor=cursor)

            cursor.execute("SELECT participant_counter_tasks FROM giveaways WHERE id = %s", (giveaway_id,))
            participant_counter_tasks = get_json_field(cursor, "SELECT participant_counter_tasks FROM giveaways WHERE id = %s", (giveaway_id,)) if cursor.fetchone()[0] else []
            channel_links = []
            if participant_counter_tasks:
                unique_chat_ids = set(task['chat_id'] for task in participant_counter_tasks)
                for chat_id in unique_chat_ids:
                    try:
                        chat = await bot.get_chat(chat_id)
                        channel_name = chat.title
                        invite_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                        channel_links.append(f"<a href=\"{invite_link}\">{channel_name}</a>")
                    except Exception as e:
                        logger.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                        channel_links.append("Неизвестный канал")

            channel_info = f"\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Результаты опубликованы в:</b> {', '.join(channel_links)}" if channel_links else ""
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> Розыгрыш завершён!{channel_info}",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка при завершении 😔")

    @dp.callback_query(lambda c: c.data.startswith('edit_active_post:'))
    async def process_edit_active_post(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    async def _show_edit_menu_active(user_id: int, giveaway_id: str, message_id: int = None):
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, cursor.fetchone()))
        if not giveaway:
            await bot.send_message(user_id, "🔍 Розыгрыш не найден 😕")
            return

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name_active:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_description_active:{giveaway_id}")
        keyboard.button(text="🏆 Победители", callback_data=f"edit_winner_count_active:{giveaway_id}")
        keyboard.button(text="⏰ Дата", callback_data=f"change_end_date_active:{giveaway_id}")
        keyboard.button(text="🖼️ Медиа", callback_data=f"manage_media_active:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_active_giveaway:{giveaway_id}")
        keyboard.adjust(2, 2, 1, 1)

        media_display = "Медиа: отсутствует" if not giveaway['media_type'] else f"Медиа: {giveaway['media_type']}"
        dop_info = (
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}\n"
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> <b>{media_display}</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {(giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)"
        )

        giveaway_info = f"""
<b>Название:</b> {giveaway['name']}
<b>Описание:</b> {giveaway['description']}

{dop_info}

<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Что хотите изменить?
"""

        try:
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': types.InputMediaPhoto,
                    'gif': types.InputMediaAnimation,
                    'video': types.InputMediaVideo
                }
                await bot.edit_message_media(
                    chat_id=user_id,
                    message_id=message_id,
                    media=media_types[giveaway['media_type']](
                        media=giveaway['media_file_id'],
                        caption=giveaway_info,
                        parse_mode='HTML'
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
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.send_message(user_id, "<tg-emoji emoji-id='5422649047334794716'>😵</tg-emoji> Ошибка загрузки меню 😔")

    async def update_published_posts_active(giveaway_id: str, giveaway: Dict[str, Any]):
        try:
            published_messages = get_json_field(cursor, "SELECT published_messages FROM giveaways WHERE id = %s",
                                                (giveaway_id,))

            cursor.execute("SELECT COUNT(*) FROM participations WHERE giveaway_id = %s", (giveaway_id,))
            participants_count = cursor.fetchone()[0]

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = (giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M (МСК)')
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            new_post_text = f"""
{giveaway['name']}

{formatted_description}
"""

            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"🎉 Участвовать ({participants_count})",
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )

            for message in published_messages:
                try:
                    if giveaway['media_type'] and giveaway['media_file_id']:
                        media_types = {
                            'photo': types.InputMediaPhoto,
                            'gif': types.InputMediaAnimation,
                            'video': types.InputMediaVideo
                        }
                        await bot.edit_message_media(
                            chat_id=message['chat_id'],
                            message_id=message['message_id'],
                            media=media_types[giveaway['media_type']](
                                media=giveaway['media_file_id'],
                                caption=new_post_text,
                                parse_mode='HTML'
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    else:
                        await bot.edit_message_text(
                            chat_id=message['chat_id'],
                            message_id=message['message_id'],
                            text=new_post_text,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                except Exception as e:
                    logger.error(f"🚫 Ошибка обновления поста {message['message_id']}: {str(e)}")
        except Exception as e:
            logger.error(f"🚫 Ошибка в update_published_posts_active: {str(e)}")
            raise

    @dp.callback_query(lambda c: c.data.startswith('edit_name_active:'))
    async def process_edit_name_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT name FROM giveaways WHERE id = %s", (giveaway_id,))
        current_name = cursor.fetchone()[0]

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_name_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        message_text = (
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Текущее название: <b>{current_name}</b>\n\n"
            f"Отправьте новое название (до {MAX_NAME_LENGTH} символов):\n{FORMATTING_GUIDE}"
        )

        try:
            image = FSInputFile('image/opis.png')
            await bot.edit_message_media(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                media=types.InputMediaPhoto(
                    media=image,
                    caption=message_text,
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
        except aiogram.exceptions.TelegramBadRequest:
            image = FSInputFile('image/opis.png')
            sent_message = await bot.send_photo(
                chat_id=callback_query.from_user.id,
                photo=image,
                caption=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
            await state.update_data(last_message_id=sent_message.message_id)

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_name_active)
    async def process_new_name_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']
        new_name = message.html_text if message.text else ""

        text_length = count_length_with_custom_emoji(new_name)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        if not new_name or text_length > MAX_NAME_LENGTH:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/name.png'),
                    caption=f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название должно быть от 1 до {MAX_NAME_LENGTH} символов! Сейчас: {text_length}\n{FORMATTING_GUIDE}",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
            return

        if text_length > MAX_CAPTION_LENGTH:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/name.png'),
                    caption=f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название превышает лимит Telegram ({MAX_CAPTION_LENGTH} символов)! Сейчас: {text_length}\n{FORMATTING_GUIDE}",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
            return

        try:
            cursor.execute("UPDATE giveaways SET name = %s WHERE id = %s", (new_name, giveaway_id))
            conn.commit()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/name.png'),
                    caption="<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить название 😔",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_description_active:'))
    async def process_edit_description_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT description FROM giveaways WHERE id = %s", (giveaway_id,))
        current_description = cursor.fetchone()[0]

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_description_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        message_text = (
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Текущее описание: <b>{current_description}</b>\n\n"
            f"Отправьте новое описание (до {MAX_DESCRIPTION_LENGTH} символов):\n{FORMATTING_GUIDE2}"
        )

        try:
            image = FSInputFile('image/opis.png')
            await bot.edit_message_media(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                media=types.InputMediaPhoto(
                    media=image,
                    caption=message_text,
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
        except aiogram.exceptions.TelegramBadRequest:
            image = FSInputFile('image/opis.png')
            sent_message = await bot.send_photo(
                chat_id=callback_query.from_user.id,
                photo=image,
                caption=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
            await state.update_data(last_message_id=sent_message.message_id)

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_description_active)
    async def process_new_description_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']
        new_description = message.html_text if message.text else ""

        text_length = count_length_with_custom_emoji(new_description)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        if not new_description or text_length > MAX_DESCRIPTION_LENGTH:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/opis.png'),
                    caption=f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Описание должно быть от 1 до {MAX_DESCRIPTION_LENGTH} символов! Сейчас: {text_length}\n{FORMATTING_GUIDE2}",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
            return

        if text_length > MAX_CAPTION_LENGTH:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/opis.png'),
                    caption=f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Описание превышает лимит Telegram ({MAX_CAPTION_LENGTH} символов)! Сейчас: {text_length}\n{FORMATTING_GUIDE2}",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
            return

        try:
            cursor.execute("UPDATE giveaways SET description = %s WHERE id = %s", (new_description, giveaway_id))
            conn.commit()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/opis.png'),
                    caption="<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить описание 😔",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count_active:'))
    async def process_edit_winner_count_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_winner_count_active)

        cursor.execute("SELECT winner_count FROM giveaways WHERE id = %s", (giveaway_id,))
        current_winner_count = cursor.fetchone()[0]

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Текущее количество победителей: <b>{current_winner_count}</b>\n\n"
            f"Укажите новое число (максимум {MAX_WINNERS}):",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )
        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_winner_count_active)
    async def process_new_winner_count_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']

        try:
            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Количество должно быть положительным")

            if new_winner_count > MAX_WINNERS:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Слишком много победителей! Максимум {MAX_WINNERS}",
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id
                )
                return

            cursor.execute("SELECT winner_count FROM giveaways WHERE id = %s", (giveaway_id,))
            current_winner_count = cursor.fetchone()[0]

            cursor.execute("UPDATE giveaways SET winner_count = %s WHERE id = %s", (new_winner_count, giveaway_id))
            if new_winner_count > current_winner_count:
                for place in range(current_winner_count + 1, new_winner_count + 1):
                    cursor.execute(
                        "INSERT INTO congratulations (giveaway_id, place, message) VALUES (%s, %s, %s)",
                        (giveaway_id, place, f"🎉 Поздравляем! Вы заняли {place} место!")
                    )
            elif new_winner_count < current_winner_count:
                cursor.execute(
                    "DELETE FROM congratulations WHERE giveaway_id = %s AND place >= %s",
                    (giveaway_id, new_winner_count + 1)
                )
            conn.commit()

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число! Например, 3",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить победителей 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )

    @dp.callback_query(lambda c: c.data.startswith('change_end_date_active:'))
    async def process_change_end_date_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_end_time_active)

        cursor.execute("SELECT end_time FROM giveaways WHERE id = %s", (giveaway_id,))
        current_end_time = cursor.fetchone()[0]
        formatted_end_time = (current_end_time + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M (МСК)')

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Текущее время окончания: <b>{formatted_end_time}</b>

Укажите новую дату завершения в формате ДД.ММ.ГГГГ ЧЧ:ММ по МСК

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
"""
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            html_message,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )
        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_end_time_active)
    async def process_new_end_time_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']

        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        if message.text.lower() == 'отмена':
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
            return

        try:
            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            moscow_tz = pytz.timezone('Europe/Moscow')
            new_end_time_tz = moscow_tz.localize(new_end_time)

            cursor.execute("UPDATE giveaways SET end_time = %s WHERE id = %s", (new_end_time_tz, giveaway_id))
            conn.commit()

            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""
<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Неправильный формат даты! Используйте ДД.ММ.ГГГГ ЧЧ:ММ

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
"""
            await send_message_with_image(
                bot,
                message.chat.id,
                html_message,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить дату 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )

    @dp.callback_query(lambda c: c.data.startswith('manage_media_active:'))
    async def process_manage_media_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_media_active)

        keyboard = InlineKeyboardBuilder()
        if giveaway['media_type']:
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_media_active:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        message_text = (
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Текущее медиа: {giveaway['media_type']}.\n\nОтправьте новое или удалите текущее."
            if giveaway['media_type'] else
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!"
        )

        try:
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': types.InputMediaPhoto,
                    'gif': types.InputMediaAnimation,
                    'video': types.InputMediaVideo
                }
                await bot.edit_message_media(
                    chat_id=callback_query.from_user.id,
                    message_id=callback_query.message.message_id,
                    media=media_types[giveaway['media_type']](
                        media=giveaway['media_file_id'],
                        caption=message_text,
                        parse_mode='HTML'
                    ),
                    reply_markup=keyboard.as_markup()
                )
            else:
                image = FSInputFile('image/media.png')
                await bot.edit_message_media(
                    chat_id=callback_query.from_user.id,
                    message_id=callback_query.message.message_id,
                    media=types.InputMediaPhoto(
                        media=image,
                        caption=message_text,
                        parse_mode='HTML'
                    ),
                    reply_markup=keyboard.as_markup()
                )
        except aiogram.exceptions.TelegramBadRequest:
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': bot.send_photo,
                    'gif': bot.send_animation,
                    'video': bot.send_video
                }
                sent_message = await media_types[giveaway['media_type']](
                    chat_id=callback_query.from_user.id,
                    **{giveaway['media_type']: giveaway['media_file_id']},
                    caption=message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            else:
                image = FSInputFile('image/media.png')
                sent_message = await bot.send_photo(
                    chat_id=callback_query.from_user.id,
                    photo=image,
                    caption=message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            await state.update_data(last_message_id=sent_message.message_id)

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_media_active)
    async def process_new_media_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        try:
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
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=last_message_id,
                    media=types.InputMediaPhoto(
                        media=FSInputFile('image/media.png'),
                        caption="<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Отправьте фото, GIF или видео!",
                        parse_mode='HTML'
                    ),
                    reply_markup=keyboard.as_markup()
                )
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)
            file_size_mb = file.file_size / (1024 * 1024)

            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await bot.edit_message_media(
                    chat_id=message.chat.id,
                    message_id=last_message_id,
                    media=types.InputMediaPhoto(
                        media=FSInputFile('image/media.png'),
                        caption=f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой! Максимум {MAX_MEDIA_SIZE_MB} МБ",
                        parse_mode='HTML'
                    ),
                    reply_markup=keyboard.as_markup()
                )
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(result)

            cursor.execute(
                "UPDATE giveaways SET media_type = %s, media_file_id = %s WHERE id = %s",
                (media_type, result, giveaway_id)
            )
            conn.commit()

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_media(
                chat_id=message.chat.id,
                message_id=last_message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/media.png'),
                    caption="<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось загрузить медиа 😔",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('delete_media_active:'))
    async def process_delete_media_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute(
                "UPDATE giveaways SET media_type = NULL, media_file_id = NULL WHERE id = %s",
                (giveaway_id,)
            )
            conn.commit()

            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await bot.edit_message_media(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                media=types.InputMediaPhoto(
                    media=FSInputFile('image/media.png'),
                    caption=f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!",
                    parse_mode='HTML'
                ),
                reply_markup=keyboard.as_markup()
            )
            await bot.answer_callback_query(callback_query.id, text="Медиа удалено ✅")
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id, text="Не удалось удалить медиа 😔")
