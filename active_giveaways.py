from utils import end_giveaway, send_message_with_image, select_random_winners
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime, timedelta
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder
import json
import boto3
from botocore.client import Config
import requests
import re
from aiogram.types import CallbackQuery
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
MAX_CAPTION_LENGTH = 2500
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 10
MAX_WINNERS = 100
DEFAULT_IMAGE_URL = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'  # Заглушка

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
    # Регулярное выражение для удаления всех HTML-тегов
    tag_pattern = r'<[^>]+>'
    # Удаляем все теги из текста
    cleaned_text = re.sub(tag_pattern, '', text)
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

async def get_file_url(bot: Bot, file_id: str) -> str:
    """Получает URL файла по его file_id."""
    try:
        file = await bot.get_file(file_id)
        file_path = file.file_path
        file_url = f"https://api.telegram.org/file/bot{bot.token}/{file_path}"
        return file_url
    except Exception as e:
        logger.error(f"🚫 Ошибка получения URL файла {file_id}: {str(e)}")
        raise

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

            published_messages = get_json_field(cursor, "SELECT published_messages FROM giveaways WHERE id = %s",
                                                (giveaway_id,)) if giveaway['published_messages'] else []
            channel_info = ""
            if published_messages:
                channel_links = []
                for msg in published_messages:
                    chat_id = msg['chat_id']
                    message_id = msg['message_id']
                    try:
                        chat = await bot.get_chat(chat_id)
                        channel_name = chat.title
                        # Проверяем тип чата: группа или канал
                        if chat.type in ['group', 'supergroup']:
                            # Для групп используем ссылку на группу
                            post_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                        else:
                            # Для каналов используем ссылку на конкретный пост
                            if chat.username:
                                post_link = f"https://t.me/{chat.username}/{message_id}"
                            else:
                                post_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{message_id}"
                        channel_links.append(f"<a href=\"{post_link}\">{channel_name}</a>")
                    except Exception as e:
                        logger.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                        channel_links.append("Неизвестный канал")
                channel_info = f"\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Опубликовано в:</b> {', '.join(channel_links)}"

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = (giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M (МСК)')
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            giveaway_info = f"""{formatted_description}

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

            # Проверяем наличие медиа
            image_url = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])
            else:
                # Используем заглушку для сообщения в меню
                image_url = DEFAULT_IMAGE_URL

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url
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
        # Используем заглушку для сообщения
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> Вы уверены, что хотите завершить розыгрыш?",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

    @dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
    async def process_force_end_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Завершаем розыгрыш...",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

            # Получаем данные о розыгрыше
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                raise Exception("Розыгрыш не найден")

            # Получаем участников
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

            # Выбираем победителей
            winners = await select_random_winners(
                bot, participants, min(len(participants), giveaway['winner_count']), giveaway_id, conn, cursor
            )

            # Завершаем розыгрыш с параметром notify_creator=False
            await end_giveaway(bot=bot, giveaway_id=giveaway_id, conn=conn, cursor=cursor, notify_creator=False)

            # Формируем сообщение в том же формате, что в notify_winners_and_publish_results
            participant_counter_tasks = get_json_field(cursor,
                                                       "SELECT participant_counter_tasks FROM giveaways WHERE id = %s",
                                                       (giveaway_id,))
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
                result_message = f"""
<b>Розыгрыш завершен</b>

{giveaway['name']}

К сожалению, в этом розыгрыше не было участников.
"""

            if winners and len(winners) < giveaway['winner_count']:
                result_message += f"""
Не все призовые места были распределены.
"""

            if channel_links:
                result_message += f"""
<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Результаты опубликованы в:</b> {', '.join(channel_links)}
"""

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")

            # Определяем URL изображения
            image_url = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])

            await bot.answer_callback_query(callback_query.id)
            if image_url:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    result_message,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=image_url
                )
            else:
                # Если медиа нет, отправляем сообщение без изображения
                await bot.edit_message_text(
                    chat_id=callback_query.from_user.id,
                    message_id=callback_query.message.message_id,
                    text=result_message,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка при завершении 😔")
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "Ошибка при завершении 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_active_post:'))
    async def process_edit_active_post(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    async def _show_edit_menu_active(user_id: int, giveaway_id: str, message_id: int = None):
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, cursor.fetchone()))
        if not giveaway:
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                user_id,
                "🔍 Розыгрыш не найден 😕",
                reply_markup=None,
                message_id=message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            return

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name_active:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_description_active:{giveaway_id}")
        keyboard.button(text="🏆 Победители", callback_data=f"edit_winner_count_active:{giveaway_id}")
        keyboard.button(text="⏰ Дата", callback_data=f"change_end_date_active:{giveaway_id}")
        keyboard.button(text="🖼️ Медиа", callback_data=f"manage_media_active:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_active_giveaway:{giveaway_id}")
        keyboard.adjust(2, 2, 1, 1)

        # Определяем тип медиа для отображения
        media_display = "Медиа: отсутствует"
        if giveaway['media_type']:
            if giveaway['media_type'] == 'photo':
                media_display = "Медиа: фото"
            elif giveaway['media_type'] == 'gif':
                media_display = "Медиа: gif"
            elif giveaway['media_type'] == 'video':
                media_display = "Медиа: видео"

        dop_info = (
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}\n"
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> <b>{media_display}</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {(giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)"
        )

        giveaway_info = f"""<b>Название:</b> {giveaway['name']}
<b>Описание:\n</b> {giveaway['description']}

{dop_info}

<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Что хотите изменить?
"""

        try:
            # Проверяем наличие медиа
            image_url = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])
            else:
                # Используем заглушку для меню редактирования
                image_url = DEFAULT_IMAGE_URL

            await send_message_with_image(
                bot,
                user_id,
                giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=message_id,
                parse_mode='HTML',
                image_url=image_url
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                user_id,
                "<tg-emoji emoji-id='5422649047334794716'>😵</tg-emoji> Ошибка загрузки меню 😔",
                reply_markup=None,
                message_id=message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    async def update_published_posts_active(giveaway_id: str, giveaway: Dict[str, Any]):
        try:
            # Получаем опубликованные сообщения
            published_messages = get_json_field(cursor, "SELECT published_messages FROM giveaways WHERE id = %s",
                                                (giveaway_id,))
            if not published_messages:
                logger.info(f"Нет опубликованных сообщений для розыгрыша {giveaway_id}")
                return

            # Получаем текущее количество участников
            cursor.execute("SELECT COUNT(*) FROM participations WHERE giveaway_id = %s", (giveaway_id,))
            participants_count = cursor.fetchone()[0]

            # Форматируем описание с учетом переменных
            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = (giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M (МСК)')
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            # Текст нового поста
            new_post_text = f"{formatted_description}"

            # Создаем клавиатуру с кнопкой "Участвовать"
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"🎉 Участвовать ({participants_count})",
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )

            # Обновляем все опубликованные сообщения
            for message in published_messages:
                chat_id = message['chat_id']
                message_id = message['message_id']

                try:
                    # Определяем URL изображения
                    image_url = None
                    if giveaway['media_type'] and giveaway['media_file_id']:
                        image_url = giveaway['media_file_id']
                        if not image_url.startswith('http'):
                            image_url = await get_file_url(bot, giveaway['media_file_id'])

                    if image_url:
                        # Обновляем сообщение с изображением
                        await send_message_with_image(
                            bot,
                            chat_id,
                            new_post_text,
                            reply_markup=keyboard.as_markup(),
                            message_id=message_id,
                            parse_mode='HTML',
                            image_url=image_url
                        )
                    else:
                        # Обновляем текстовое сообщение
                        await bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=new_post_text,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    logger.info(f"Обновлен пост {message_id} в чате {chat_id}")

                except Exception as e:
                    logger.error(f"🚫 Ошибка обновления поста {message_id} в чате {chat_id}: {str(e)}")
                    continue

        except Exception as e:
            logger.error(f"🚫 Ошибка в update_published_posts_active для розыгрыша {giveaway_id}: {str(e)}")
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

        # Используем заглушку для сообщения
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

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
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название должно быть от 1 до {MAX_NAME_LENGTH} символов! Сейчас: {text_length}\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            return

        if text_length > MAX_CAPTION_LENGTH:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название превышает лимит Telegram ({MAX_CAPTION_LENGTH} символов)! Сейчас: {text_length}\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
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
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить название 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
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

        # Используем заглушку для сообщения
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

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
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Описание должно быть от 1 до {MAX_DESCRIPTION_LENGTH} символов! Сейчас: {text_length}\n{FORMATTING_GUIDE2}",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            return

        if text_length > MAX_CAPTION_LENGTH:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Описание превышает лимит Telegram ({MAX_CAPTION_LENGTH} символов)! Сейчас: {text_length}\n{FORMATTING_GUIDE2}",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
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
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить описание 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
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

        # Используем заглушку для сообщения
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Текущее количество победителей: <b>{current_winner_count}</b>\n\n"
            f"Укажите новое число (максимум {MAX_WINNERS}):",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
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
                # Используем заглушку для сообщения
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Слишком много победителей! Максимум {MAX_WINNERS}",
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
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
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число! Например, 3",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить победителей 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
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
        html_message = f"""<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Текущее время окончания: <b>{formatted_end_time}</b>

Укажите новую дату завершения в формате ДД.ММ.ГГГГ ЧЧ:ММ по МСК

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
"""
        # Используем заглушку для сообщения
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            html_message,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
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
            html_message = f"""<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Неправильный формат даты! Используйте ДД.ММ.ГГГГ ЧЧ:ММ

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
"""
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                message.chat.id,
                html_message,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить дату 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('manage_media_active:'))
    async def process_manage_media_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))

        # Проверяем наличие медиа
        media_file_id = giveaway.get('media_file_id')
        media_type = giveaway.get('media_type')
        has_media = media_file_id and media_type

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_media_active)

        keyboard = InlineKeyboardBuilder()
        if giveaway['media_type']:
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_media_active:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
        keyboard.adjust(1, 1)
        message_text = (
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Текущее медиа: {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}.\n\nОтправьте новое или удалите текущее."
            if giveaway['media_type'] else
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!"
        )

        # Проверяем наличие медиа
        image_url = None
        if giveaway['media_type'] and giveaway['media_file_id']:
            image_url = giveaway['media_file_id']
            if not image_url.startswith('http'):
                image_url = await get_file_url(bot, giveaway['media_file_id'])
        else:
            # Используем заглушку для сообщения в меню
            image_url = DEFAULT_IMAGE_URL

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=image_url
        )

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
                file_ext = 'mp4'
            elif message.video:
                file_id = message.video.file_id
                media_type = 'video'
                file_ext = 'mp4'
            else:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                # Используем заглушку для сообщения
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Отправьте фото, GIF или видео!",
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
                )
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)
            file_size_mb = file.file_size / (1024 * 1024)

            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                # Используем заглушку для сообщения
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой! Максимум {MAX_MEDIA_SIZE_MB} МБ",
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
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
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось загрузить медиа 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
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
            # Используем заглушку для сообщения
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            await bot.answer_callback_query(callback_query.id, text="Медиа удалено ✅")
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id, text="Не удалось удалить медиа 😔")
            # Используем заглушку для сообщения об ошибке
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "Не удалось удалить медиа 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
