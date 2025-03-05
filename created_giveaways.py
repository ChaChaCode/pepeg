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

# Настройка логирования 📝
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация бота 🤖
BOT_TOKEN = '7924714999:AAFUbKWC--s-ff2DKe6g5Sk1C2Z7yl7hh0c'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

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

# Глобальные переменные 🌍
user_selected_communities = {}
paid_users: Dict[int, str] = {}

# Константы ⚙️
MAX_NAME_LENGTH = 50
MAX_DESCRIPTION_LENGTH = 2500
MAX_MEDIA_SIZE_MB = 5
MAX_WINNERS = 50

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
</blockquote>
"""

def strip_html_tags(text: str) -> str:
    """Удаляет HTML-теги из текста 🧹"""
    return re.sub(r'<[^>]+>', '', text)

class GiveawayStates(StatesGroup):
    waiting_for_name = State()  # ✏️
    waiting_for_description = State()  # 📜
    waiting_for_media_choice = State()  # 🖼️
    waiting_for_media_upload = State()  # 📤
    waiting_for_end_time = State()  # ⏰
    waiting_for_winner_count = State()  # 🏆
    waiting_for_community_name = State()  # 👥
    waiting_for_new_end_time = State()  # ⏳
    waiting_for_media_edit = State()  # 🖌️
    waiting_for_congrats_message = State()  # 🎉
    waiting_for_common_congrats_message = State()  # 🎊
    waiting_for_edit_name = State()  # ✏️
    waiting_for_edit_description = State()  # 📝
    waiting_for_edit_winner_count = State()  # 🏅
    creating_giveaway = State()  # 🚀
    binding_communities = State()  # 🔗
    waiting_for_invite_quantity = State()  # 📩

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    """Загружает файл в хранилище 📤"""
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"⚠️ Файл слишком большой! Максимум: {MAX_MEDIA_SIZE_MB} МБ 😔"

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
        return False, f"❌ Не удалось загрузить файл: {str(e)}"

def register_created_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    """Регистрирует обработчики для управления розыгрышами 🎁"""

    @dp.callback_query(lambda c: c.data == 'created_giveaways' or c.data.startswith('created_giveaways_page:'))
    async def process_created_giveaways(callback_query: CallbackQuery):
        """Показывает список созданных розыгрышей с is_active = 'false' или 'waiting' 📋"""
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if ':' in callback_query.data else 1

        try:
            # Изменяем запрос, чтобы выбрать розыгрыши с is_active в ('false', 'waiting')
            response = supabase.table('giveaways').select('*').eq('user_id', user_id).in_('is_active', ['false',
                                                                                                        'waiting']).execute()
            if not response.data:
                await bot.answer_callback_query(callback_query.id,
                                                text="📭 Пока нет розыгрышей? Создайте свой первый! 🚀")
                return

            total_giveaways = len(response.data)
            total_pages = math.ceil(total_giveaways / ITEMS_PER_PAGE)
            start_idx = (current_page - 1) * ITEMS_PER_PAGE
            current_giveaways = response.data[start_idx:start_idx + ITEMS_PER_PAGE]

            keyboard = InlineKeyboardBuilder()
            for giveaway in current_giveaways:
                clean_name = strip_html_tags(giveaway['name'])[:61] + "..." if len(
                    giveaway['name']) > 64 else strip_html_tags(giveaway['name'])
                # Можно добавить индикатор состояния в название, если хотите
                status_indicator = "" if giveaway['is_active'] == 'waiting' else ""
                keyboard.row(InlineKeyboardButton(
                    text=f"{status_indicator} {clean_name}",
                    callback_data=f"view_created_giveaway:{giveaway['id']}"
                ))

            nav_buttons = []
            if current_page > 1:
                nav_buttons.append(
                    InlineKeyboardButton(text="◀️", callback_data=f"created_giveaways_page:{current_page - 1}"))
            if total_pages > 1:
                nav_buttons.append(InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="ignore"))
            if current_page < total_pages:
                nav_buttons.append(
                    InlineKeyboardButton(text="▶️", callback_data=f"created_giveaways_page:{current_page + 1}"))

            if nav_buttons:
                keyboard.row(*nav_buttons)
            keyboard.row(InlineKeyboardButton(text=" ◀️ Назад", callback_data="back_to_main_menu"))

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(
                bot,
                user_id,
                f"🎉 Выберите розыгрыш для просмотра! (⏳ — ожидание, 📝 — неактивен)",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Упс! Что-то пошло не так 😔")

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('view_created_giveaway:'))
    async def process_view_created_giveaway(callback_query: CallbackQuery):
        """Показывает детали розыгрыша 👀"""
        giveaway_id = callback_query.data.split(':')[1]
        try:
            response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            if not response.data:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                return

            giveaway = response.data
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Редактировать", callback_data=f"edit_post:{giveaway_id}")
            keyboard.button(text="👥 Привязать сообщества", callback_data=f"bind_communities:{giveaway_id}")
            keyboard.button(text="🚀 Опубликовать", callback_data=f"activate_giveaway:{giveaway_id}")
            keyboard.button(text="📩 Добавить приглашения", callback_data=f"add_invite_task:{giveaway_id}")
            keyboard.button(text="🎉 Сообщение победителям", callback_data=f"message_winners:{giveaway_id}")
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_giveaway:{giveaway_id}")
            keyboard.button(text="👀 Предпросмотр", callback_data=f"preview_giveaway:{giveaway_id}")  # Новая кнопка
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            keyboard.adjust(1)

            invite_info = f"\n📩 Пригласите {giveaway['quantity_invite']} друга(зей) для участия!" if giveaway.get(
                'invite', False) else ""
            giveaway_info = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

🏆 <b>Победителей:</b> {giveaway['winner_count']}
⏰ <b>Конец:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
{invite_info}
"""

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
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка загрузки розыгрыша 😔")
            await bot.send_message(
                callback_query.from_user.id,
                "⚠️ Упс! Что-то пошло не так. Попробуйте снова! 😊"
            )

    @dp.callback_query(lambda c: c.data.startswith('preview_giveaway:'))
    async def process_preview_giveaway(callback_query: CallbackQuery):
        """Показывает предпросмотр розыгрыша как при публикации и меняет is_active на 'waiting'"""
        giveaway_id = callback_query.data.split(':')[1]
        try:
            # Получаем текущие данные розыгрыша
            response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            if not response.data:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                return

            giveaway = response.data

            # Обновляем состояние is_active на 'waiting'
            supabase.table('giveaways').update({'is_active': 'waiting'}).eq('id', giveaway_id).execute()
            logger.info(f"Состояние is_active для розыгрыша {giveaway_id} изменено на 'waiting'")

            # Получаем количество участников
            participant_count = await get_participant_count(giveaway_id, supabase)

            # Формируем текст предпросмотра
            post_text = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

