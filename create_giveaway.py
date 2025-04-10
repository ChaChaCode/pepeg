from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LinkPreviewOptions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime
import pytz
import logging
import boto3
from botocore.client import Config
import io
import re
import random
import string

# Настройка логирования
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

# Константы
MAX_CAPTION_LENGTH = 2500
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 10
MAX_WINNERS = 100

# Состояния FSM
class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description_and_media = State()  # Новое объединенное состояние
    waiting_for_end_time = State()
    waiting_for_winner_count = State()

# Обновленные инструкции форматирования
FORMATTING_GUIDE_INITIAL = """
🖥 Теперь добавьте описание (до 2500 символов) с медиа файлом (фото, видео или GIF до 10 МБ):

Поддерживаемые форматы текста:
<blockquote expandable>- Цитата
- Жирный: <b>текст</b>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code></blockquote>

Переменные:
- <code>{win}</code> — количество победителей  
- <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (по МСК)  
"""

FORMATTING_GUIDE_UPDATE = """
Поддерживаемые форматы текста:
<blockquote expandable>- Цитата
- Жирный: <b>текст</b>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code></blockquote>

Переменные:
- <code>{win}</code> — количество победителей  
- <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (по МСК)
"""

# Функция для генерации уникального 8-значного кода
def generate_unique_code(cursor) -> str:
    while True:
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        cursor.execute("SELECT COUNT(*) FROM giveaways WHERE id = %s", (code,))
        if cursor.fetchone()[0] == 0:
            return code

async def build_navigation_keyboard(state: FSMContext, current_state: State) -> InlineKeyboardBuilder:
    data = await state.get_data()
    keyboard = InlineKeyboardBuilder()

    next_states = {
        GiveawayStates.waiting_for_name: (GiveawayStates.waiting_for_description_and_media, 'next_to_description_and_media', 'name'),
        GiveawayStates.waiting_for_description_and_media: (GiveawayStates.waiting_for_end_time, 'next_to_end_time', 'description'),
        GiveawayStates.waiting_for_end_time: (GiveawayStates.waiting_for_winner_count, 'next_to_winner_count', 'end_time'),
    }

    back_states = {
        GiveawayStates.waiting_for_description_and_media: 'back_to_name',
        GiveawayStates.waiting_for_end_time: 'back_to_description_and_media',
        GiveawayStates.waiting_for_winner_count: 'back_to_end_time',
    }

    has_next = False
    has_back = False
    has_delete = False

    # Кнопка "Удалить медиа" появляется, если есть медиа и мы на этапе описания и медиа
    if current_state == GiveawayStates.waiting_for_description_and_media and data.get('media_url'):
        keyboard.button(text="🗑️ Удалить медиа", callback_data="delete_media")
        has_delete = True

    # Кнопка "Назад"
    if current_state in back_states:
        keyboard.button(text="◀️ Назад", callback_data=back_states[current_state])
        has_back = True

    # Кнопка "Далее" для этапа описания и медиа появляется, если есть описание
    if current_state == GiveawayStates.waiting_for_description_and_media:
        if data.get('description'):  # Проверяем наличие описания
            keyboard.button(text="Далее ▶️", callback_data="next_to_end_time")
            has_next = True
    # Для остальных состояний используем старую логику
    elif current_state in next_states:
        next_state, callback, required_field = next_states[current_state]
        if required_field in data and data[required_field]:
            keyboard.button(text="Далее ▶️", callback_data=callback)
            has_next = True

    keyboard.button(text="В меню", callback_data="back_to_main_menu")

    # Корректировка расположения кнопок
    if has_delete:
        if has_back and has_next:
            keyboard.adjust(1, 2, 1)  # Удалить отдельно, Назад и Далее вместе, В меню отдельно
        elif has_back or has_next:
            keyboard.adjust(1, 1, 1)  # Удалить, Назад или Далее, В меню
        else:
            keyboard.adjust(1, 1)  # Удалить, В меню
    else:
        if has_back and has_next:
            keyboard.adjust(2, 1)  # Назад и Далее вместе, В меню отдельно
        else:
            keyboard.adjust(1, 1)  # Назад или В меню

    return keyboard


