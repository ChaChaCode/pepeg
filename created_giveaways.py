from typing import List, Dict, Any
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardMarkup, InlineKeyboardButton
from database import cursor, conn
import aiogram.exceptions
import json
import asyncio
import math
import requests
from aiogram.types import CallbackQuery
import logging
from utils import truncate_text, count_length_with_custom_emoji, FORMATTING_GUIDE, FORMATTING_GUIDE2, DEFAULT_IMAGE_URL, \
    MAX_MEDIA_SIZE_MB, MAX_NAME_LENGTH, MAX_DESCRIPTION_LENGTH, MAX_WINNERS, get_file_url, s3_client, YANDEX_BUCKET_NAME
from utils import send_message_auto


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_selected_communities = {}
paid_users: Dict[int, str] = {}

async def build_community_selection_ui(user_id: int, giveaway_id: str, bot: Bot, bot_info, notification: str = None) -> tuple[str, InlineKeyboardMarkup, str]:
    bound_communities = await get_bound_communities(bot, user_id, cursor)
    giveaway_communities = await get_giveaway_communities(giveaway_id)

    user_selected_communities[user_id] = {
        'giveaway_id': giveaway_id,
        'communities': set((comm['community_id'], comm['community_username']) for comm in giveaway_communities)
    }

    try:
        cursor.execute(
            """
            INSERT INTO user_binding_state (user_id, giveaway_id, admin_notification)
            VALUES (%s, %s, %s)
            ON CONFLICT (user_id)
            DO UPDATE SET giveaway_id = EXCLUDED.giveaway_id,
                          admin_notification = EXCLUDED.admin_notification,
                          updated_at = CURRENT_TIMESTAMP
            """,
            (user_id, giveaway_id, notification)
        )
        conn.commit()
    except Exception as e:
        logging.error(f"Ошибка сохранения состояния привязки для {user_id}: {str(e)}")

    keyboard = InlineKeyboardBuilder()
    if bound_communities:
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

        keyboard.button(text="💾 Сохранить выбор", callback_data=f"confirm_community_selection:{giveaway_id}")
    keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
    keyboard.adjust(1)

    if bound_communities:
        message_text = (
            "<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Выберите сообщества для привязки и нажмите 'Сохранить выбор'\n\n"
            "<blockquote expandable>Чтобы привязать паблик/канал/группу:\n"
            f"1. Добавьте бота <code>@{bot_info.username}</code> в администраторы.\n"
            "2. Вы должны быть администратором.\n"
            "3. Не меняйте права бота при добавлении.\n"
            "Бот автоматически обнаружит добавление.\n\n"
            "<b>Новое</b> Если другой пользователь хочет провести розыгрыш с вашим каналом:\n"
            "- Назначьте бота администратором.\n"
            "- Добавьте этого пользователя администратором с минимальными правами.\n"
            "Кнопки автоматически обновляются после успешной привязки</blockquote>"
        )
    else:
        message_text = (
            "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> У вас нет доступных сообществ для привязки.\n\n"
            "<blockquote expandable>Чтобы привязать паблик/канал/группу:\n"
            f"1. Добавьте бота <code>@{bot_info.username}</code> в администраторы.\n"
            "2. Вы должны быть администратором.\n"
            "3. Не меняйте права бота при добавлении.\n"
            "Бот автоматически обнаружит добавление.\n\n"
            "<b>Новое</b> Если другой пользователь хочет провести розыгрыш с вашим каналом:\n"
            "- Назначьте бота администратором.\n"
            "- Добавьте этого пользователя администратором с минимальными правами.\n"
            "Кнопки автоматически обновляются после успешной привязки</blockquote>"
        )

    if notification:
        message_text = f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> {notification}\n\n{message_text}"

    image_url = DEFAULT_IMAGE_URL
    return message_text, keyboard.as_markup(), image_url