🏆 <b>Победителей:</b> {giveaway['winner_count']}
⏰ <b>Конец:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
"""

            # Создаём клавиатуру для предпросмотра
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"🎉 Участвовать ({participant_count})",
                url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}"
            )
            keyboard.button(
                text="◀️ Назад",
                callback_data=f"view_created_giveaway:{giveaway_id}"
            )
            keyboard.adjust(1)

            await bot.answer_callback_query(callback_query.id)

            # Отображаем предпросмотр с учётом медиа
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
                        caption=post_text,
                        parse_mode='HTML'
                    ),
                    reply_markup=keyboard.as_markup()
                )
            else:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    post_text,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML'
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка предпросмотра: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при предпросмотре 😔")

    @dp.callback_query(lambda c: c.data.startswith('add_invite_task:'))
    async def process_add_invite_task(callback_query: CallbackQuery):
        """Добавление задания с приглашениями 📩"""
        giveaway_id = callback_query.data.split(':')[1]
        response = supabase.table('giveaways').select('invite', 'quantity_invite').eq('id', giveaway_id).single().execute()
        giveaway = response.data

        keyboard = InlineKeyboardBuilder()
        if giveaway.get('invite', False):
            keyboard.button(text="✏️ Изменить количество", callback_data=f"change_invite_quantity:{giveaway_id}")
            keyboard.button(text="🗑️ Убрать задание", callback_data=f"remove_invite_task:{giveaway_id}")
            keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)
            message_text = f"📩 Задание 'Пригласить друга' уже активно!\n\n👥 Нужно пригласить {giveaway['quantity_invite']} друга(зей)"
        else:
            keyboard.button(text="✅ Да", callback_data=f"confirm_invite_task:{giveaway_id}")
            keyboard.button(text=" ◀️ Нет", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(2)
            message_text = "📩 Хотите добавить задание 'Пригласить друга'?"

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_invite_task:'))
    async def process_confirm_invite_task(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_invite_quantity)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Назад", callback_data=f"add_invite_task:{giveaway_id}")

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "📩 Сколько друзей должен пригласить участник?\nВведите число",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('change_invite_quantity:'))
    async def process_change_invite_quantity(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_invite_quantity)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "✏️ Введите новое количество друзей для приглашения",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('remove_invite_task:'))
    async def process_remove_invite_task(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            supabase.table('giveaways').update({'invite': False, 'quantity_invite': 0}).eq('id', giveaway_id).execute()
            await bot.answer_callback_query(callback_query.id, text="Задание убрано ✅")
            new_callback_query = types.CallbackQuery(
                id=callback_query.id,
                from_user=callback_query.from_user,
                chat_instance=callback_query.chat_instance,
                message=callback_query.message,
                data=f"view_created_giveaway:{giveaway_id}"
            )
            await process_view_created_giveaway(new_callback_query)
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Упс! Не удалось убрать задание 😔")

    @dp.message(GiveawayStates.waiting_for_invite_quantity)
    async def process_invite_quantity(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']

        try:
            quantity = int(message.text)
            if quantity <= 0:
                raise ValueError("Количество должно быть положительным")

            supabase.table('giveaways').update({'invite': True, 'quantity_invite': quantity}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Изменить количество", callback_data=f"change_invite_quantity:{giveaway_id}")
            keyboard.button(text="🗑️ Убрать задание", callback_data=f"remove_invite_task:{giveaway_id}")
            keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_with_image(
                bot,
                message.from_user.id,
                f"✅ Задание добавленно\n\n👥 Пригласить {quantity} другу(зей) для участия!",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )
            await state.clear()

        except ValueError:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.from_user.id,
                "⚠️ Введите положительное число! Например, 5 😊",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_post:'))
    async def process_edit_post(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    async def _show_edit_menu(user_id: int, giveaway_id: str, message_id: int = None):
        """Показывает меню редактирования розыгрыша ✏️"""
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        if not response.data:
            await bot.send_message(user_id, "🔍 Розыгрыш не найден 😕")
            return

        giveaway = response.data
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_description:{giveaway_id}")
        keyboard.button(text="🏆 Победители", callback_data=f"edit_winner_count:{giveaway_id}")
        keyboard.button(text="⏰ Дата", callback_data=f"change_end_date:{giveaway_id}")
        keyboard.button(text="🖼️ Медиа", callback_data=f"manage_media:{giveaway_id}")
        keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(2, 2, 1, 1)

        invite_info = f"\n👥 Пригласите {giveaway['quantity_invite']} друга(зей)!" if giveaway.get('invite', False) else ""
        giveaway_info = f"""
