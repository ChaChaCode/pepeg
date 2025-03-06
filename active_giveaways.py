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
import requests
import re
from aiogram.types import CallbackQuery

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
MAX_NAME_LENGTH = 100
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
- Кастомные эмодзи
</blockquote>
"""

def strip_html_tags(text: str) -> str:
    """Удаляет HTML-теги из текста 🧹"""
    return re.sub(r'<[^>]+>', '', text)

class EditGiveawayStates(StatesGroup):
    waiting_for_new_name_active = State()  # ✏️
    waiting_for_new_description_active = State()  # 📜
    waiting_for_new_winner_count_active = State()  # 🏆
    waiting_for_new_end_time_active = State()  # ⏰
    waiting_for_new_media_active = State()  # 🖼️

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
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

def register_active_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    """Регистрирует обработчики для активных розыгрышей 🎁"""

    @dp.callback_query(lambda c: c.data == 'active_giveaways' or c.data.startswith('active_giveaways_page:'))
    async def process_active_giveaways(callback_query: types.CallbackQuery):
        """Показывает список активных розыгрышей 📋"""
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if ':' in callback_query.data else 1

        try:
            response = supabase.table('giveaways').select('*').eq('is_active', 'true').eq('user_id', user_id).order('end_time').execute()
            if not response.data:
                await bot.answer_callback_query(callback_query.id, text="📭 У вас нет активных розыгрышей! Создайте новый? 🚀")
                return

            total_giveaways = len(response.data)
            total_pages = math.ceil(total_giveaways / ITEMS_PER_PAGE)
            start_idx = (current_page - 1) * ITEMS_PER_PAGE
            current_giveaways = response.data[start_idx:start_idx + ITEMS_PER_PAGE]

            keyboard = InlineKeyboardBuilder()
            for giveaway in current_giveaways:
                clean_name = strip_html_tags(giveaway['name'])[:61] + "..." if len(giveaway['name']) > 64 else strip_html_tags(giveaway['name'])
                keyboard.row(types.InlineKeyboardButton(
                    text=f"{clean_name}",
                    callback_data=f"view_active_giveaway:{giveaway['id']}"
                ))

            nav_buttons = []
            if current_page > 1:
                nav_buttons.append(types.InlineKeyboardButton(text="◀️", callback_data=f"active_giveaways_page:{current_page - 1}"))
            if total_pages > 1:
                nav_buttons.append(types.InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="ignore"))
            if current_page < total_pages:
                nav_buttons.append(types.InlineKeyboardButton(text="▶️", callback_data=f"active_giveaways_page:{current_page + 1}"))

            if nav_buttons:
                keyboard.row(*nav_buttons)
            keyboard.row(types.InlineKeyboardButton(text="◀️ Назад", callback_data="back_to_main_menu"))

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(
                bot,
                user_id,
                f"<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите активный розыгрыш для просмотра!",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Упс! Ошибка загрузки розыгрышей 😔")

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('view_active_giveaway:'))
    async def process_view_active_giveaway(callback_query: types.CallbackQuery):
        """Просмотр активного розыгрыша 👀"""
        giveaway_id = callback_query.data.split(':')[1]
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()

        if not response.data:
            await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
            return

        giveaway = response.data
        participants_response = supabase.table('participations').select('count').eq('giveaway_id', giveaway_id).execute()
        participants_count = participants_response.data[0]['count']

        giveaway_info = f"""
<b>{giveaway['name']}</b>