async def get_bound_communities(bot, user_id: int, cursor) -> List[Dict[str, Any]]:
    """Получает список сообществ, где пользователь является администратором.

    Args:
        bot: Экземпляр бота Aiogram для выполнения запросов к Telegram API.
        user_id (int): ID пользователя, для которого получаем сообщества.
        cursor: Курсор базы данных для выполнения SQL-запросов.

    Returns:
        List[Dict[str, Any]]: Список словарей с информацией о сообществах.
    """
    try:
        cursor.execute("SELECT * FROM bound_communities WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        communities = [dict(zip(columns, row)) for row in rows]

        additional_communities = []
        cursor.execute("SELECT DISTINCT community_id, community_username, community_name, community_type, media_file_ava FROM bound_communities")
        all_communities = cursor.fetchall()
        for community in all_communities:
            community_id = community[0]
            try:
                chat_member = await bot.get_chat_member(chat_id=community_id, user_id=user_id)
                if chat_member.status in ['administrator', 'creator']:
                    bot_member = await bot.get_chat_member(chat_id=community_id, user_id=(await bot.get_me()).id)
                    if bot_member.status == 'administrator':
                        community_dict = {
                            'community_id': community_id,
                            'community_username': community[1],
                            'community_name': community[2],
                            'community_type': community[3],
                            'user_id': user_id,
                            'media_file_ava': community[4]
                        }
                        if not any(c['community_id'] == community_id for c in communities):
                            additional_communities.append(community_dict)
            except aiogram.exceptions.TelegramBadRequest as e:
                logging.warning(f"Не удалось проверить статус в сообществе {community_id}: {str(e)}")
                continue
            except Exception as e:
                logging.error(f"Ошибка при проверке статуса в сообществе {community_id}: {str(e)}")
                continue

        communities.extend(additional_communities)
        return communities
    except Exception as e:
        logging.error(f"Ошибка получения сообществ: {str(e)}")
        return []

class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_media_choice = State()
    waiting_for_media_upload = State()
    waiting_for_end_time = State()
    waiting_for_winner_count = State()
    waiting_for_community_name = State()
    waiting_for_new_end_time = State()
    waiting_for_media_edit = State()
    waiting_for_congrats_message = State()
    waiting_for_common_congrats_message = State()
    waiting_for_edit_name = State()
    waiting_for_edit_description = State()
    waiting_for_edit_winner_count = State()
    creating_giveaway = State()
    binding_communities = State()
    waiting_for_invite_quantity = State()
    waiting_for_edit_button = State()

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > MAX_MEDIA_SIZE_MB:
            return False, f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой Максимум: {MAX_MEDIA_SIZE_MB} МБ 😔"
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
            logger.info(f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> Файл загружен: {unique_filename}")
            return True, public_url
        else:
            logger.error(f"❌ Ошибка загрузки: {response.status_code}")
            raise Exception(f"Не удалось загрузить: {response.status_code}")
    except Exception as e:
        logger.error(f"🚫 Ошибка: {str(e)}")
        return False, f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось загрузить файл: {str(e)}"

async def get_giveaway_communities(giveaway_id):
    try:
        cursor.execute("SELECT * FROM giveaway_communities WHERE giveaway_id = %s", (giveaway_id,))
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in rows]
    except Exception as e:
        logger.error(f"🚫 Ошибка получения сообществ розыгрыша: {str(e)}")
        return []

def truncate_name(name, max_length=20):
    return name if len(name) <= max_length else name[:max_length - 3] + "..."

def register_created_giveaways_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    from main import cursor
    """Регистрирует обработчики для управления розыгрышами 🎁"""

    @dp.callback_query(lambda c: c.data == 'created_giveaways' or c.data.startswith('created_giveaways_page:'))
    async def process_created_giveaways(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if ':' in callback_query.data else 1
        try:
            cursor.execute(
                """
                SELECT COUNT(*) FROM giveaways 
                WHERE user_id = %s AND is_completed = false
                """,
                (user_id,)
            )
            total_giveaways = cursor.fetchone()[0]
            if total_giveaways == 0:
                await bot.answer_callback_query(callback_query.id,
                                                text="📭 Пока нет незавершенных розыгрышей? Создайте свой первый 🚀")
                await send_message_auto(
                    bot,
                    user_id,
                    "📭 Пока нет незавершенных розыгрышей? Создайте свой первый 🚀",
                    reply_markup=None,
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
                )
                return
            total_pages = max(1, math.ceil(total_giveaways / ITEMS_PER_PAGE))
            offset = (current_page - 1) * ITEMS_PER_PAGE
            cursor.execute(
                """
                SELECT * FROM giveaways 
                WHERE user_id = %s AND is_completed = false
                ORDER BY CASE is_active 
                    WHEN 'true' THEN 0 
                    WHEN 'waiting' THEN 1 
                    WHEN 'false' THEN 2 
                    END
                LIMIT %s OFFSET %s
                """,
                (user_id, ITEMS_PER_PAGE, offset)
            )
            columns = [desc[0] for desc in cursor.description]
            current_giveaways = [dict(zip(columns, row)) for row in cursor.fetchall()]
            keyboard = InlineKeyboardBuilder()
            for giveaway in current_giveaways:
                name = giveaway['name'] if giveaway['name'] else "Без названия"
                clean_name = truncate_text(name, 61)
                status_indicator = "✅ " if giveaway['is_active'] == 'true' else ""
                callback_data = (f"view_active_giveaway:{giveaway['id']}" if giveaway['is_active'] == 'true'
                                 else f"view_created_giveaway:{giveaway['id']}")
                keyboard.row(InlineKeyboardButton(
                    text=f"{status_indicator}{clean_name}",
                    callback_data=callback_data
                ))
            nav_buttons = []
            if total_pages > 1:
                prev_page = current_page - 1 if current_page > 1 else total_pages
                nav_buttons.append(
                    InlineKeyboardButton(text="◀️", callback_data=f"created_giveaways_page:{prev_page}"))
                nav_buttons.append(InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="ignore"))
                next_page = current_page + 1 if current_page < total_pages else 1
                nav_buttons.append(
                    InlineKeyboardButton(text="▶️", callback_data=f"created_giveaways_page:{next_page}"))
            if nav_buttons:
                keyboard.row(*nav_buttons)
            keyboard.row(InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu"))
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM giveaways WHERE user_id = %s AND is_active = 'true' AND is_completed = false)",
                (user_id,)
            )
            has_active = cursor.fetchone()[0]
            message_text = (
                f"<tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Выберите розыгрыш для просмотра\n\n"
                f"Всего розыгрышей: {total_giveaways}\n\n"
                "✅ - Активный розыгрыш" if has_active else
                f"<tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Выберите розыгрыш для просмотра\n\n"
                f"Всего розыгрышей: {total_giveaways}"
            )
            message_text = truncate_text(message_text, 4000)
            await bot.answer_callback_query(callback_query.id)
            await send_message_auto(
                bot,
                user_id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Упс Что-то пошло не так 😔")
            await send_message_auto(
                bot,
                user_id,
                "⚠️ Упс Что-то пошло не так. Попробуйте снова 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('view_created_giveaway:'))
    async def process_view_created_giveaway(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    "🔍 Розыгрыш не найден 😕",
                    reply_markup=None,
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL,
                    previous_message_type='photo'  # Добавляем по умолчанию
                )
                return

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

            description = giveaway['description'] or "Описание отсутствует"
            winner_count = str(giveaway['winner_count'])
            end_time = giveaway['end_time'].strftime('%d.%m.%Y %H:%M (МСК)') if giveaway['end_time'] else "Не указано"
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            giveaway_info = f"{formatted_description}"

            image_url = None
            media_type = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                media_type = giveaway['media_type']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])
            else:
                image_url = DEFAULT_IMAGE_URL

            # Получаем данные состояния
            data = await state.get_data()
            previous_message_type = data.get('previous_message_type', 'photo')  # По умолчанию 'photo'
            current_message_type = media_type or (
                'image' if count_length_with_custom_emoji(giveaway_info) > 800 else 'photo')

            await bot.answer_callback_query(callback_query.id)

            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type and callback_query.message.message_id:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(f"Удалено старое сообщение {callback_query.message.message_id} перед отправкой нового")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

            # Отправляем сообщение с учетом типа медиа
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type,
                previous_message_type=previous_message_type
            )

            # Обновляем состояние
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка загрузки розыгрыша 😔")
            data = await state.get_data()
            previous_message_type = data.get('previous_message_type', 'photo')
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                "⚠️ Упс Что-то пошло не так. Попробуйте снова 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )

    @dp.callback_query(lambda c: c.data.startswith('add_invite_task:'))
    async def process_add_invite_task(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT invite, quantity_invite FROM giveaways WHERE id = %s", (giveaway_id,))
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, cursor.fetchone()))

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        keyboard = InlineKeyboardBuilder()
        if giveaway['invite']:
            keyboard.button(text="✏️ Изменить количество", callback_data=f"change_invite_quantity:{giveaway_id}")
            keyboard.button(text="🗑️ Убрать задание", callback_data=f"remove_invite_task:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)
            message_text = f"<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Задание 'Пригласить друга' уже активно\n\nНужно пригласить {giveaway['quantity_invite']} друга(зей)"
        else:
            keyboard.button(text="✅ Да", callback_data=f"confirm_invite_task:{giveaway_id}")
            keyboard.button(text="❌ Нет", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(2)
            message_text = "<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Хотите добавить задание 'Пригласить друга'?"

        current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

        await bot.answer_callback_query(callback_query.id)
        await state.clear()

        # Пытаемся удалить старое сообщение, если тип изменился
        if previous_message_type != current_message_type:
            try:
                await bot.delete_message(
                    chat_id=callback_query.from_user.id,
                    message_id=callback_query.message.message_id
                )
                logger.info(f"Удалено старое сообщение {callback_query.message.message_id} в process_add_invite_task")
            except Exception as e:
                logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

        sent_message = await send_message_auto(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL,
            previous_message_type=previous_message_type
        )

        if sent_message:
            await state.update_data(
                last_message_id=sent_message.message_id,
                previous_message_type=current_message_type
            )

    @dp.callback_query(lambda c: c.data.startswith('confirm_invite_task:'))
    async def process_confirm_invite_task(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_invite_quantity)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"add_invite_task:{giveaway_id}")

        await bot.answer_callback_query(callback_query.id)
        await send_message_auto(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5271604874419647061'>🔗</tg-emoji> Сколько друзей должен пригласить участник?\nВведите число",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

    @dp.callback_query(lambda c: c.data.startswith('change_invite_quantity:'))
    async def process_change_invite_quantity(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_invite_quantity)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"add_invite_task:{giveaway_id}")

        await bot.answer_callback_query(callback_query.id)
        await send_message_auto(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Введите новое количество друзей для приглашения",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

    @dp.callback_query(lambda c: c.data.startswith('remove_invite_task:'))
    async def process_remove_invite_task(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute(
                "UPDATE giveaways SET invite = %s, quantity_invite = %s WHERE id = %s",
                (False, 0, giveaway_id)
            )
            conn.commit()
            await bot.answer_callback_query(callback_query.id, text="Задание убрано ✅")
            new_callback_query = types.CallbackQuery(
                id=callback_query.id,
                from_user=callback_query.from_user,
                chat_instance=callback_query.chat_instance,
                message=callback_query.message,
                data=f"view_created_giveaway:{giveaway_id}"
            )
            await process_view_created_giveaway(new_callback_query, state)
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id,
                                            text="Упс Не удалось убрать задание 😔")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "⚠️ Упс Не удалось убрать задание 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.message(GiveawayStates.waiting_for_invite_quantity)
    async def process_invite_quantity(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']

        try:
            quantity = int(message.text)
            if quantity <= 0:
                raise ValueError("Количество должно быть положительным")

            cursor.execute(
                "UPDATE giveaways SET invite = %s, quantity_invite = %s WHERE id = %s",
                (True, quantity, giveaway_id)
            )
            conn.commit()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Изменить количество", callback_data=f"change_invite_quantity:{giveaway_id}")
            keyboard.button(text="🗑️ Убрать задание", callback_data=f"remove_invite_task:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_auto(
                bot,
                message.from_user.id,
                f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> Задание добавлено\n\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Пригласить {quantity} друга(зей) для участия",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            await state.clear()

        except ValueError:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"add_invite_task:{giveaway_id}")
            await send_message_auto(
                bot,
                message.from_user.id,
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число Например, 5",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_post:'))
    async def process_edit_post(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    async def _show_edit_menu(user_id: int, giveaway_id: str, message_id: int = None, state: FSMContext = None):
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, cursor.fetchone()))
        if not giveaway:
            data = await state.get_data() if state else {}
            previous_message_type = data.get('previous_message_type', 'photo')
            await send_message_auto(
                bot,
                user_id,
                "🔍 Розыгрыш не найден 😕",
                reply_markup=None,
                message_id=message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                previous_message_type=previous_message_type
            )
            return

        if state:
            await state.update_data(user_messages=[], limit_exceeded=False)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_description:{giveaway_id}")
        keyboard.button(text="🏆 Победители", callback_data=f"edit_winner_count:{giveaway_id}")
        keyboard.button(text="⏰ Дата", callback_data=f"change_end_date:{giveaway_id}")
        keyboard.button(text="🖼️ Медиа", callback_data=f"manage_media:{giveaway_id}")
        keyboard.button(text="🔗 Кнопка", callback_data=f"edit_button:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(2, 2, 2, 1)

        media_display = "Медиа: отсутствует"
        if giveaway['media_type']:
            if giveaway['media_type'] == 'photo':
                media_display = "Медиа: фото"
            elif giveaway['media_type'] == 'gif':
                media_display = "Медиа: gif"
            elif giveaway['media_type'] == 'video':
                media_display = "Медиа: видео"

        button_display = giveaway.get('button', '🎉 Участвовать')

        dop_info = (
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> <b>Победителей:</b> {giveaway['winner_count']}\n"
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> <b>{media_display}</b>\n"
            f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Конец:</b> {(giveaway['end_time']).strftime('%d.%m.%Y %H:%M')} (МСК)\n"
            f"<tg-emoji emoji-id='5271604874419647061'>🔗</tg-emoji> <b>Кнопка:</b> {button_display}"
        )

        giveaway_info = f"""<b>Название:</b> {giveaway['name']}
    <b>Описание:\n</b> {giveaway['description']}

    {dop_info}

    <tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Что хотите изменить?
    """

        image_url = None
        media_type = None
        if giveaway['media_type'] and giveaway['media_file_id']:
            image_url = giveaway['media_file_id']
            media_type = giveaway['media_type']
            if not image_url.startswith('http'):
                image_url = await get_file_url(bot, giveaway['media_file_id'])
        else:
            image_url = DEFAULT_IMAGE_URL

        # Получаем данные состояния
        data = await state.get_data() if state else {}
        previous_message_type = data.get('previous_message_type', 'photo')
        current_message_type = media_type or (
            'image' if count_length_with_custom_emoji(giveaway_info) > 800 else 'photo')

        try:
            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type and message_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=message_id)
                    logger.info(f"Удалено старое сообщение {message_id} перед отправкой нового в _show_edit_menu")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                user_id,
                giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_type != current_message_type else message_id,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type,
                previous_message_type=previous_message_type
            )

            if state and sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                user_id,
                "<tg-emoji emoji-id='5422649047334794716'>😵</tg-emoji> Упс Ошибка при загрузке меню. Попробуйте снова 😔",
                reply_markup=None,
                message_id=message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                previous_message_type=previous_message_type
            )
            if state and sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )

    @dp.callback_query(lambda c: c.data.startswith('edit_button:'))
    async def process_edit_button(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT button FROM giveaways WHERE id = %s", (giveaway_id,))
        current_button = cursor.fetchone()[0] or "🎉 Участвовать"

        await state.update_data(
            giveaway_id=giveaway_id,
            last_message_id=callback_query.message.message_id,
            user_messages=[],
            current_message_parts=[],
            limit_exceeded=False,
            last_message_time=None
        )
        await state.set_state(GiveawayStates.waiting_for_edit_button)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
        keyboard.adjust(1)

        message_text = (
            f"<tg-emoji emoji-id='5271604874419647061'>🔗</tg-emoji> Текущий текст кнопки: <b>{current_button}</b>\n\n"
            f"Отправьте новый текст для кнопки (до 50 символов)"
        )

        try:
            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_button2.jpg'
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url
            )
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_button2.jpg'
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                image_url=image_url
            )
            await state.update_data(last_message_id=sent_message.message_id)

        await bot.answer_callback_query(callback_query.id)

    @dp.message(GiveawayStates.waiting_for_edit_button)
    async def process_new_button_text(message: types.Message, state: FSMContext):
        data = await state.get_data()
        await process_long_message(
            message,
            state,
            giveaway_id=data.get('giveaway_id'),
            last_message_id=data.get('last_message_id'),
            field='button',
            max_length=50,
            formatting_guide=FORMATTING_GUIDE,
            image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_button2.jpg'
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_name:'))
    async def process_edit_name(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT name FROM giveaways WHERE id = %s", (giveaway_id,))
        current_name = cursor.fetchone()[0]

        # Получаем данные из состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')  # Значение по умолчанию 'photo'

        await state.update_data(
            giveaway_id=giveaway_id,
            last_message_id=callback_query.message.message_id,
            user_messages=[],
            current_message_parts=[],
            limit_exceeded=False,
            last_message_time=None
        )
        await state.set_state(GiveawayStates.waiting_for_edit_name)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
        keyboard.adjust(1)

        message_text = (
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Текущее название: <b>{current_name}</b>\n\n"
            f"Отправьте новое название (до {MAX_NAME_LENGTH} символов)"
            if current_name else
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Отправьте название розыгрыша (до {MAX_NAME_LENGTH} символов)"
        )

        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
        current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

        try:
            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(f"Удалено старое сообщение {callback_query.message.message_id} в process_edit_name")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None,  # Отправляем новое сообщение
                parse_mode='HTML',
                image_url=image_url,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('edit_description:'))
    async def process_edit_description(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT description FROM giveaways WHERE id = %s", (giveaway_id,))
        current_description = cursor.fetchone()[0]

        # Получаем данные из состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')  # Значение по умолчанию 'photo'

        await state.update_data(
            giveaway_id=giveaway_id,
            last_message_id=callback_query.message.message_id,
            user_messages=[],
            current_message_parts=[],
            limit_exceeded=False,
            last_message_time=None
        )
        await state.set_state(GiveawayStates.waiting_for_edit_description)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
        keyboard.adjust(1)

        message_text = (
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Текущее описание:\n{current_description}\n\n"
            f"Отправьте новое описание (до {MAX_DESCRIPTION_LENGTH} символов)\n{FORMATTING_GUIDE2}"
            if current_description else
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Отправьте описание розыгрыша (до {MAX_DESCRIPTION_LENGTH} символов)\n{FORMATTING_GUIDE2}"
        )

        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

        try:
            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_edit_description")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None,  # Отправляем новое сообщение
                parse_mode='HTML',
                image_url=image_url,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count:'))
    async def process_edit_winner_count(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_edit_winner_count)
        await bot.answer_callback_query(callback_query.id)

        cursor.execute("SELECT winner_count FROM giveaways WHERE id = %s", (giveaway_id,))
        current_winner_count = cursor.fetchone()[0]

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        await send_message_auto(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Текущее количество победителей: <b>{current_winner_count}</b>\n\n"
            f"Если хотите изменить, укажите новое число (максимум {MAX_WINNERS}):",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

    async def process_long_message(
            message: types.Message,
            state: FSMContext,
            giveaway_id: str,
            last_message_id: int,
            field: str,
            max_length: int,
            formatting_guide: str,
            image_url: str
    ):
        field_translations = {
            'name': 'Название',
            'description': 'Описание',
            'button': 'Кнопка'
        }

        data = await state.get_data()
        user_messages = data.get('user_messages', [])
        limit_exceeded = data.get('limit_exceeded', False)
        current_message_parts = data.get('current_message_parts', [])
        last_message_time = data.get('last_message_time')
        previous_message_type = data.get('previous_message_type', 'photo')  # Значение по умолчанию 'photo'
        new_text = message.html_text if message.text else ""

        current_time = datetime.now().timestamp()

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
        keyboard.adjust(1)

        if last_message_time is not None and (current_time - last_message_time) <= 2:
            current_message_parts.append(new_text)
            await state.update_data(
                current_message_parts=current_message_parts,
                last_message_time=current_time,
                user_messages=user_messages,
                limit_exceeded=limit_exceeded
            )
            return

        if current_message_parts:
            combined_message = "".join(current_message_parts)
            if combined_message:
                user_messages.append(combined_message)
            current_message_parts = [new_text]
        else:
            current_message_parts = [new_text]

        await state.update_data(
            current_message_parts=current_message_parts,
            last_message_time=current_time,
            user_messages=user_messages,
            limit_exceeded=limit_exceeded
        )

        combined_current_message = "".join(current_message_parts)
        current_length = count_length_with_custom_emoji(combined_current_message)
        current_message_type = 'photo' if current_length <= 800 else 'image'

        if limit_exceeded:
            if 0 < current_length <= max_length:
                try:
                    cursor.execute(
                        f"UPDATE giveaways SET {field} = %s WHERE id = %s",
                        (combined_current_message, giveaway_id)
                    )
                    conn.commit()

                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

                    # Удаляем старое сообщение бота, если тип изменился
                    if previous_message_type != current_message_type and last_message_id:
                        try:
                            await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                            logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message (успех)")
                        except Exception as e:
                            logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

                    await state.update_data(
                        user_messages=[],
                        current_message_parts=[],
                        limit_exceeded=False,
                        last_message_time=None
                    )

                    await _show_edit_menu(message.from_user.id, giveaway_id, last_message_id, state)
                    await state.clear()
                except Exception as e:
                    logger.error(f"🚫 Ошибка при обновлении {field}: {str(e)}")
                    conn.rollback()
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                    except Exception as e:
                        logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")
                    if previous_message_type != 'photo' and last_message_id:
                        try:
                            await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                            logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message (ошибка)")
                        except Exception as e:
                            logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
                    sent_message = await send_message_auto(
                        bot,
                        message.chat.id,
                        f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ой Не удалось обновить {field_translations[field]} 😔",
                        reply_markup=keyboard.as_markup(),
                        message_id=None,
                        parse_mode='HTML',
                        image_url=image_url,
                        previous_message_type=previous_message_type
                    )
                    if sent_message:
                        await state.update_data(
                            last_message_id=sent_message.message_id,
                            previous_message_type='photo'
                        )
            else:
                if last_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                        logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message (лимит)")
                    except Exception as e:
                        logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

                sent_message = await send_message_auto(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {field_translations[field]} должно быть от 1 до {max_length} символов. Текущее: {current_length}\n{formatting_guide}",
                    reply_markup=keyboard.as_markup(),
                    message_id=None,
                    parse_mode='HTML',
                    image_url=image_url,
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type=current_message_type,
                        limit_exceeded=True,
                        last_message_time=current_time
                    )
            return

        total_length = sum(count_length_with_custom_emoji(msg) for msg in user_messages if msg)
        total_length += current_length

        if total_length > max_length or not combined_current_message:
            if last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message (лимит)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

            await state.update_data(
                user_messages=user_messages,
                current_message_parts=current_message_parts,
                limit_exceeded=True,
                last_message_id=None,
                last_message_time=current_time
            )

            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {field_translations[field]} превышает лимит ({max_length} символов).\nОбщая длина: {total_length}\nОтправьте новое {field_translations[field].lower()}.\n{formatting_guide}",
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
            return

        try:
            cursor.execute(
                f"UPDATE giveaways SET {field} = %s WHERE id = %s",
                (combined_current_message, giveaway_id)
            )
            conn.commit()

            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

            # Удаляем старое сообщение бота, если тип изменился
            if previous_message_type != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message (успех)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

            await state.update_data(
                user_messages=[],
                current_message_parts=[],
                last_message_time=None
            )

            await _show_edit_menu(message.from_user.id, giveaway_id, last_message_id, state)
            await state.clear()
        except Exception as e:
            logger.error(f"🚫 Ошибка при обновлении {field}: {str(e)}")
            conn.rollback()
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            except Exception as e:
                logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")
            if previous_message_type != 'photo' and last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message (ошибка)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ой Не удалось обновить {field_translations[field]} 😔",
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )

    @dp.message(GiveawayStates.waiting_for_edit_name)
    async def process_new_name(message: types.Message, state: FSMContext):
        data = await state.get_data()
        await process_long_message(
            message,
            state,
            giveaway_id=data.get('giveaway_id'),
            last_message_id=data.get('last_message_id'),
            field='name',
            max_length=MAX_NAME_LENGTH,
            formatting_guide=FORMATTING_GUIDE,
            image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
        )

    @dp.message(GiveawayStates.waiting_for_edit_description)
    async def process_new_description(message: types.Message, state: FSMContext):
        data = await state.get_data()
        await process_long_message(
            message,
            state,
            giveaway_id=data.get('giveaway_id'),
            last_message_id=data.get('last_message_id'),
            field='description',
            max_length=MAX_DESCRIPTION_LENGTH,
            formatting_guide=FORMATTING_GUIDE2,
            image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        )

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
                keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
                await send_message_auto(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Слишком много победителей Максимум {MAX_WINNERS}",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
                )
                return

            data = await state.get_data()
            giveaway_id = data['giveaway_id']
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Обновляем количество победителей...",
                message_id=data.get('last_message_id'),
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

            cursor.execute("SELECT winner_count FROM giveaways WHERE id = %s", (giveaway_id,))
            current_winner_count = cursor.fetchone()[0]

            cursor.execute(
                "UPDATE giveaways SET winner_count = %s WHERE id = %s",
                (new_winner_count, giveaway_id)
            )

            if new_winner_count > current_winner_count:
                for place in range(current_winner_count + 1, new_winner_count + 1):
                    cursor.execute(
                        """
                        INSERT INTO congratulations (giveaway_id, place, message)
                        VALUES (%s, %s, %s)
                        """,
                        (giveaway_id, place, f"🎉 Поздравляем Вы заняли {place} место")
                    )
            elif new_winner_count < current_winner_count:
                cursor.execute(
                    "DELETE FROM congratulations WHERE giveaway_id = %s AND place >= %s",
                    (giveaway_id, new_winner_count + 1)
                )

            conn.commit()
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])

        except ValueError:
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
            await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число Например, 3",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{data['giveaway_id']}")
            await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ой Не удалось обновить победителей 😔",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('manage_media:'))
    async def process_manage_media(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, cursor.fetchone()))

        media_file_id = giveaway.get('media_file_id')
        media_type = giveaway.get('media_type')
        has_media = media_file_id and media_type

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_media_edit)

        keyboard = InlineKeyboardBuilder()
        if has_media:
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_media:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
        keyboard.adjust(1)

        message_text = (
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Текущее медиа: {'Фото' if media_type == 'photo' else 'GIF' if media_type == 'gif' else 'Видео'}.\n\nОтправьте новое или удалите текущее."
            if has_media else
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)"
        )

        image_url = None
        if has_media:
            image_url = media_file_id
            if not image_url.startswith('http'):
                image_url = await get_file_url(bot, media_file_id)
        else:
            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_media2.jpg'

        current_message_type = media_type if has_media else 'photo'

        try:
            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(f"Удалено старое сообщение {callback_query.message.message_id} в process_manage_media")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type if has_media else None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"Ошибка редактирования медиа: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type if has_media else None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('add_media:') or c.data.startswith('change_media:'))
    async def process_add_or_change_media(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id)
        await state.set_state(GiveawayStates.waiting_for_media_edit)

        data = await state.get_data()
        last_message_id = data.get('last_bot_message_id') or callback_query.message.message_id

        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"manage_media:{giveaway_id}")]])

        message = await send_message_auto(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)",
            reply_markup=keyboard,
            message_id=last_message_id,
            parse_mode='HTML',
            image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_media2.jpg'
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
        data = await state.get_data()
        giveaway_id = data.get('giveaway_id')
        last_message_id = data.get('last_message_id', message.message_id)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
        keyboard.adjust(1)

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
                await send_message_auto(
                    bot,
                    message.chat.id,
                    "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Отправьте фото, GIF или видео",
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_media2.jpg'
                )
                return

            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)

            file_size_mb = file.file_size / (1024 * 1024)
            if file_size_mb > MAX_MEDIA_SIZE_MB:
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                await send_message_auto(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой Максимум {MAX_MEDIA_SIZE_MB} МБ",
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_media2.jpg'
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
            await _show_edit_menu(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ой Не удалось загрузить медиа 😔",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_media2.jpg'
            )
            await state.clear()

    @dp.callback_query(lambda c: c.data.startswith('delete_media:'))
    async def process_delete_media(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute(
                "UPDATE giveaways SET media_type = NULL, media_file_id = NULL WHERE id = %s",
                (giveaway_id,)
            )
            conn.commit()
            data = await state.get_data()
            last_message_id = data.get('last_message_id', callback_query.message.message_id)

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_post:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до 10 МБ)",
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi_media2.jpg'
            )
            await bot.answer_callback_query(callback_query.id, text="Медиа удалено")
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id, text="Не удалось удалить медиа")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "⚠️ Не удалось удалить медиа 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('delete_giveaway:'))
    async def process_delete_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✅ Да", callback_data=f"confirm_delete_giveaway:{giveaway_id}")
        keyboard.button(text="❌ Нет", callback_data=f"cancel_delete_giveaway:{giveaway_id}")
        keyboard.adjust(2)
        await send_message_auto(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> Вы уверены, что хотите удалить розыгрыш?",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_delete_giveaway:'))
    async def process_confirm_delete_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute("DELETE FROM giveaway_communities WHERE giveaway_id = %s", (giveaway_id,))
            cursor.execute("DELETE FROM participations WHERE giveaway_id = %s", (giveaway_id,))
            cursor.execute("DELETE FROM congratulations WHERE giveaway_id = %s", (giveaway_id,))
            cursor.execute("DELETE FROM giveaways WHERE id = %s", (giveaway_id,))
            conn.commit()

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> Розыгрыш успешно удалён",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5422649047334794716'>😵</tg-emoji> Упс Не удалось удалить розыгрыш 😔",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('cancel_delete_giveaway:'))
    async def process_cancel_delete_giveaway(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.clear()
        new_callback_query = types.CallbackQuery(
            id=callback_query.id,
            from_user=callback_query.from_user,
            chat_instance=callback_query.chat_instance,
            message=callback_query.message,
            data=f"view_created_giveaway:{giveaway_id}"
        )
        await process_view_created_giveaway(new_callback_query, state)

    @dp.callback_query(lambda c: c.data.startswith('change_end_date:'))
    async def process_change_end_date(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(GiveawayStates.waiting_for_new_end_time)
        await callback_query.answer()

        cursor.execute("SELECT end_time FROM giveaways WHERE id = %s", (giveaway_id,))
        current_end_time = cursor.fetchone()[0]
        formatted_end_time = current_end_time.strftime('%d.%m.%Y %H:%M (МСК)')

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Текущее время окончания: <b>{formatted_end_time}</b>

Если хотите изменить, укажите новую дату завершения в формате ДД.ММ.ГГГГ ЧЧ:ММ по МСК

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
"""
        await send_message_auto(
            bot,
            callback_query.from_user.id,
            html_message,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

    @dp.callback_query(lambda c: c.data.startswith('preview_giveaway:'))
    async def process_preview_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    "🔍 Розыгрыш не найден 😕",
                    reply_markup=None,
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=None
                )
                return

            cursor.execute(
                "UPDATE giveaways SET is_active = %s WHERE id = %s",
                ('waiting', giveaway_id)
            )
            conn.commit()
            logger.info(f"Состояние is_active для розыгрыша {giveaway_id} изменено на 'waiting'")

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = giveaway['end_time'].strftime('%d.%m.%Y %H:%M (МСК)') if giveaway['end_time'] else "Не указано"
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            post_text = f"""{formatted_description}"""

            keyboard = InlineKeyboardBuilder()
            button_text = giveaway.get('button', '🎉 Участвовать (0)')
            keyboard.button(
                text=button_text,
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )
            keyboard.button(
                text="◀️ Назад",
                callback_data=f"view_created_giveaway:{giveaway_id}"
            )
            keyboard.adjust(1)

            image_url = None
            media_type = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                media_type = giveaway['media_type']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])

            await bot.answer_callback_query(callback_query.id)
            await send_message_auto(
                bot,
                callback_query.message.chat.id,
                post_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type
            )

        except Exception as e:
            logger.error(f"🚫 Ошибка предпросмотра: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id,
                                            text="Ошибка при предпросмотре 😔")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "⚠️ Ошибка при предпросмотре 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=None,
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
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Обновляем дату завершения...",
                message_id=data.get('last_message_id'),
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

            cursor.execute(
                "UPDATE giveaways SET end_time = %s WHERE id = %s",
                (new_end_time_tz, giveaway_id)
            )
            conn.commit()
            await state.clear()
            await _show_edit_menu(message.from_user.id, giveaway_id, data['last_message_id'])
        except ValueError:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Неправильный формат даты\nИспользуйте ДД.ММ.ГГГГ ЧЧ:ММ

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
"""
            await send_message_auto(
                bot,
                message.chat.id,
                html_message,
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Отмена", callback_data=f"edit_post:{giveaway_id}")
            await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ой Не удалось обновить дату 😔",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    async def get_giveaway_creator(giveaway_id: str) -> int:
        cursor.execute("SELECT user_id FROM giveaways WHERE id = %s", (giveaway_id,))
        result = cursor.fetchone()
        return int(result[0]) if result else -1

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username):
        try:
            cursor.execute("SELECT * FROM bound_communities WHERE community_id = %s", (community_id,))
            community = cursor.fetchone()
            if not community:
                logger.error(f"🚫 Сообщество {community_id} не найдено")
                return False

            columns = [desc[0] for desc in cursor.description]
            community_dict = dict(zip(columns, community))
            actual_username = community_username if community_username != 'id' else (
                community_dict.get('community_username') or community_dict.get('community_name'))

            cursor.execute(
                """
                INSERT INTO giveaway_communities (giveaway_id, community_id, community_username, community_type, user_id, community_name)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (giveaway_id, community_id, actual_username, community_dict['community_type'], community_dict['user_id'], community_dict['community_name'])
            )
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            return False

    async def unbind_community_from_giveaway(giveaway_id, community_id):
        cursor.execute(
            "DELETE FROM giveaway_communities WHERE giveaway_id = %s AND community_id = %s",
            (giveaway_id, community_id)
        )
        conn.commit()

    @dp.callback_query(lambda c: c.data == 'bind_communities:' or c.data.startswith('bind_communities:'))
    async def process_bind_communities(callback_query: CallbackQuery, state: FSMContext):
        if callback_query.data == 'bind_communities:':
            await bot.answer_callback_query(callback_query.id, text="Неверный формат данных 😔")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "⚠️ Неверный формат данных 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            return

        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        message_id = callback_query.message.message_id

        try:
            cursor.execute(
                """
                INSERT INTO user_binding_state (user_id, giveaway_id, message_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id)
                DO UPDATE SET giveaway_id = EXCLUDED.giveaway_id,
                              message_id = EXCLUDED.message_id,
                              updated_at = CURRENT_TIMESTAMP
                """,
                (user_id, giveaway_id, message_id)
            )
            conn.commit()
        except Exception as e:
            logging.error(f"Ошибка сохранения состояния привязки для {user_id}: {str(e)}")

        await state.update_data(giveaway_id=giveaway_id, message_id=message_id)
        await bot.answer_callback_query(callback_query.id)

        loading_message = await send_message_auto(
            bot,
            user_id,
            "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Загружаем ваши каналы и паблики",
            reply_markup=None,
            message_id=message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

        try:
            message_text, keyboard, _ = await build_community_selection_ui(
                user_id, giveaway_id, bot, await bot.get_me()
            )

            await send_message_auto(
                bot,
                user_id,
                message_text,
                reply_markup=keyboard,
                message_id=loading_message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

            try:
                cursor.execute(
                    """
                    UPDATE user_binding_state
                    SET message_id = %s
                    WHERE user_id = %s
                    """,
                    (loading_message.message_id, user_id)
                )
                conn.commit()
            except Exception as e:
                logging.error(f"Ошибка обновления message_id для {user_id}: {str(e)}")

        except Exception as e:
            logging.error(f"🚫 Ошибка при загрузке сообществ: {str(e)}")
            await send_message_auto(
                bot,
                user_id,
                "⚠️ Ошибка при загрузке сообществ 😔",
                reply_markup=None,
                message_id=loading_message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

    @dp.callback_query(lambda c: c.data.startswith('toggle_community:'))
    async def process_toggle_community(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        parts = callback_query.data.split(':')
        if len(parts) < 4:
            await bot.answer_callback_query(callback_query.id, text="Неверные данные 😔")
            return

        _, giveaway_id, community_id, community_username = parts

        try:
            cursor.execute("SELECT community_name FROM bound_communities WHERE community_id = %s",
                           (community_id,))
            community = cursor.fetchone()
            community_name = community[0] if community else community_username

            if user_id not in user_selected_communities or user_selected_communities[user_id][
                'giveaway_id'] != giveaway_id:
                user_selected_communities[user_id] = {'giveaway_id': giveaway_id, 'communities': set()}

            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        if '✅' in button.text:
                            new_text = f"{truncate_name(community_name)}"
                            user_selected_communities[user_id]['communities'].discard(
                                (community_id, community_username))
                        else:
                            new_text = f"{truncate_name(community_name)} ✅"
                            user_selected_communities[user_id]['communities'].add((community_id, community_username))
                        new_row.append(InlineKeyboardButton(text=new_text, callback_data=button.callback_data))
                    else:
                        new_row.append(button)
                new_keyboard.append(new_row)

            bot_info = await bot.get_me()

            bound_communities = await get_bound_communities(bot, user_id, cursor)
            if bound_communities:
                message_text = (
                    "<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Выберите сообщества для привязки и нажмите 'Сохранить выбор'\n\n"
                    "<blockquote expandable>Чтобы привязать паблик/канал/группу:\n"
                    f"1. Добавьте бота <code>@{bot_info.username}</code> в администраторы.\n"
                    "2. Вы должны быть администратором.\n"
                    "3. Не меняйте права бота при добавлении.\n"
                    "Бот автоматически обнаружит добавление.\n\n"
                    "<b>Новое</b> Если другой пользователь хочет провести розыгрыш с вашим каналом:\n"
                    "- Назначьте бота администратором.\n"
                    "- Добавьте этого пользователя администратором с минимальными правами.\n"
                    "Кнопки автоматически обновляются после успешной привязки</blockquote>"
                )
            else:
                message_text = (
                    "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> У вас нет доступных сообществ для привязки.\n\n"
                    "<blockquote expandable>Чтобы привязать паблик/канал/группу:\n"
                    f"1. Добавьте бота <code>@{bot_info.username}</code> в администраторы.\n"
                    "2. Вы должны быть администратором.\n"
                    "3. Не меняйте права бота при добавлении.\n"
                    "Бот автоматически обнаружит добавление.\n\n"
                    "<b>Новое</b> Если другой пользователь хочет провести розыгрыш с вашим каналом:\n"
                    "- Назначьте бота администратором.\n"
                    "- Добавьте этого пользователя администратором с минимальными правами.\n"
                    "Кнопки автоматически обновляются после успешной привязки</blockquote>"
                )

            await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard),
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )

            await bot.answer_callback_query(callback_query.id)
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Упс Ошибка при выборе сообщества 😔")

    @dp.callback_query(lambda c: c.data.startswith('activate_giveaway:'))
    async def process_activate_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        try:
            cursor.execute(
                "SELECT community_id, community_username, community_name, media_file_ava FROM giveaway_communities WHERE giveaway_id = %s",
                (giveaway_id,)
            )
            communities = cursor.fetchall()
            communities = [dict(zip(['community_id', 'community_username', 'community_name', 'media_file_ava'], comm))
                           for comm in communities]

            if not communities:
                await bot.answer_callback_query(callback_query.id, text="⚠️ Нет привязанных сообществ для публикации")
                return

            keyboard = InlineKeyboardBuilder()
            for community in communities:
                display_name = truncate_name(community['community_name'])
                callback_data = f"toggle_activate_community:{giveaway_id}:{community['community_id']}:{community['community_username']}"
                if len(callback_data.encode('utf-8')) > 60:
                    callback_data = f"toggle_activate_community:{giveaway_id}:{community['community_id']}:id"
                keyboard.button(text=display_name, callback_data=callback_data)
            keyboard.button(text="Подтвердить выбор", callback_data=f"confirm_activate_selection:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
            keyboard.adjust(1)

            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'

            await bot.answer_callback_query(callback_query.id)
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите сообщества, в которых будет опубликован розыгрыш.\n\nРезультаты также будут размещены только в этих сообществах.",
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                image_url=image_url
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка при загрузке сообществ 😔")

    @dp.callback_query(lambda c: c.data.startswith('toggle_activate_community:'))
    async def process_toggle_activate_community(callback_query: CallbackQuery):
        _, giveaway_id, community_id, community_username = callback_query.data.split(':')
        try:
            cursor.execute("SELECT community_name, media_file_ava FROM bound_communities WHERE community_id = %s",
                           (community_id,))
            community = cursor.fetchone()
            community_name = community[0] if community else community_username
            media_file_ava = community[1] if community else None

            new_keyboard = []
            for row in callback_query.message.reply_markup.inline_keyboard:
                new_row = []
                for button in row:
                    if button.callback_data == callback_query.data:
                        new_text = f"{truncate_name(community_name)}" if '✅' in button.text else f"{truncate_name(community_name)} ✅"
                        new_row.append(InlineKeyboardButton(text=new_text, callback_data=button.callback_data))
                    else:
                        new_row.append(button)
                new_keyboard.append(new_row)

            await bot.edit_message_reply_markup(
                chat_id=callback_query.message.chat.id,
                message_id=callback_query.message.message_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard)
            )

            image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'

            message_text = (
                "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите сообщества, в которых будет опубликован розыгрыш.\n\nРезультаты также будут размещены только в этих сообществах."
            )

            await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=new_keyboard),
                message_id=callback_query.message.message_id,
                image_url=image_url
            )

            await bot.answer_callback_query(callback_query.id)
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка при выборе сообщества 😔")

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
            await bot.answer_callback_query(callback_query.id, text="Выберите хотя бы одно сообщество")
            return

        user_selected_communities[user_id] = {
            'giveaway_id': giveaway_id,
            'communities': [(comm[0], comm[1]) for comm in selected_communities]
        }

        community_links = []
        for community_id, _, community_name in selected_communities:
            try:
                chat = await bot.get_chat(community_id)
                invite_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(community_id).replace('-100', '')}"
                community_links.append(f"<a href=\"{invite_link}\">{community_name}</a>")
            except Exception as e:
                logger.error(f"Не удалось получить информацию о сообществе {community_id}: {str(e)}")
                community_links.append(f"{community_name} (ссылка недоступна)")

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🚀 Опубликовать", callback_data=f"publish_giveaway:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"activate_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'

        await bot.answer_callback_query(callback_query.id)
        await send_message_auto(
            bot,
            callback_query.from_user.id,
            f"<tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Розыгрыш будет опубликован в: {', '.join(community_links)}\nПодтвердите запуск",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            image_url=image_url
        )

    @dp.callback_query(lambda c: c.data.startswith('confirm_community_selection:'))
    async def process_confirm_community_selection(callback_query: CallbackQuery, state: FSMContext):
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
                            cursor.execute("SELECT * FROM bound_communities WHERE community_id = %s", (community_id,))
                            community = cursor.fetchone()
                            if community:
                                columns = [desc[0] for desc in cursor.description]
                                community_dict = dict(zip(columns, community))
                                community_username = community_dict.get('community_username') or community_dict.get(
                                    'community_name')
                                selected_set.add((str(community_id), community_username))

            to_add = selected_set - current_set
            to_remove = current_set - selected_set

            changes_made = bool(to_add or to_remove)

            if changes_made:
                for community_id, community_username in to_add:
                    cursor.execute(
                        "SELECT community_username, community_type, user_id, community_name "
                        "FROM bound_communities WHERE community_id = %s",
                        (community_id,)
                    )
                    community = cursor.fetchone()
                    if community:
                        community_username, community_type, user_id, community_name = community
                        cursor.execute(
                            """
                            INSERT INTO giveaway_communities (
                                giveaway_id, community_id, community_username, community_type, user_id, 
                                community_name
                            ) VALUES (%s, %s, %s, %s, %s, %s)
                            """,
                            (
                                giveaway_id, community_id, community_username, community_type, user_id,
                                community_name
                            )
                        )
                    else:
                        logger.error(f"🚫 Сообщество {community_id} не найдено")
                for community_id, _ in to_remove:
                    await unbind_community_from_giveaway(giveaway_id, community_id)

                conn.commit()
                await bot.answer_callback_query(callback_query.id, text="✅ Сообщества обновлены")
            else:
                await bot.answer_callback_query(callback_query.id, text="✅ Выбор сохранен")

            try:
                cursor.execute("DELETE FROM user_binding_state WHERE user_id = %s", (user_id,))
                conn.commit()
                await state.clear()
            except Exception as e:
                logging.error(f"Ошибка очистки состояния привязки для {user_id}: {str(e)}")

            new_callback_query = types.CallbackQuery(
                id=callback_query.id,
                from_user=callback_query.from_user,
                chat_instance=callback_query.chat_instance,
                message=callback_query.message,
                data=f"view_created_giveaway:{giveaway_id}"
            )
            if user_id in user_selected_communities:
                del user_selected_communities[user_id]
            await process_view_created_giveaway(new_callback_query, state)

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id, text="❌ Ошибка при обновлении сообществ 😔")

    @dp.callback_query(lambda c: c.data.startswith('publish_giveaway:'))
    async def process_publish_giveaway(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        participant_counter_tasks = []

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Отмена", callback_data=f"activate_giveaway:{giveaway_id}")

        await send_message_auto(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Публикуем ваш розыгрыш...",
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL
        )

        user_data = user_selected_communities.get(user_id)
        if not user_data or user_data['giveaway_id'] != giveaway_id or not user_data.get('communities'):
            await bot.answer_callback_query(callback_query.id, text="❌ Нет выбранных сообществ для публикации 😔")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "❌ Нет выбранных сообществ для публикации 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
            return

        selected_communities = user_data['communities']

        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    "🔍 Розыгрыш не найден 😕",
                    reply_markup=None,
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
                )
                return

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = giveaway['end_time'].strftime('%d.%m.%Y %H:%M (МСК)') if giveaway['end_time'] else "Не указано"
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            post_text = f"""{formatted_description}"""

            keyboard = InlineKeyboardBuilder()
            button_text = giveaway.get('button', '🎉 Участвовать')
            keyboard.button(
                text=button_text,
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )
            keyboard.adjust(1)

            success_count = 0
            error_count = 0
            error_messages = []
            published_messages = []

            image_url = None
            media_type = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                media_type = giveaway['media_type']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])

            for community_id, community_username in selected_communities:
                try:
                    if image_url:
                        sent_message = await send_message_auto(
                            bot,
                            int(community_id),
                            post_text,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML',
                            image_url=image_url,
                            media_type=media_type  # Добавляем media_type
                        )
                    else:
                        sent_message = await bot.send_message(
                            chat_id=int(community_id),
                            text=post_text,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )

                    if sent_message:
                        published_messages.append(
                            {'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                        participant_counter_tasks.append(
                            {'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                        success_count += 1
                    await asyncio.sleep(0.5)

                except aiogram.exceptions.TelegramBadRequest as e:
                    if "chat not found" in str(e).lower():
                        error_count += 1
                        error_messages.append(
                            f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибка в @{community_username}: Бот был удалён из канала или группы администратором.")
                    else:
                        error_count += 1
                        error_messages.append(
                            f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибка в @{community_username}: {str(e)}")
                except aiogram.exceptions.TelegramRetryAfter as e:
                    retry_after = e.retry_after
                    logger.warning(
                        f"<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Лимит Telegram, ждём {retry_after} сек.")
                    await asyncio.sleep(retry_after)
                    try:
                        if image_url:
                            sent_message = await send_message_auto(
                                bot,
                                int(community_id),
                                post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML',
                                image_url=image_url,
                                media_type=media_type  # Добавляем media_type
                            )
                        else:
                            sent_message = await bot.send_message(
                                chat_id=int(community_id),
                                text=post_text,
                                reply_markup=keyboard.as_markup(),
                                parse_mode='HTML'
                            )

                        if sent_message:
                            published_messages.append(
                                {'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                            participant_counter_tasks.append(
                                {'chat_id': sent_message.chat.id, 'message_id': sent_message.message_id})
                            success_count += 1
                    except aiogram.exceptions.TelegramBadRequest as retry_error:
                        if "chat not found" in str(retry_error).lower():
                            error_count += 1
                            error_messages.append(
                                f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибка в @{community_username} после паузы: Бот был удалён из канала или группы администратором.")
                        else:
                            error_count += 1
                            error_messages.append(
                                f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибка в @{community_username} после паузы: {str(retry_error)}")
                    except Exception as retry_error:
                        error_count += 1
                        error_messages.append(
                            f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибка в @{community_username} после паузы: {str(retry_error)}")
                except Exception as e:
                    error_count += 1
                    error_messages.append(
                        f"<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибка в @{community_username}: {str(e)}")

            if success_count > 0:
                try:
                    cursor.execute("DELETE FROM giveaway_winners WHERE giveaway_id = %s", (giveaway_id,))
                    cursor.execute("DELETE FROM participations WHERE giveaway_id = %s", (giveaway_id,))

                    moscow_tz = pytz.timezone('Europe/Moscow')
                    current_time = datetime.now(moscow_tz)

                    cursor.execute(
                        """
                        UPDATE giveaways 
                        SET is_active = %s, created_at = %s, published_messages = %s, participant_counter_tasks = %s 
                        WHERE id = %s
                        """,
                        ('true', current_time, json.dumps(published_messages), json.dumps(participant_counter_tasks),
                         giveaway_id)
                    )
                    conn.commit()

                    await bot.answer_callback_query(callback_query.id, text="✅ Розыгрыш запущен 🎉")

                    channel_links = []
                    for msg in published_messages:
                        chat_id = msg['chat_id']
                        message_id = msg['message_id']
                        try:
                            chat = await bot.get_chat(chat_id)
                            channel_name = chat.title
                            if chat.type in ['group', 'supergroup']:
                                post_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                            else:
                                if chat.username:
                                    post_link = f"https://t.me/{chat.username}/{message_id}"
                                else:
                                    post_link = f"https://t.me/c/{str(chat_id).replace('-100', '')}/{message_id}"
                            channel_links.append(f"<a href=\"{post_link}\">{channel_name}</a>")
                        except Exception as e:
                            logger.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                            channel_links.append("Неизвестный канал")

                    channel_info = f"\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Опубликовано в:</b> {', '.join(channel_links)}" if channel_links else ""

                    keyboard = InlineKeyboardBuilder()
                    keyboard.button(text="🏠 Назад", callback_data="back_to_main_menu")

                    result_message = f"<b><tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> Успешно опубликовано в {success_count} сообществах</b>{channel_info}\n<tg-emoji emoji-id='5451882707875276247'>🕯</tg-emoji> Количество участников будут обновляться каждые 10 мин."
                    if error_count > 0:
                        result_message += f"\n\n<b><tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Ошибок: {error_count}</b>"
                        for error in error_messages:
                            if "bot is not a member" in error:
                                community = error.split('@')[1].split(':')[0]
                                result_message += f"\n<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> @{community}: Бот не админ или сообщество удалено"
                            else:
                                result_message += f"\n{error}"

                    await send_message_auto(
                        bot,
                        callback_query.from_user.id,
                        result_message,
                        reply_markup=keyboard.as_markup(),
                        message_id=callback_query.message.message_id,
                        parse_mode='HTML',
                        image_url=image_url if image_url else DEFAULT_IMAGE_URL,
                        media_type=media_type  # Добавляем media_type
                    )
                except Exception as e:
                    logger.error(f"🚫 Ошибка активации: {str(e)}")
                    conn.rollback()
                    await bot.answer_callback_query(callback_query.id, text="Ошибка при запуске розыгрыша 😔")
                    await send_message_auto(
                        bot,
                        callback_query.from_user.id,
                        "⚠️ Ошибка при запуске розыгрыша 😔",
                        reply_markup=None,
                        message_id=callback_query.message.message_id,
                        parse_mode='HTML',
                        image_url=DEFAULT_IMAGE_URL
                    )
            else:
                await bot.answer_callback_query(callback_query.id, text="Не удалось опубликовать 😔")
                error_keyboard = InlineKeyboardBuilder()
                error_keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
                await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    f"<b><tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Публикация не удалась</b>\nОшибок: {error_count}\n\n<b>Подробности:</b>\n" + "\n".join(
                        error_messages),
                    reply_markup=error_keyboard.as_markup(),
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка при публикации 😔")
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "⚠️ Ошибка при публикации 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
        finally:
            user_selected_communities.pop(user_id, None)