📊 <b>Ваш розыгрыш:</b>

<b>Название:</b> {giveaway['name']}

<b>Описание:</b> {giveaway['description']}

🏆 <b>Победителей:</b> {giveaway['winner_count']}
⏰ <b>Конец:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
🖼️ <b>Медиа:</b> {'✅ Есть' if giveaway['media_type'] else '❌ Нет'}
{invite_info}
✨ Что хотите изменить?
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
            await bot.send_message(user_id, "❌ Упс! Ошибка при загрузке меню. Попробуйте снова! 😊", parse_mode='HTML')

    @dp.callback_query(lambda c: c.data.startswith('edit_name:'))
    async def process_edit_name(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_name)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"✏️ Введите новое название (до {MAX_NAME_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_description:'))
    async def process_edit_description(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_description)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"📝 Введите новое описание (до {MAX_DESCRIPTION_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count:'))
    async def process_edit_winner_count(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_winner_count)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"🏆 Сколько будет победителей? Максимум {MAX_WINNERS}! Введите число 😊",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(GiveawayStates.waiting_for_edit_name)
    async def process_new_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_name = message.html_text if message.text else ""

        if len(new_name) > MAX_NAME_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"⚠️ Название слишком длинное! Максимум {MAX_NAME_LENGTH} символов, сейчас {len(new_name)}. Сократите! 😊\n{FORMATTING_GUIDE}",
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
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Упс! Не удалось обновить название 😔",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
        await state.clear()

    @dp.message(GiveawayStates.waiting_for_edit_description)
    async def process_new_description(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_description = message.html_text if message.text else ""

        if len(new_description) > MAX_DESCRIPTION_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"⚠️ Описание слишком длинное! Максимум {MAX_DESCRIPTION_LENGTH} символов, сейчас {len(new_description)}. Сократите! 😊\n{FORMATTING_GUIDE}",
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
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить описание 😔",
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
                raise ValueError("Количество должно быть положительным")

            if new_winner_count > MAX_WINNERS:
                data = await state.get_data()
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"⚠️ Слишком много победителей! Максимум {MAX_WINNERS} 😊",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

            data = await state.get_data()
            giveaway_id = data['giveaway_id']
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "⏳ Обновляем количество победителей...",
                message_id=data.get('last_message_id'),
            )

            current_winner_count_response = supabase.table('giveaways').select('winner_count').eq('id', giveaway_id).single().execute()
            current_winner_count = current_winner_count_response.data['winner_count']

            supabase.table('giveaways').update({'winner_count': new_winner_count}).eq('id', giveaway_id).execute()

            if new_winner_count > current_winner_count:
                for place in range(current_winner_count + 1, new_winner_count + 1):
                    supabase.table('congratulations').insert({
                        'giveaway_id': giveaway_id,
                        'place': place,
                        'message': f"🎉 Поздравляем! Вы заняли {place} место!"
                    }).execute()
            elif new_winner_count < current_winner_count:
                supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).gte('place', new_winner_count + 1).execute()

            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "⚠️ Введите положительное число! Например, 3 😊",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить победителей 😔",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('manage_media:'))
    async def process_manage_media(callback_query: CallbackQuery, state: FSMContext):
        """Управление медиа для розыгрыша 🖼️"""
        giveaway_id = callback_query.data.split(':')[1]
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if giveaway['media_type']:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Изменить медиа", callback_data=f"change_media:{giveaway_id}")
            keyboard.button(text="🗑️ Удалить медиа", callback_data=f"delete_media:{giveaway_id}")
            keyboard.button(text=" ◀️ Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")
            keyboard.adjust(1)
            text = "🖼️ Что сделать с медиа?"
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✅ Добавить", callback_data=f"add_media:{giveaway_id}")
            keyboard.button(text=" ◀️ Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")
            keyboard.adjust(2)
            text = f"🖼️ Добавить фото, GIF или видео? Максимум {MAX_MEDIA_SIZE_MB} МБ! 📎"

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
    async def process_add_or_change_media(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id)
        await state.set_state(GiveawayStates.waiting_for_media_edit)

        data = await state.get_data()
        last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data=f"manage_media:{giveaway_id}")]])

        message = await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"📸 Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)! 😊",
            reply_markup=keyboard,
            message_id=last_message_id
        )
        if message:
            await state.update_data(last_bot_message_id=message.message_id)
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('back_to_edit_menu:'))
    async def process_back_to_edit_menu(callback_query: CallbackQuery, state: FSMContext):
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

            keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=" ◀️ Назад", callback_data=f"back_to_edit_menu:{giveaway_id}")]])
            await send_message_with_image(
                bot,
                message.from_user.id,
                "⏳ Загружаем ваше медиа...",
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
                await message.reply("⚠️ Пожалуйста, отправьте фото, GIF или видео! 😊")
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)

            file_size_mb = file.file_size / (1024 * 1024)
            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await send_message_with_image(
                    bot,
                    message.from_user.id,
                    f"⚠️ Файл слишком большой! Максимум {MAX_MEDIA_SIZE_MB} МБ 😔",
                    reply_markup=keyboard,
                    message_id=last_message_id
                )
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(result)

            supabase.table('giveaways').update({'media_type': media_type, 'media_file_id': result}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, last_message_id)

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await message.reply("❌ Ой! Не удалось загрузить медиа 😔")
            await state.clear()

    @dp.callback_query(lambda c: c.data.startswith('delete_media:'))
    async def process_delete_media(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            supabase.table('giveaways').update({'media_type': None, 'media_file_id': None}).eq('id', giveaway_id).execute()
            data = await state.get_data()
            last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id
            await _show_edit_menu(callback_query.from_user.id, giveaway_id, last_message_id)
            await bot.answer_callback_query(callback_query.id, text="✅ Медиа удалено! ✨")
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Не удалось удалить медиа 😔")

    @dp.callback_query(lambda c: c.data.startswith('delete_giveaway:'))
    async def process_delete_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✅ Да", callback_data=f"confirm_delete_giveaway:{giveaway_id}")
        keyboard.button(text="❌ Нет", callback_data=f"cancel_delete_giveaway:{giveaway_id}")
        keyboard.adjust(2)
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "🗑️ Вы уверены, что хотите удалить розыгрыш? 😮",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_delete_giveaway:'))
    async def process_confirm_delete_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            supabase.table('giveaway_communities').delete().eq('giveaway_id', giveaway_id).execute()
            supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()
            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).execute()
            response = supabase.table('giveaways').delete().eq('id', giveaway_id).execute()

            if response.data:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    "✅ Розыгрыш успешно удалён! ✨",
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )
            else:
                raise Exception("Нет данных об удалении")
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "❌ Упс! Не удалось удалить розыгрыш 😔",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )

    @dp.callback_query(lambda c: c.data.startswith('cancel_delete_giveaway:'))
    async def process_cancel_delete_giveaway(callback_query: CallbackQuery):
        await process_view_created_giveaway(callback_query)

    @dp.callback_query(lambda c: c.data.startswith('change_end_date:'))
    async def process_change_end_date(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_new_end_time)
        await callback_query.answer()

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
⏰ Укажите новую дату завершения в формате ДД.ММ.ГГГГ ЧЧ:ММ