{giveaway['description']}

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}
<tg-emoji emoji-id='5449683594425410231'>🔼</tg-emoji> <b>Участников:</b> {participants_count}
"""

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✏️ Редактировать", callback_data=f"edit_active_post:{giveaway_id}")
        keyboard.button(text="⏹️ Завершить", callback_data=f"confirm_force_end_giveaway:{giveaway_id}")
        keyboard.button(text="🔗 Открыть", url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data="active_giveaways")
        keyboard.adjust(1)

        try:
            await bot.answer_callback_query(callback_query.id)
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
                        media=media_class(media=giveaway['media_file_id'], caption=giveaway_info, parse_mode='HTML'),
                        reply_markup=keyboard.as_markup()
                    )
                else:
                    raise ValueError(f"Неизвестный тип медиа: {giveaway['media_type']}")
            else:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML'
                )
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message to edit not found" in str(e):
                logger.warning(f"Сообщение не найдено: {e}")
                await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
            else:
                raise

    @dp.callback_query(lambda c: c.data.startswith('confirm_force_end_giveaway:'))
    async def process_confirm_force_end_giveaway(callback_query: types.CallbackQuery):
        """Запрос подтверждения для завершения розыгрыша ⏹️"""
        giveaway_id = callback_query.data.split(':')[1]
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✅ Да", callback_data=f"force_end_giveaway:{giveaway_id}")
        keyboard.button(text="❌ Нет", callback_data=f"view_active_giveaway:{giveaway_id}")
        keyboard.adjust(2)

        await bot.answer_callback_query(callback_query.id)
        await send_message_with_image(
            bot,
            chat_id=callback_query.from_user.id,
            message_id=callback_query.message.message_id,
            text="<tg-emoji emoji-id='5352640560718949874'>🤨</tg-emoji> Вы уверены, что хотите завершить розыгрыш?\nЭто действие нельзя отменить!",
            reply_markup=keyboard.as_markup()
        )

    @dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
    async def process_force_end_giveaway(callback_query: types.CallbackQuery):
        """Принудительное завершение розыгрыша после подтверждения ⏹️"""
        giveaway_id = callback_query.data.split(':')[1]
        await bot.answer_callback_query(callback_query.id, text="Завершаем розыгрыш...")

        try:
            await end_giveaway(bot=bot, giveaway_id=giveaway_id, supabase=supabase)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                text="✅ Розыгрыш завершён! Результаты уже в сообществах 🎉",
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка завершения розыгрыша: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="active_giveaways")
            await send_message_with_image(
                bot,
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                text="❌ Упс! Не удалось завершить розыгрыш 😔 Попробуйте снова!",
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_active_post:'))
    async def process_edit_active_post(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    async def _show_edit_menu_active(user_id: int, giveaway_id: str, message_id: int = None):
        """Показывает меню редактирования активного розыгрыша ✏️"""
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        if not response.data:
            await bot.send_message(user_id, "🔍 Розыгрыш не найден 😕")
            return

        giveaway = response.data
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name_active:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_name_active_active:{giveaway_id}")
        keyboard.button(text="🏆 Победители", callback_data=f"edit_winner_count_active:{giveaway_id}")
        keyboard.button(text="⏰ Дата", callback_data=f"change_end_date_active:{giveaway_id}")
        keyboard.button(text="🖼️ Медиа", callback_data=f"view_manage_media:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_active_giveaway:{giveaway_id}")
        keyboard.adjust(2, 2, 1, 1)

        invite_info = f"\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Приглашайте {giveaway['quantity_invite']} друзей!" if giveaway.get('invite', False) else ""
        giveaway_info = f"""
<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Что хотите изменить?

<b>Название:</b> {giveaway['name']}

<b>Описание:</b> {giveaway['description']}