def count_length_with_custom_emoji(text: str) -> int:
    # Удаляем HTML-теги
    tag_pattern = r'<[^>]+>'
    cleaned_text = re.sub(tag_pattern, '', text)

    # Подсчитываем базовую длину текста без тегов
    length = len(cleaned_text)

    # Добавляем фиксированную длину для переменных
    length += text.count('{win}') * (5 - len('{win}'))  # 5 символов минус длина самой строки "{win}"
    length += text.count('{data}') * (16 - len('{data}'))  # 16 символов минус длина самой строки "{data}"

    return length

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"Файл слишком большой! Максимум: {MAX_MEDIA_SIZE_MB} МБ"

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        s3_client.put_object(
            Bucket=YANDEX_BUCKET_NAME,
            Key=unique_filename,
            Body=io.BytesIO(file_content),
            ContentType="application/octet-stream"
        )
        public_url = f"https://{YANDEX_BUCKET_NAME}.storage.yandexcloud.net/{unique_filename}"
        logger.info(f"✅ Файл загружен: {unique_filename}")
        return True, public_url
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки: {str(e)}")
        return False, f"Ошибка загрузки: {str(e)}"

async def save_giveaway(conn, cursor, user_id: int, name: str, description: str, end_time: str,
                       winner_count: int, media_type: str = None, media_file_id: str = None):
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = datetime.strptime(end_time, "%d.%m.%Y %H:%M")
    end_time_tz = moscow_tz.localize(end_time_dt)

    try:
        giveaway_id = generate_unique_code(cursor)

        cursor.execute(
            """
            INSERT INTO giveaways (id, user_id, name, description, end_time, winner_count, is_active, media_type, media_file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (giveaway_id, user_id, name, description, end_time_tz, winner_count, False, media_type, media_file_id)
        )

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
        logger.error(f"Ошибка сохранения: {str(e)}")
        conn.rollback()
        return False, None

def register_create_giveaway_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    @dp.callback_query(lambda c: c.data == 'create_giveaway')
    async def process_create_giveaway(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_name)
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
        message_text = f"<a href=\"{image_url}\">\u200B</a><tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Давайте придумаем название розыгрыша (до {MAX_NAME_LENGTH} символов):"
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )
        await state.update_data(last_message_id=callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_name)
    async def process_name(message: types.Message, state: FSMContext):
        if message.text and message.text.startswith('/'):
            return

        formatted_text = message.html_text if message.text else ""
        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_NAME_LENGTH:
            data = await state.get_data()
            keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_name)
            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
            error_text = f"<a href=\"{image_url}\">\u200B</a>⚠️ Название слишком длинное! Максимум {MAX_NAME_LENGTH} символов, сейчас {text_length}. Сократите!"
            link_preview_options = LinkPreviewOptions(show_above_text=True)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data['last_message_id'],
                text=error_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )
            return

        await state.update_data(name=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_description_and_media)
        data = await state.get_data()
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        message_text = f"<a href=\"{image_url}\">\u200B</a>{FORMATTING_GUIDE_INITIAL}"
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=data['last_message_id'],
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.callback_query(lambda c: c.data == "next_to_description_and_media")
    async def next_to_description_and_media(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_description_and_media)
        data = await state.get_data()
        description = data.get('description', '')
        media_url = data.get('media_url', '')
        media_type = data.get('media_type', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
        image_url = media_url if media_url else 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        media_display = f"📸 Текущее медиа: {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}." if media_url else "📸 Текущее медиа: Отсутствует."
        message_text = (
            f"<a href=\"{image_url}\">\u200B</a>🖥 Текущее описание:\n{description if description else 'Отсутствует'}\n"
            f"{media_display}\n\n"
            f"Если хотите изменить, отправьте новый текст с медиа файлом или можете отправить по отдельности только описание или медиа:\n\n"
            f"{FORMATTING_GUIDE_UPDATE}"
        )

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.message(GiveawayStates.waiting_for_description_and_media)
    async def process_description_and_media(message: types.Message, state: FSMContext):
        if message.text and message.text.startswith('/'):
            return

        data = await state.get_data()
        placeholder_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        # Обработка текста (из message.text или message.caption)
        formatted_text = None
        if message.text:
            formatted_text = message.html_text
        elif message.caption:
            formatted_text = message.html_text

        if formatted_text:
            text_length = count_length_with_custom_emoji(formatted_text)
            if text_length > MAX_DESCRIPTION_LENGTH:
                keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
                error_text = (
                    f"<a href=\"{data.get('media_url', placeholder_url)}\">\u200B</a>"
                    f"⚠️ Описание слишком длинное! Максимум {MAX_DESCRIPTION_LENGTH} символов, сейчас {text_length}. Сократите!\n\n"
                    f"{FORMATTING_GUIDE_UPDATE}"
                )
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=data['last_message_id'],
                    text=error_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )
                return
            await state.update_data(description=formatted_text)

        # Обработка медиа
        file_id = None
        file_ext = None
        media_type = None

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

        if file_id and file_ext:
            file = await bot.get_file(file_id)
            if file.file_size / (1024 * 1024) > MAX_MEDIA_SIZE_MB:
                keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
                error_text = (
                    f"<a href=\"{placeholder_url}\">\u200B</a>"
                    f"🤯 Файл слишком большой! Максимум {MAX_MEDIA_SIZE_MB} МБ\n\n"
                    f"{FORMATTING_GUIDE_UPDATE}"
                )
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=data['last_message_id'],
                    text=error_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )
                return

            file_content = await bot.download_file(file.file_path)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, media_url = await upload_to_storage(file_content.read(), filename)

            if not success:
                keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
                error_text = (
                    f"<a href=\"{placeholder_url}\">\u200B</a>"
                    f"❌ Ошибка загрузки медиа: {media_url}\n\n"
                    f"{FORMATTING_GUIDE_UPDATE}"
                )
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=data['last_message_id'],
                    text=error_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )
                return

            await state.update_data(media_url=media_url, media_type=media_type)

        # Если ничего не отправлено
        if not formatted_text and not (message.photo or message.animation or message.video):
            keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
            error_text = (
                f"<a href=\"{data.get('media_url', placeholder_url)}\">\u200B</a>"
                f"⚠️ Отправьте текст, медиа или и то, и другое!\n\n"
                f"{FORMATTING_GUIDE_UPDATE}"
            )
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data['last_message_id'],
                text=error_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )
            return

        # Обновление сообщения после ввода
        data = await state.get_data()
        description = data.get('description', '')
        media_url = data.get('media_url', '')
        media_type = data.get('media_type', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
        image_url = media_url if media_url else placeholder_url
        media_display = f"📸 Текущее медиа: {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}." if media_url else "📸 Текущее медиа: Отсутствует."
        message_text = (
            f"<a href=\"{image_url}\">\u200B</a>🖥 Текущее описание:\n{description if description else 'Отсутствует'}\n"
            f"{media_display}\n\n"
            f"Если хотите изменить, отправьте новый текст с медиа файлом или можете отправить по отдельности только описание или медиа:\n\n"
            f"{FORMATTING_GUIDE_UPDATE}"
        )

        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=data['last_message_id'],
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.callback_query(lambda c: c.data == "delete_media")
    async def delete_media(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.update_data(media_url=None, media_type=None)
        data = await state.get_data()
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
        description = data.get('description', '')
        placeholder_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        message_text = (
            f"📸 Текущее медиа: Отсутствует.\n"
            f"<a href=\"{placeholder_url}\">\u200B</a>🖥 Текущее описание:\n{description if description else 'Отсутствует'}\n\n"
            f"Если хотите изменить, отправьте новый текст с медиа файлом или можете отправить по отдельности только описание или медиа:\n\n"
            f"{FORMATTING_GUIDE_UPDATE}"
        )
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.callback_query(lambda c: c.data == "back_to_name")
    async def back_to_name(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        data = await state.get_data()
        name = data.get('name', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_name)
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
        message_text = f"<a href=\"{image_url}\">\u200B</a><tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Текущее название: {name}\n\nЕсли хотите изменить, отправьте новый текст:"
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.callback_query(lambda c: c.data == "next_to_end_time")
    async def next_to_end_time(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_end_time)
        data = await state.get_data()
        end_time = data.get('end_time', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
        message_text = (
            f"<a href=\"{image_url}\">\u200B</a>Текущее время окончания: <b>{end_time}</b>\n\n"
            f"Если хотите изменить, укажите новую дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b> по МСК\n\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
            if end_time else
            f"<a href=\"{image_url}\">\u200B</a>Когда завершится розыгрыш? Укажите дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b> (по МСК)\n\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
        )
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.callback_query(lambda c: c.data == "back_to_description_and_media")
    async def back_to_description_and_media(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_description_and_media)
        data = await state.get_data()
        description = data.get('description', '')
        media_url = data.get('media_url', '')
        media_type = data.get('media_type', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description_and_media)
        image_url = media_url if media_url else 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        media_display = f"📸 Текущее медиа: {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}." if media_url else "📸 Текущее медиа: Отсутствует."
        message_text = (
            f"<a href=\"{image_url}\">\u200B</a>🖥 Текущее описание:\n{description if description else 'Отсутствует'}\n"
            f"{media_display}\n\n"
            f"Если хотите изменить, отправьте новый текст с медиа файлом или можете отправить по отдельности только описание или медиа:\n\n"
            f"{FORMATTING_GUIDE_UPDATE}"
        )
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.message(GiveawayStates.waiting_for_end_time)
    async def process_end_time(message: types.Message, state: FSMContext):
        if message.text and message.text.startswith('/'):
            return

        data = await state.get_data()
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        try:
            end_time_dt = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            moscow_tz = pytz.timezone('Europe/Moscow')
            end_time_tz = moscow_tz.localize(end_time_dt)
            if end_time_tz <= datetime.now(moscow_tz):
                raise ValueError("Дата окончания должна быть в будущем!")

            await state.update_data(end_time=message.text)
            await state.set_state(GiveawayStates.waiting_for_winner_count)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="back_to_end_time")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(1)

            message_text = f"<a href=\"{image_url}\">\u200B</a><tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Сколько будет победителей? Максимум {MAX_WINNERS}!"
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data['last_message_id'],
                text=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )

        except ValueError as e:
            keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
            if "day is out of range for month" in str(e):
                error_msg = "⚠️ День находится вне диапазона для месяца"
            elif "does not match format" in str(e):
                error_msg = "⚠️ Неверный формат даты! Используйте ДД.ММ.ГГГГ ЧЧ:ММ (например, 31.03.2025 12:00)"
            else:
                error_msg = str(e)

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            error_text = f"<a href=\"{image_url}\">\u200B</a>{error_msg}\n🗓 Сейчас в Москве: <code>{current_time}</code>"
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data['last_message_id'],
                text=error_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )

    @dp.callback_query(lambda c: c.data == "next_to_winner_count")
    async def next_to_winner_count(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_winner_count)
        data = await state.get_data()
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data="back_to_end_time")
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        message_text = f"<a href=\"{image_url}\">\u200B</a><tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Сколько будет победителей? Максимум {MAX_WINNERS}!"
        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.callback_query(lambda c: c.data == "back_to_end_time")
    async def back_to_end_time(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_end_time)
        data = await state.get_data()
        end_time = data.get('end_time', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
        message_text = (
            f"<a href=\"{image_url}\">\u200B</a>Текущее время окончания: <b>{end_time}</b>\n\n"
            f"Если хотите изменить, укажите новую дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b> по МСК\n\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
            if end_time else
            f"<a href=\"{image_url}\">\u200B</a>Когда завершится розыгрыш? Укажите дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b> (по МСК)\n\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
        )
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        await bot.edit_message_text(
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            parse_mode='HTML',
            link_preview_options=link_preview_options
        )

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        if message.text and message.text.startswith('/'):
            return

        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        data = await state.get_data()
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
        link_preview_options = LinkPreviewOptions(show_above_text=True)

        try:
            winner_count = int(message.text)
            if winner_count <= 0:
                raise ValueError("Количество должно быть положительным")
            if winner_count > MAX_WINNERS:
                raise ValueError(f"Максимум {MAX_WINNERS} победителей")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")

            message_text = f"<a href=\"{image_url}\">\u200B</a><tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Создаём ваш розыгрыш..."
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data.get('last_message_id'),
                text=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )

            media_url = data.get('media_url')
            media_type = data.get('media_type')

            success, giveaway_id = await save_giveaway(
                conn,
                cursor,
                message.from_user.id,
                data['name'],
                data['description'],
                data['end_time'],
                winner_count,
                media_type,
                media_url
            )

            if success:
                await display_giveaway(bot, message.chat.id, giveaway_id, conn, cursor,
                                       message_id=data.get('last_message_id'))
                await state.clear()
            else:
                raise Exception("Не удалось сохранить розыгрыш")

        except ValueError as ve:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="back_to_end_time")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(1)

            if "invalid literal for int()" in str(ve):
                error_msg = "⚠️ Введите число! Например, 1, 5 или 10"
            else:
                error_msg = str(ve) if str(ve) else f"Введите число от 1 до {MAX_WINNERS}"

            error_text = f"<a href=\"{image_url}\">\u200B</a><tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {error_msg}"
            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data.get('last_message_id'),
                text=error_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="🔄 Попробовать снова", callback_data="create_giveaway")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(1)
            error_message = f"<a href=\"{image_url}\">\u200B</a>❌ Ошибка: {str(e) if str(e) else 'Что-то пошло не так'}"

            await bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=data.get('last_message_id'),
                text=error_message,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                link_preview_options=link_preview_options
            )

    async def display_giveaway(bot: Bot, chat_id: int, giveaway_id: str, conn, cursor, message_id: int = None):
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                raise Exception("Giveaway not found in database")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Редактировать", callback_data=f"edit_post:{giveaway_id}")
            keyboard.button(text="👀 Предпросмотр", callback_data=f"preview_giveaway:{giveaway_id}")
            keyboard.button(text="👥 Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
            keyboard.button(text="📢 Опубликовать", callback_data=f"activate_giveaway:{giveaway_id}")
            keyboard.button(text="📩 Добавить приглашения", callback_data=f"add_invite_task:{giveaway_id}")
            keyboard.button(text="🎉 Сообщение победителям", callback_data=f"message_winners:{giveaway_id}")
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_giveaway:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            keyboard.adjust(2, 2, 1, 1, 1, 1)

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = giveaway['end_time'].strftime('%d.%m.%Y %H:%M (МСК)') if giveaway['end_time'] else "Не указано"
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            media_file_id = giveaway.get('media_file_id')
            image_url = media_file_id if media_file_id else 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'

            giveaway_info = f"<a href=\"{image_url}\">\u200B</a>{formatted_description}"
            link_preview_options = LinkPreviewOptions(show_above_text=True)

            if message_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )

        except Exception as e:
            logger.error(f"Ошибка отображения розыгрыша: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
            error_text = f"<a href=\"{image_url}\">\u200B</a>❌ Ошибка загрузки розыгрыша. Попробуйте снова!"
            link_preview_options = LinkPreviewOptions(show_above_text=True)

            if message_id:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=error_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )
            else:
                await bot.send_message(
                    chat_id=chat_id,
                    text=error_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    link_preview_options=link_preview_options
                )