📅 Сейчас в Москве: <code>{current_time}</code>
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
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "⏳ Обновляем дату завершения...",
                message_id=data.get('last_message_id'),
            )

            supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id', giveaway_id).execute()
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""
⚠️ Неправильный формат даты! Используйте ДД.ММ.ГГГГ ЧЧ:ММ

📅 Сейчас в Москве: <code>{current_time}</code>
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
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text=" ◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить дату 😔",
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
                logger.error(f"🚫 Сообщество {community_id} не найдено")
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
            logger.error(f"🚫 Ошибка: {str(e)}")
            return False

    async def unbind_community_from_giveaway(giveaway_id, community_id):
        supabase.table("giveaway_communities").delete().eq("giveaway_id", giveaway_id).eq("community_id", community_id).execute()

    @dp.callback_query(lambda c: c.data == 'bind_communities:' or c.data.startswith('bind_communities:'))
    async def process_bind_communities(callback_query: CallbackQuery, state: FSMContext):
        if callback_query.data == 'bind_communities:':
            await bot.answer_callback_query(callback_query.id, text="❌ Неверный формат данных 😔")
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

        keyboard.button(text="✅ Подтвердить", callback_data=f"confirm_community_selection:{giveaway_id}")
        keyboard.button(text="➕ Новый паблик", callback_data=f"bind_new_community:{giveaway_id}")
        keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "👥 Выберите сообщества для привязки и нажмите 'Подтвердить'! ✨",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    def truncate_name(name, max_length=20):
        return name if len(name) <= max_length else name[:max_length - 3] + "..."

    @dp.callback_query(lambda c: c.data.startswith('toggle_community:'))
    async def process_toggle_community(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        parts = callback_query.data.split(':')
        if len(parts) >= 4:
            _, giveaway_id, community_id, community_username = parts
        else:
            await bot.answer_callback_query(callback_query.id, text="❌ Неверные данные 😔")
            return

        try:
            response = supabase.table('bound_communities').select('community_name').eq('community_id', community_id).execute()
            community_name = response.data[0]['community_name'] if response.data else community_username

            if user_id not in user_selected_communities or user_selected_communities[user_id]['giveaway_id'] != giveaway_id:
                user_selected_communities[user_id] = {'giveaway_id': giveaway_id, 'communities': set()}

            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        if '✅' in button.text:
                            new_text = f"{truncate_name(community_name)}"
                            user_selected_communities[user_id]['communities'].discard((community_id, community_username))
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
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Упс! Ошибка при выборе сообщества 😔")

    async def get_giveaway_communities(giveaway_id):
        response = supabase.table("giveaway_communities").select("*").eq("giveaway_id", giveaway_id).execute()
        return response.data

    @dp.callback_query(lambda c: c.data.startswith('activate_giveaway:'))
    async def process_activate_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        try:
            response = supabase.table('giveaway_communities').select('community_id', 'community_username', 'community_name').eq('giveaway_id', giveaway_id).execute()
            communities = response.data

            if not communities:
                await bot.answer_callback_query(callback_query.id, text="⚠️ Нет привязанных сообществ для публикации! 😕")
                return

            keyboard = InlineKeyboardBuilder()
            for community in communities:
                display_name = truncate_name(community['community_name'])
                callback_data = f"toggle_activate_community:{giveaway_id}:{community['community_id']}:{community['community_username']}"
                if len(callback_data.encode('utf-8')) > 60:
                    callback_data = f"toggle_activate_community:{giveaway_id}:{community['community_id']}:id"
                keyboard.button(text=display_name, callback_data=callback_data)
            keyboard.button(text="✅ Подтвердить", callback_data=f"confirm_activate_selection:{giveaway_id}")
            keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                "🚀 Выберите сообщества для публикации (нажмите для выбора/отмены):",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при загрузке сообществ 😔")

    @dp.callback_query(lambda c: c.data.startswith('toggle_activate_community:'))
    async def process_toggle_activate_community(callback_query: CallbackQuery):
        _, giveaway_id, community_id, community_username = callback_query.data.split(':')
        try:
            response = supabase.table('bound_communities').select('community_name').eq('community_id', community_id).execute()
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
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при выборе сообщества 😔")

    @dp.callback_query(lambda c: c.data.startswith('confirm_activate_selection:'))
    async def process_confirm_activate_selection(callback_query: CallbackQuery):
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
            await bot.answer_callback_query(callback_query.id, text="⚠️ Выберите хотя бы одно сообщество! 😊")
            return

        user_selected_communities[user_id] = {
            'giveaway_id': giveaway_id,
            'communities': [(comm[0], comm[1]) for comm in selected_communities]
        }

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🚀 Опубликовать", callback_data=f"publish_giveaway:{giveaway_id}")
        keyboard.button(text=" ◀️ Назад", callback_data=f"activate_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        await bot.answer_callback_query(callback_query.id)
        community_names = [comm[2] for comm in selected_communities]
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"✨ Розыгрыш будет опубликован в: {', '.join(community_names)}! Подтвердите запуск! 🚀",
            keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_community_selection:'))
    async def process_confirm_community_selection(callback_query: CallbackQuery):
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
                        logger.error(f"🚫 Ошибка привязки: {community_username}")
                for community_id, _ in to_remove:
                    await unbind_community_from_giveaway(giveaway_id, community_id)

                await bot.answer_callback_query(callback_query.id, text="✅ Сообщества обновлены! ✨")

            # Перенаправление в любом случае
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

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при обновлении сообществ 😔")

    @dp.callback_query(lambda c: c.data.startswith('publish_giveaway:'))
    async def process_publish_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        participant_counter_tasks = []

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text=" ◀️ Отмена", callback_data=f"activate_giveaway:{giveaway_id}")
        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            "⏳ Публикуем ваш розыгрыш...",
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

        user_data = user_selected_communities.get(user_id)
        if not user_data or user_data['giveaway_id'] != giveaway_id or not user_data.get('communities'):
            await bot.answer_callback_query(callback_query.id, text="❌ Нет выбранных сообществ для публикации! 😔")
            return

        selected_communities = user_data['communities']

        try:
            giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            giveaway = giveaway_response.data

            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                return

            participant_count = await get_participant_count(giveaway_id, supabase)
            post_text = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

🏆 <b>Победителей:</b> {giveaway['winner_count']}
⏰ <b>Конец:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
"""

            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"🎉 Участвовать ({participant_count})",
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
                            sent_message = await bot.send_photo(int(community_id), giveaway['media_file_id'], caption=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                        elif giveaway['media_type'] == 'gif':
                            sent_message = await bot.send_animation(int(community_id), animation=giveaway['media_file_id'], caption=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                        elif giveaway['media_type'] == 'video':
                            sent_message = await bot.send_video(int(community_id), video=giveaway['media_file_id'], caption=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                    else:
                        sent_message = await bot.send_message(int(community_id), text=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')

                    if sent_message:
                        published_messages.append({'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                        participant_counter_tasks.append({'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                        success_count += 1
                    await asyncio.sleep(0.5)

                except aiogram.exceptions.TelegramRetryAfter as e:
                    retry_after = e.retry_after
                    logger.warning(f"⏳ Лимит Telegram, ждём {retry_after} сек.")
                    await asyncio.sleep(retry_after)
                    try:
                        if giveaway['media_type'] == 'photo':
                            sent_message = await bot.send_photo(int(community_id), giveaway['media_file_id'], caption=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                        elif giveaway['media_type'] == 'gif':
                            sent_message = await bot.send_animation(int(community_id), animation=giveaway['media_file_id'], caption=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                        elif giveaway['media_type'] == 'video':
                            sent_message = await bot.send_video(int(community_id), video=giveaway['media_file_id'], caption=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                        else:
                            sent_message = await bot.send_message(int(community_id), text=post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')

                        if sent_message:
                            published_messages.append({'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                            participant_counter_tasks.append({'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                            success_count += 1
                    except Exception as retry_error:
                        error_count += 1
                        error_messages.append(f"❌ Ошибка в @{community_username} после паузы: {str(retry_error)}")
                except Exception as e:
                    error_count += 1
                    error_messages.append(f"❌ Ошибка в @{community_username}: {str(e)}")

            if success_count > 0:
                try:
                    supabase.table('giveaway_winners').delete().eq('giveaway_id', giveaway_id).execute()
                    supabase.table('participations').delete().eq('giveaway_id', giveaway_id).execute()

                    moscow_tz = pytz.timezone('Europe/Moscow')
                    current_time = datetime.now(moscow_tz)

                    supabase.table('giveaways').update({
                        'is_active': 'true',
                        'created_at': current_time.isoformat(),
                        'published_messages': json.dumps(published_messages),
                        'participant_counter_tasks': json.dumps(participant_counter_tasks)
                    }).eq('id', giveaway_id).execute()

                    counter_tasks = []
                    for task_info in participant_counter_tasks:
                        task = asyncio.create_task(
                            start_participant_counter(bot, task_info['chat_id'], task_info['message_id'], giveaway_id, supabase)
                        )
                        counter_tasks.append(task)

                    await bot.answer_callback_query(callback_query.id, text="✅ Розыгрыш запущен! 🎉")

                    keyboard = InlineKeyboardBuilder()
                    keyboard.button(text="🏠 Назад", callback_data="back_to_main_menu")

                    result_message = f"<b>✅ Успешно опубликовано в {success_count} сообществах!</b>\n🔄 Участники будут обновляться каждую минуту."
                    if error_count > 0:
                        result_message += f"\n\n<b>❌ Ошибок: {error_count}</b>"
                        for error in error_messages:
                            if "bot is not a member" in error:
                                community = error.split('@')[1].split(':')[0]
                                result_message += f"\n⚠️ @{community}: Бот не админ или сообщество удалено"
                            else:
                                result_message += f"\n{error}"

                    await send_message_with_image(
                        bot,
                        callback_query.from_user.id,
                        result_message,
                        reply_markup=keyboard.as_markup(),
                        message_id=callback_query.message.message_id,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"🚫 Ошибка активации: {str(e)}")
                    await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при запуске розыгрыша 😔")
            else:
                await bot.answer_callback_query(callback_query.id, text="❌ Не удалось опубликовать 😔")
                error_keyboard = InlineKeyboardBuilder()
                error_keyboard.button(text=" ◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    f"<b>❌ Публикация не удалась</b>\nОшибок: {error_count}\n\n<b>Подробности:</b>\n" + "\n".join(error_messages),
                    reply_markup=error_keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при публикации 😔")
        finally:
            user_selected_communities.pop(user_id, None)

    async def get_participant_count(giveaway_id: str, supabase: Client) -> int:
        try:
            response = supabase.table('participations').select('*', count='exact').eq('giveaway_id', giveaway_id).execute()
            return response.count if hasattr(response, 'count') else 0
        except Exception as e:
            logger.error(f"🚫 Ошибка подсчёта участников: {str(e)}")
            return 0

    async def update_participant_button(bot: Bot, chat_id: int, message_id: int, giveaway_id: str, supabase: Client):
        try:
            count = await get_participant_count(giveaway_id, supabase)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text=f"🎉 Участвовать ({count})",
                url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}"
            )
            keyboard.adjust(1)
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка обновления кнопки: {str(e)}")

    async def start_participant_counter(bot: Bot, chat_id: int, message_id: int, giveaway_id: str, supabase: Client):
        while True:
            await update_participant_button(bot, chat_id, message_id, giveaway_id, supabase)
            await asyncio.sleep(60)

# Запуск обработчиков (в основном коде программы)
register_created_giveaways_handlers(dp, bot, supabase)
