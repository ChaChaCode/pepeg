from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from datetime import datetime
import pytz
from utils import send_message_with_image
import logging
import boto3
from botocore.client import Config
import io
import re

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
MAX_CAPTION_LENGTH = 850
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 850
MAX_MEDIA_SIZE_MB = 10
MAX_WINNERS = 100

# Состояния FSM
class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_media_choice = State()
    waiting_for_media_upload = State()
    waiting_for_end_time = State()
    waiting_for_winner_count = State()

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

async def build_navigation_keyboard(state: FSMContext, current_state: State) -> InlineKeyboardBuilder:
    """Создает клавиатуру с кнопками навигации"""
    data = await state.get_data()
    keyboard = InlineKeyboardBuilder()

    next_states = {
        GiveawayStates.waiting_for_name: (GiveawayStates.waiting_for_description, 'next_to_description', 'name'),
        GiveawayStates.waiting_for_description: (
            GiveawayStates.waiting_for_media_choice, 'next_to_media', 'description'),
        GiveawayStates.waiting_for_media_choice: (
            GiveawayStates.waiting_for_end_time, 'next_to_end_time', 'media_type'),
        GiveawayStates.waiting_for_media_upload: (
            GiveawayStates.waiting_for_end_time, 'next_to_end_time', 'media_file_id_temp'),
        GiveawayStates.waiting_for_end_time: (
            GiveawayStates.waiting_for_winner_count, 'next_to_winner_count', 'end_time'),
    }

    back_states = {
        GiveawayStates.waiting_for_description: 'back_to_name',
        GiveawayStates.waiting_for_media_choice: 'back_to_description',
        GiveawayStates.waiting_for_media_upload: 'back_to_media_choice',
        GiveawayStates.waiting_for_end_time: 'back_to_media_choice',
        GiveawayStates.waiting_for_winner_count: 'back_to_end_time',
    }

    has_next = False
    has_back = False

    # Сначала добавляем кнопку "Назад"
    if current_state in back_states:
        keyboard.button(text="◀️ Назад", callback_data=back_states[current_state])
        has_back = True

    # Затем добавляем кнопку "Далее"
    if current_state in next_states:
        next_state, callback, required_field = next_states[current_state]
        if required_field in data or current_state == GiveawayStates.waiting_for_media_choice:
            keyboard.button(text="Далее ▶️", callback_data=callback)
            has_next = True

    # Добавляем кнопку "В меню"
    keyboard.button(text="В меню", callback_data="back_to_main_menu")

    # Если есть обе кнопки "Назад" и "Далее", размещаем их на одной строке
    if has_next and has_back:
        keyboard.adjust(2, 1)  # 2 кнопки в первой строке, 1 во второй
    else:
        keyboard.adjust(1)  # Все кнопки в один столбец

    return keyboard