<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}
<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> <b>Медиа:</b> {'✅ Есть' if giveaway['media_type'] else '❌ Нет'}
{invite_info}
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
                        media=media_class(media=giveaway['media_file_id'], caption=giveaway_info, parse_mode='HTML'),
                        reply_markup=keyboard.as_markup()
                    )
                else:
                    raise ValueError(f"Неизвестный тип медиа: {giveaway['media_type']}")
            else:
                await send_message_with_image(
                    bot,
                    user_id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=message_id,
                    parse_mode='HTML'
                )
        except aiogram.exceptions.TelegramBadRequest as e:
            if "message to edit not found" in str(e):
                logger.warning(f"Сообщение не найдено: {e}")
                await send_new_giveaway_message(user_id, giveaway, giveaway_info, keyboard)
            else:
                raise
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.send_message(user_id, "❌ Упс! Ошибка загрузки меню 😔 Попробуйте снова!", parse_mode='HTML')

    async def send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard):
        """Отправляет новое сообщение о розыгрыше 📬"""
        if giveaway['media_type'] and giveaway['media_file_id']:
            if giveaway['media_type'] == 'photo':
                await bot.send_photo(chat_id, giveaway['media_file_id'], caption=giveaway_info, reply_markup=keyboard.as_markup(), parse_mode='HTML')
            elif giveaway['media_type'] == 'gif':
                await bot.send_animation(chat_id, animation=giveaway['media_file_id'], caption=giveaway_info, reply_markup=keyboard.as_markup(), parse_mode='HTML')
            elif giveaway['media_type'] == 'video':
                await bot.send_video(chat_id, video=giveaway['media_file_id'], caption=giveaway_info, reply_markup=keyboard.as_markup(), parse_mode='HTML')
        else:
            await send_message_with_image(bot, chat_id, giveaway_info, reply_markup=keyboard.as_markup(), parse_mode='HTML')

    async def update_published_posts_active(giveaway_id: str, new_giveaway_data: dict):
        """Обновляет опубликованные посты активного розыгрыша 📢"""
        try:
            giveaway_response = supabase.table('giveaways').select('published_messages').eq('id', giveaway_id).single().execute()
            published_messages = json.loads(giveaway_response.data['published_messages'])

            for message in published_messages:
                chat_id = message['chat_id']
                message_id = message['message_id']

                participants_response = supabase.table('participations').select('count').eq('giveaway_id', giveaway_id).execute()
                participants_count = participants_response.data[0]['count']

                new_post_text = f"""
<b>{new_giveaway_data['name']}</b>

{new_giveaway_data['description']}

<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {new_giveaway_data['winner_count']}
<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {(datetime.fromisoformat(new_giveaway_data['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')} (МСК)
"""

                keyboard = InlineKeyboardBuilder()
                keyboard.button(text=f"🎉 Участвовать ({participants_count})", url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway_id}")

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
                                media=media_class(media=new_giveaway_data['media_file_id'], caption=new_post_text, parse_mode='HTML'),
                                reply_markup=keyboard.as_markup()
                            )
                        else:
                            raise ValueError(f"Неизвестный тип медиа: {new_giveaway_data['media_type']}")
                    else:
                        try:
                            await bot.edit_message_text(
                                chat_id=chat_id,
                                message_id=message_id,
                                text=new_post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        except aiogram.exceptions.TelegramBadRequest as e:
                            if "there is no text in the message to edit" in str(e).lower():
                                new_message = await bot.send_message(chat_id, text=new_post_text, reply_markup=keyboard.as_markup(), parse_mode='HTML')
                                try:
                                    await bot.delete_message(chat_id=chat_id, message_id=message_id)
                                except aiogram.exceptions.TelegramBadRequest:
                                    logger.warning(f"Не удалось удалить сообщение {message_id} в чате {chat_id}")
                                updated_messages = [msg for msg in published_messages if msg['message_id'] != message_id]
                                updated_messages.append({'chat_id': chat_id, 'message_id': new_message.message_id})
                                supabase.table('giveaways').update({'published_messages': json.dumps(updated_messages)}).eq('id', giveaway_id).execute()
                            else:
                                raise
                except Exception as e:
                    logger.error(f"🚫 Ошибка обновления поста: {str(e)}")
        except Exception as e:
            logger.error(f"🚫 Ошибка обновления постов: {str(e)}")

    @dp.callback_query(lambda c: c.data.startswith('edit_name_active:'))
    async def process_edit_name_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_name_active)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Введите новое название (до {MAX_NAME_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.message(EditGiveawayStates.waiting_for_new_name_active)
    async def process_new_name_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_name = message.html_text if message.text else ""

        if len(new_name) > MAX_NAME_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Название длинное! Максимум {MAX_NAME_LENGTH} символов, сейчас {len(new_name)}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        try:
            supabase.table('giveaways').update({'name': new_name}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить название 😔",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
        await state.clear()

    @dp.callback_query(lambda c: c.data.startswith('edit_name_active_active:'))
    async def process_edit_description_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_description_active)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Введите новое описание (до {MAX_DESCRIPTION_LENGTH} символов):\n{FORMATTING_GUIDE}",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    @dp.message(EditGiveawayStates.waiting_for_new_description_active)
    async def process_new_description_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        new_description = message.html_text if message.text else ""

        if len(new_description) > MAX_DESCRIPTION_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_with_image(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Описание длинное! Максимум {MAX_DESCRIPTION_LENGTH} символов, сейчас {len(new_description)}. Сократите!\n{FORMATTING_GUIDE}",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
            return

        try:
            supabase.table('giveaways').update({'description': new_description}).eq('id', giveaway_id).execute()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить описание 😔",
                reply_markup=keyboard.as_markup(),
                message_id=data['last_message_id'],
                parse_mode='HTML'
            )
        await state.clear()

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count_active:'))
    async def process_edit_winner_count_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_winner_count_active)
        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Сколько будет победителей? Максимум {MAX_WINNERS}! Введите число",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )

    @dp.message(EditGiveawayStates.waiting_for_new_winner_count_active)
    async def process_new_winner_count_active(message: types.Message, state: FSMContext):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Обновляем количество победителей...",
                message_id=data.get('last_message_id'),
            )

            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Количество должно быть положительным")

            if new_winner_count > MAX_WINNERS:
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Слишком много! Максимум {MAX_WINNERS} победителей",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

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

            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)
            await state.clear()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число! Например, 3",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить победителей 😔",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('change_end_date_active:'))
    async def process_change_end_date_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_end_time_active)
        await callback_query.answer()

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
Укажите новую дату окончания в формате ДД.ММ.ГГГГ ЧЧ:ММ

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

    @dp.message(EditGiveawayStates.waiting_for_new_end_time_active)
    async def process_new_end_time_active(message: types.Message, state: FSMContext):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        if message.text.lower() == 'отмена':
            await state.clear()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])
            return

        try:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Обновляем дату...",
                message_id=data.get('last_message_id'),
            )

            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            moscow_tz = pytz.timezone('Europe/Moscow')
            new_end_time_tz = moscow_tz.localize(new_end_time)

            supabase.table('giveaways').update({'end_time': new_end_time_tz.isoformat()}).eq('id', giveaway_id).execute()
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute().data
            await update_published_posts_active(giveaway_id, giveaway_data)
            await state.clear()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            html_message = f"""
<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Неверный формат!\nИспользуйте ДД.ММ.ГГГГ ЧЧ:ММ

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
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
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось обновить дату 😔",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('view_manage_media:'))
    async def process_view_manage_media_active(callback_query: CallbackQuery, state: FSMContext):
        """Управление медиа активного розыгрыша 🖼️"""
        giveaway_id = callback_query.data.split(':')[1]
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if giveaway['media_type']:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Изменить медиа", callback_data=f"change_media_active:{giveaway_id}")
            keyboard.button(text="🗑️ Удалить медиа", callback_data=f"delete_media_active:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            keyboard.adjust(1)
            text = "<tg-emoji emoji-id='5352640560718949874'>🤨</tg-emoji> Что сделать с медиа?"
        else:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✅ Добавить", callback_data=f"add_media_active:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            keyboard.adjust(2)
            text = f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Добавить фото, GIF или видео? Максимум {MAX_MEDIA_SIZE_MB} МБ! 📎"

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('add_media_active:') or c.data.startswith('change_media_active:'))
    async def process_add_or_change_media_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_media_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"view_manage_media:{giveaway_id}")

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)!",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id
        )
        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_media_active)
    async def process_new_media_active(message: types.Message, state: FSMContext):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Загружаем медиа...",
                message_id=data.get('last_message_id'),
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
                    "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Отправьте фото, GIF или видео! ",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)
            file_size_mb = file.file_size / (1024 * 1024)

            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await send_message_with_image(
                    bot,
                    message.from_user.id,
                    f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл большой! Максимум {MAX_MEDIA_SIZE_MB} МБ, сейчас {file_size_mb:.2f} МБ 😔",
                    reply_markup=keyboard.as_markup(),
                    message_id=data.get('last_message_id')
                )
                return

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(result)

            supabase.table('giveaways').update({'media_type': media_type, 'media_file_id': result}).eq('id', giveaway_id).execute()
            await _show_edit_menu_active(message.from_user.id, giveaway_id, data['last_message_id'])
            giveaway_data = supabase.table('giveaways').select('*').eq('id', giveaway_id).execute().data[0]
            await update_published_posts_active(giveaway_id, giveaway_data)
            await state.clear()

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_active_post:{giveaway_id}")
            await send_message_with_image(
                bot,
                message.chat.id,
                "❌ Ой! Не удалось загрузить медиа 😔",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

    @dp.callback_query(lambda c: c.data.startswith('delete_media_active:'))
    async def process_delete_media_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            supabase.table('giveaways').update({'media_type': None, 'media_file_id': None}).eq('id', giveaway_id).execute()
            giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            giveaway_data = giveaway_response.data
            await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)
            await update_published_posts_active(giveaway_id, giveaway_data)
            await bot.answer_callback_query(callback_query.id, text="✅ Медиа удалено! ✨")
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="❌ Не удалось удалить медиа 😔")