def count_length_with_custom_emoji(text: str) -> int:
    emoji_pattern = r'<tg-emoji emoji-id="[^"]+">[^<]+</tg-emoji>'
    custom_emojis = re.findall(emoji_pattern, text)
    cleaned_text = text
    for emoji in custom_emojis:
        cleaned_text = cleaned_text.replace(emoji, ' ')
    return len(cleaned_text)

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
    """Сохраняет данные розыгрыша в базу данных"""
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = datetime.strptime(end_time, "%d.%m.%Y %H:%M")
    end_time_tz = moscow_tz.localize(end_time_dt)

    try:
        cursor.execute(
            """
            INSERT INTO giveaways (user_id, name, description, end_time, winner_count, is_active, media_type, media_file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (user_id, name, description, end_time_tz, winner_count, False, media_type, media_file_id)
        )
        giveaway_id = cursor.fetchone()[0]

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
        await send_message_with_image(
            bot, callback_query.from_user.id,
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Давайте придумаем название розыгрыша (до {MAX_NAME_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )
        await state.update_data(last_message_id=callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_name)
    async def process_name(message: types.Message, state: FSMContext):
        formatted_text = message.html_text if message.text else ""
        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_NAME_LENGTH:
            data = await state.get_data()
            keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_name)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot, message.chat.id,
                f"⚠️ Название слишком длинное! Максимум {MAX_NAME_LENGTH} символов, сейчас {text_length}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        await state.update_data(name=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_description)
        data = await state.get_data()
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description)
        description = data.get('description', '')

        # Проверяем, есть ли описание
        if description:
            message_text = (
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Текущее описание: <b>{description}</b>\n"
                f"Если хотите изменить, отправьте новый текст:\n{FORMATTING_GUIDE}"
            )
        else:
            message_text = (
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Теперь добавьте описание (до {MAX_DESCRIPTION_LENGTH} символов):\n"
                f"{FORMATTING_GUIDE}"
            )

        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await send_message_with_image(
            bot, message.chat.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=data['last_message_id'],
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "back_to_name")
    async def back_to_name(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        data = await state.get_data()
        name = data.get('name', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_name)
        await send_message_with_image(
            bot, callback_query.from_user.id,
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Текущее название: <b>{name}</b>\nЕсли хотите изменить, отправьте новый текст:\n{FORMATTING_GUIDE}",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "next_to_description")
    async def next_to_description(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_description)
        data = await state.get_data()
        description = data.get('description', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description)

        # Проверяем, есть ли описание
        if description:
            message_text = (
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Текущее описание: <b>{description}</b>\n"
                f"Если хотите изменить, отправьте новый текст:\n{FORMATTING_GUIDE}"
            )
        else:
            message_text = (
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Теперь добавьте описание (до {MAX_DESCRIPTION_LENGTH} символов):\n"
                f"{FORMATTING_GUIDE}"
            )

        await send_message_with_image(
            bot, callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_description)
    async def process_description(message: types.Message, state: FSMContext):
        formatted_text = message.html_text if message.text else ""
        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_DESCRIPTION_LENGTH:
            data = await state.get_data()
            keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot, message.chat.id,
                f"⚠️ Описание слишком длинное! Максимум {MAX_DESCRIPTION_LENGTH} символов, сейчас {text_length}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        await state.update_data(description=formatted_text)
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        data = await state.get_data()
        keyboard = InlineKeyboardBuilder()
        media_file_id = data.get('media_file_id_temp')
        media_type = data.get('media_type')

        # Добавляем кнопки "Изменить"/"Добавить Медиа" и "🗑️ Удалить" (если есть медиа)
        keyboard.button(text="Изменить" if media_file_id and media_type else "Добавить Медиа", callback_data="add_media")
        if media_file_id and media_type:
            keyboard.button(text="🗑️ Удалить", callback_data="delete_media")

        # Добавляем навигационные кнопки
        nav_keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_media_choice)
        for button in nav_keyboard.buttons:
            keyboard.button(text=button.text, callback_data=button.callback_data)

        # Настраиваем расположение кнопок
        if media_file_id and media_type:
            keyboard.adjust(2, 2, 1)  # 2 в первой строке, 2 во второй, 1 в третьей
        else:
            keyboard.adjust(1, 2, 1)  # 1 в первой, 2 во второй, 1 в третьей

        message_text = (
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите изменить или удалить {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}?"
            if media_file_id and media_type else
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите добавить фото, GIF или видео? (до {MAX_MEDIA_SIZE_MB} МБ)"
        )

        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        last_message_id = data.get('last_message_id')
        sent_message = None

        if media_file_id and media_type:
            try:
                if media_type == 'photo':
                    await bot.edit_message_media(
                        chat_id=message.chat.id,
                        message_id=last_message_id,
                        media=types.InputMediaPhoto(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                elif media_type == 'gif':
                    await bot.edit_message_media(
                        chat_id=message.chat.id,
                        message_id=last_message_id,
                        media=types.InputMediaAnimation(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                elif media_type == 'video':
                    await bot.edit_message_media(
                        chat_id=message.chat.id,
                        message_id=last_message_id,
                        media=types.InputMediaVideo(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                return
            except TelegramBadRequest as e:
                logger.error(f"Ошибка редактирования медиа: {str(e)}")
                if media_type == 'photo':
                    sent_message = await bot.send_photo(
                        chat_id=message.chat.id,
                        photo=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                elif media_type == 'gif':
                    sent_message = await bot.send_animation(
                        chat_id=message.chat.id,
                        animation=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                elif media_type == 'video':
                    sent_message = await bot.send_video(
                        chat_id=message.chat.id,
                        video=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
        else:
            sent_message = await send_message_with_image(
                bot, message.chat.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML'
            )

        if sent_message:
            await state.update_data(last_message_id=sent_message.message_id)

    @dp.callback_query(lambda c: c.data == "back_to_media_choice")
    async def back_to_media_choice(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        data = await state.get_data()
        keyboard = InlineKeyboardBuilder()
        media_file_id = data.get('media_file_id_temp')
        media_type = data.get('media_type')

        # Добавляем кнопки "Изменить"/"Добавить Медиа" и "🗑️ Удалить" (если есть медиа)
        keyboard.button(text="Изменить" if media_file_id and media_type else "Добавить Медиа", callback_data="add_media")
        if media_file_id and media_type:
            keyboard.button(text="🗑️ Удалить", callback_data="delete_media")

        # Добавляем навигационные кнопки
        nav_keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_media_choice)
        for button in nav_keyboard.buttons:
            keyboard.button(text=button.text, callback_data=button.callback_data)

        # Настраиваем расположение кнопок
        if media_file_id and media_type:
            keyboard.adjust(2, 2, 1)  # 2 в первой строке, 2 во второй, 1 в третьей
        else:
            keyboard.adjust(1, 2, 1)  # 1 в первой, 2 во второй, 1 в третьей

        message_text = (
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите изменить или удалить {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}?"
            if media_file_id and media_type else
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите добавить фото, GIF или видео? (до {MAX_MEDIA_SIZE_MB} МБ)"
        )

        last_message_id = data.get('last_message_id')
        try:
            if media_file_id and media_type:
                if media_type == 'photo':
                    await bot.edit_message_media(
                        chat_id=callback_query.from_user.id,
                        message_id=last_message_id,
                        media=types.InputMediaPhoto(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                elif media_type == 'gif':
                    await bot.edit_message_media(
                        chat_id=callback_query.from_user.id,
                        message_id=last_message_id,
                        media=types.InputMediaAnimation(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                elif media_type == 'video':
                    await bot.edit_message_media(
                        chat_id=callback_query.from_user.id,
                        message_id=last_message_id,
                        media=types.InputMediaVideo(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
            else:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML'
                )
        except TelegramBadRequest as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            sent_message = None
            await bot.delete_message(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id)
            if media_file_id and media_type:
                if media_type == 'photo':
                    sent_message = await bot.send_photo(
                        chat_id=callback_query.from_user.id,
                        photo=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                elif media_type == 'gif':
                    sent_message = await bot.send_animation(
                        chat_id=callback_query.from_user.id,
                        animation=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                elif media_type == 'video':
                    sent_message = await bot.send_video(
                        chat_id=callback_query.from_user.id,
                        video=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
            if not sent_message:
                sent_message = await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            await state.update_data(last_message_id=sent_message.message_id)

    @dp.callback_query(lambda c: c.data == "delete_media")
    async def delete_media(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.update_data(media_file_id_temp=None, media_type=None)  # Удаляем медиа из состояния
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        data = await state.get_data()
        keyboard = InlineKeyboardBuilder()

        # Добавляем кнопку "Добавить Медиа"
        keyboard.button(text="Добавить Медиа", callback_data="add_media")

        # Добавляем навигационные кнопки
        nav_keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_media_choice)
        for button in nav_keyboard.buttons:
            keyboard.button(text=button.text, callback_data=button.callback_data)

        # Настраиваем расположение кнопок
        keyboard.adjust(1, 2, 1)  # 1 в первой, 2 во второй, 1 в третьей

        message_text = f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите добавить фото, GIF или видео? (до {MAX_MEDIA_SIZE_MB} МБ)"
        last_message_id = data.get('last_message_id')

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=last_message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "back_to_end_time")
    async def back_to_end_time(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_end_time)
        data = await state.get_data()
        end_time = data.get('end_time', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        message_text = (
            f"Текущее время окончания: <b>{end_time}</b>\n"
            f"Если хотите изменить, укажите новую дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
            if end_time else
            f"Когда завершится розыгрыш? Укажите дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
        )

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "back_to_description")
    async def back_to_description(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_description)
        data = await state.get_data()
        description = data.get('description', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_description)

        # Проверяем, есть ли описание
        if description:
            message_text = (
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Текущее описание: <b>{description}</b>\n"
                f"Если хотите изменить, отправьте новый текст:\n{FORMATTING_GUIDE}"
            )
        else:
            message_text = (
                f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Теперь добавьте описание (до {MAX_DESCRIPTION_LENGTH} символов):\n"
                f"{FORMATTING_GUIDE}"
            )

        await send_message_with_image(
            bot, callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "add_media")
    async def process_add_media(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_media_upload)
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_media_upload)
        await send_message_with_image(
            bot, callback_query.from_user.id,
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "next_to_media")
    async def next_to_media(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        data = await state.get_data()
        keyboard = InlineKeyboardBuilder()
        media_file_id = data.get('media_file_id_temp')
        media_type = data.get('media_type')

        # Добавляем кнопки "Изменить"/"Добавить Медиа" и "🗑️ Удалить" (если есть медиа)
        keyboard.button(text="Изменить" if media_file_id and media_type else "Добавить Медиа", callback_data="add_media")
        if media_file_id and media_type:
            keyboard.button(text="🗑️ Удалить", callback_data="delete_media")

        # Добавляем навигационные кнопки
        nav_keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_media_choice)
        for button in nav_keyboard.buttons:
            keyboard.button(text=button.text, callback_data=button.callback_data)

        # Настраиваем расположение кнопок
        if media_file_id and media_type:
            keyboard.adjust(2, 2, 1)  # 2 в первой строке, 2 во второй, 1 в третьей
        else:
            keyboard.adjust(1, 2, 1)  # 1 в первой, 2 во второй, 1 в третьей

        message_text = (
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите изменить или удалить {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}?"
            if media_file_id and media_type else
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Хотите добавить фото, GIF или видео? (до {MAX_MEDIA_SIZE_MB} МБ)"
        )

        last_message_id = data.get('last_message_id')
        try:
            if media_file_id and media_type:
                if media_type == 'photo':
                    await bot.edit_message_media(
                        chat_id=callback_query.from_user.id,
                        message_id=last_message_id,
                        media=types.InputMediaPhoto(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                elif media_type == 'gif':
                    await bot.edit_message_media(
                        chat_id=callback_query.from_user.id,
                        message_id=last_message_id,
                        media=types.InputMediaAnimation(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
                elif media_type == 'video':
                    await bot.edit_message_media(
                        chat_id=callback_query.from_user.id,
                        message_id=last_message_id,
                        media=types.InputMediaVideo(
                            media=media_file_id,
                            caption=message_text,
                            parse_mode='HTML'
                        ),
                        reply_markup=keyboard.as_markup()
                    )
            else:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML'
                )
        except TelegramBadRequest as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            sent_message = None
            await bot.delete_message(chat_id=callback_query.from_user.id, message_id=callback_query.message.message_id)
            if media_file_id and media_type:
                if media_type == 'photo':
                    sent_message = await bot.send_photo(
                        chat_id=callback_query.from_user.id,
                        photo=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                elif media_type == 'gif':
                    sent_message = await bot.send_animation(
                        chat_id=callback_query.from_user.id,
                        animation=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                elif media_type == 'video':
                    sent_message = await bot.send_video(
                        chat_id=callback_query.from_user.id,
                        video=media_file_id,
                        caption=message_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
            if not sent_message:
                sent_message = await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            await state.update_data(last_message_id=sent_message.message_id)

    @dp.message(GiveawayStates.waiting_for_media_upload)
    async def process_media_upload(message: types.Message, state: FSMContext):
        data = await state.get_data()
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_media_upload)

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
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot, message.chat.id,
                "<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Отправьте фото, GIF или видео!",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        file = await bot.get_file(file_id)
        if file.file_size / (1024 * 1024) > MAX_MEDIA_SIZE_MB:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot, message.chat.id,
                f"🤯 Файл слишком большой! Максимум {MAX_MEDIA_SIZE_MB} МБ",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        await state.update_data(media_type=media_type, media_file_id_temp=file_id)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await process_end_time_request(message.chat.id, state, data['last_message_id'])

    async def process_end_time_request(chat_id: int, state: FSMContext, message_id: int):
        await state.set_state(GiveawayStates.waiting_for_end_time)
        data = await state.get_data()
        end_time = data.get('end_time', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        message_text = (
            f"Текущее время окончания: <b>{end_time}</b>\n"
            f"Если хотите изменить, укажите новую дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
            if end_time else
            f"Когда завершится розыгрыш? Укажите дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
        )

        await send_message_with_image(
            bot, chat_id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data == "next_to_end_time")
    async def next_to_end_time(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_end_time)
        data = await state.get_data()
        end_time = data.get('end_time', '')
        keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        message_text = (
            f"Текущее время окончания: <b>{end_time}</b>\n"
            f"Если хотите изменить, укажите новую дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
            if end_time else
            f"Когда завершится розыгрыш? Укажите дату в формате <b>ДД.ММ.ГГГГ ЧЧ:ММ</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве: <code>{current_time}</code>"
        )

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_end_time)
    async def process_end_time(message: types.Message, state: FSMContext):
        data = await state.get_data()
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')

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

            await send_message_with_image(
                bot, message.chat.id,
                f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Сколько будет победителей? Максимум {MAX_WINNERS}!",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )

        except ValueError as e:
            keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_end_time)
            error_msg = str(e) if str(
                e) != "time data does not match format '%d.%m.%Y %H:%M'" else "Неверный формат! Используйте ДД.ММ.ГГГГ ЧЧ:ММ"
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot, message.chat.id,
                f"⚠️ {error_msg}\n🗓 Сейчас в Москве: <code>{current_time}</code>",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )

    @dp.callback_query(lambda c: c.data == "next_to_winner_count")
    async def next_to_winner_count(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_winner_count)
        data = await state.get_data()

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data="back_to_end_time")
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"Сколько будет победителей? Введите число (максимум {MAX_WINNERS})!",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        try:
            winner_count = int(message.text)
            if winner_count <= 0:
                raise ValueError("Количество должно быть положительным")
            if winner_count > MAX_WINNERS:
                raise ValueError(f"Максимум {MAX_WINNERS} победителей")

            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")

            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Создаём ваш розыгрыш...",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

            media_file_id = None
            if data.get('media_file_id_temp'):
                file = await bot.get_file(data['media_file_id_temp'])
                file_content = await bot.download_file(file.file_path)
                file_ext = {'photo': 'jpg', 'gif': 'gif', 'video': 'mp4'}[data['media_type']]
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                filename = f"{timestamp}_{message.message_id}.{file_ext}"
                success, media_file_id = await upload_to_storage(file_content.read(), filename)
                if not success:
                    raise Exception(media_file_id)

            success, giveaway_id = await save_giveaway(
                conn,
                cursor,
                message.from_user.id,
                data['name'],
                data['description'],
                data['end_time'],
                winner_count,
                data.get('media_type'),
                media_file_id
            )

            if success:
                await display_giveaway(bot, message.chat.id, giveaway_id, conn, cursor,
                                       message_id=data.get('last_message_id'))
                await state.clear()
            else:
                raise Exception("Не удалось сохранить розыгрыш")

        except ValueError as ve:
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="back_to_end_time")
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            keyboard.adjust(1)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {str(ve) if str(ve) else 'Введите число от 1 до ' + str(MAX_WINNERS)}",
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
            error_message = f"❌ Ошибка: {str(e) if str(e) else 'Что-то пошло не так'}"

            try:
                await bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=data.get('last_message_id'),
                    text=error_message,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            except TelegramBadRequest as te:
                if "there is no text in the message to edit" in str(te):
                    try:
                        await bot.edit_message_caption(
                            chat_id=message.chat.id,
                            message_id=data.get('last_message_id'),
                            caption=error_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    except Exception as edit_caption_error:
                        logger.error(f"Не удалось отредактировать подпись: {str(edit_caption_error)}")
                        await send_message_with_image(
                            bot,
                            message.chat.id,
                            error_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                else:
                    raise te

    async def display_giveaway(bot: Bot, chat_id: int, giveaway_id: int, conn, cursor, message_id: int = None):
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                raise Exception("Giveaway not found in database")

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

            invite_info = f"\n😊 Приглашайте {giveaway['quantity_invite']} друзей для участия!" if giveaway.get(
                'invite') else ""
            end_time_msk = giveaway['end_time'].strftime('%d.%m.%Y %H:%M')
            giveaway_info = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}
<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {end_time_msk} (МСК)
{invite_info}
"""

            media_file_id = giveaway.get('media_file_id')
            media_type = giveaway.get('media_type')

            if media_file_id and media_type:  # Если есть медиа
                if message_id:
                    try:
                        if media_type == 'photo':
                            await bot.edit_message_media(
                                chat_id=chat_id,
                                message_id=message_id,
                                media=types.InputMediaPhoto(
                                    media=media_file_id,
                                    caption=giveaway_info,
                                    parse_mode='HTML'
                                ),
                                reply_markup=keyboard.as_markup()
                            )
                        elif media_type == 'gif':
                            await bot.edit_message_media(
                                chat_id=chat_id,
                                message_id=message_id,
                                media=types.InputMediaAnimation(
                                    media=media_file_id,
                                    caption=giveaway_info,
                                    parse_mode='HTML'
                                ),
                                reply_markup=keyboard.as_markup()
                            )
                        elif media_type == 'video':
                            await bot.edit_message_media(
                                chat_id=chat_id,
                                message_id=message_id,
                                media=types.InputMediaVideo(
                                    media=media_file_id,
                                    caption=giveaway_info,
                                    parse_mode='HTML'
                                ),
                                reply_markup=keyboard.as_markup()
                            )
                    except TelegramBadRequest as e:
                        logger.error(f"Ошибка редактирования медиа: {str(e)}")
                        # Если редактирование не удалось, отправляем новое сообщение
                        if media_type == 'photo':
                            await bot.send_photo(
                                chat_id=chat_id,
                                photo=media_file_id,
                                caption=giveaway_info,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        elif media_type == 'gif':
                            await bot.send_animation(
                                chat_id=chat_id,
                                animation=media_file_id,
                                caption=giveaway_info,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        elif media_type == 'video':
                            await bot.send_video(
                                chat_id=chat_id,
                                video=media_file_id,
                                caption=giveaway_info,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                else:
                    if media_type == 'photo':
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=media_file_id,
                            caption=giveaway_info,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    elif media_type == 'gif':
                        await bot.send_animation(
                            chat_id=chat_id,
                            animation=media_file_id,
                            caption=giveaway_info,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    elif media_type == 'video':
                        await bot.send_video(
                            chat_id=chat_id,
                            video=media_file_id,
                            caption=giveaway_info,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
            else:  # Если медиа нет, используем заглушку
                if message_id:
                    await send_message_with_image(
                        bot, chat_id, giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,
                        parse_mode='HTML'
                    )
                else:
                    await send_message_with_image(
                        bot, chat_id, giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )

        except Exception as e:
            logger.error(f"Ошибка отображения розыгрыша: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            await send_message_with_image(
                bot, chat_id,
                "❌ Ошибка загрузки розыгрыша. Попробуйте снова!",
                reply_markup=keyboard.as_markup(),
                message_id=message_id if message_id else None,
                parse_mode='HTML'
            )
