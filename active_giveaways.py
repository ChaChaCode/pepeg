from utils import end_giveaway, send_message_auto, select_random_winners, count_length_with_custom_emoji, \
    MAX_MEDIA_SIZE_MB, MAX_CAPTION_LENGTH, DEFAULT_IMAGE_URL, MAX_NAME_LENGTH, MAX_DESCRIPTION_LENGTH, MAX_WINNERS, \
    get_file_url, s3_client, YANDEX_BUCKET_NAME, FORMATTING_GUIDE2
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from datetime import datetime
import pytz
from aiogram.utils.keyboard import InlineKeyboardBuilder
import json
import requests
from aiogram.types import CallbackQuery
from typing import Dict, List, Tuple, Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_giveaway_data(cursor: Any, query: str, params: Tuple) -> List[Dict[str, Any]]:
    cursor.execute(query, params)
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]

class EditGiveawayStates(StatesGroup):
    waiting_for_new_name_active = State()
    waiting_for_new_description_active = State()
    waiting_for_new_winner_count_active = State()
    waiting_for_new_end_time_active = State()
    waiting_for_new_media_active = State()
    waiting_for_new_button_active = State()

async def upload_to_storage(file_content: bytes, filename: str) -> Tuple[bool, str]:
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
            logger.info(f"✅ Файл загружен: {unique_filename}")
            return True, public_url
        else:
            logger.error(f"❌ Ошибка загрузки: {response.status_code}")
            raise Exception(f"Не удалось загрузить: {response.status_code}")

    except Exception as e:
        logger.error(f"🚫 Ошибка: {str(e)}")
        return False, f"❌ Ошибка загрузки: {str(e)}"

def get_json_field(cursor, query, params):
    cursor.execute(query, params)
    result = cursor.fetchone()[0]
    if result is None:
        return []
    if isinstance(result, (str, bytes, bytearray)):
        return json.loads(result)
    if isinstance(result, list):
        return result
    raise ValueError(f"Неподдерживаемый тип данных для JSON-поля: {type(result)}")

async def process_long_message_active(
        message: types.Message,
        state: FSMContext,
        giveaway_id: str,
        last_message_id: int,
        field: str,
        max_length: int,
        formatting_guide: str,
        image_url: str,
        bot: Bot,
        conn,
        cursor,
        update_published_posts_active,
        _show_edit_menu_active
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
    previous_message_type = data.get('previous_message_type', 'photo')
    new_text = message.html_text if message.text else ""

    current_time = datetime.now().timestamp()

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
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
        if 0 < current_length <= max_length and current_length <= MAX_CAPTION_LENGTH:
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
                        logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message_active (успех)")
                    except Exception as e:
                        logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

                await state.update_data(
                    user_messages=[],
                    current_message_parts=[],
                    limit_exceeded=False,
                    last_message_time=None
                )

                cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
                giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
                await update_published_posts_active(giveaway_id, giveaway)

                await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id, state)
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
                        logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message_active (ошибка)")
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
                    media_type=None,
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
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message_active (лимит)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

            error_message = (
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {field_translations[field]} должно быть от 1 до {max_length} символов. Текущее: {current_length}\n{formatting_guide}"
                if current_length > max_length or not combined_current_message
                else f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {field_translations[field]} превышает лимит Telegram ({MAX_CAPTION_LENGTH} символов). Текущее: {current_length}\n{formatting_guide}"
            )
            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                error_message,
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                media_type=None,
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

    if total_length > max_length or not combined_current_message or total_length > MAX_CAPTION_LENGTH:
        if last_message_id:
            try:
                await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message_active (лимит)")
            except Exception as e:
                logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

        await state.update_data(
            user_messages=user_messages,
            current_message_parts=current_message_parts,
            limit_exceeded=True,
            last_message_id=None,
            last_message_time=current_time
        )

        error_message = (
            f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {field_translations[field]} превышает лимит ({max_length} символов). Общая длина: {total_length}\nОтправьте новое {field_translations[field].lower()}.\n{formatting_guide}"
            if total_length > max_length or not combined_current_message
            else f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> {field_translations[field]} превышает лимит Telegram ({MAX_CAPTION_LENGTH} символов). Общая длина: {total_length}\nОтправьте новое {field_translations[field].lower()}.\n{formatting_guide}"
        )
        sent_message = await send_message_auto(
            bot,
            message.chat.id,
            error_message,
            reply_markup=keyboard.as_markup(),
            message_id=None,
            parse_mode='HTML',
            image_url=image_url,
            media_type=None,
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
                logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message_active (успех)")
            except Exception as e:
                logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

        await state.update_data(
            user_messages=[],
            current_message_parts=[],
            last_message_time=None
        )

        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
        await update_published_posts_active(giveaway_id, giveaway)

        await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id, state)
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
                logger.info(f"Удалено старое сообщение {last_message_id} в process_long_message_active (ошибка)")
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
            media_type=None,
            previous_message_type=previous_message_type
        )
        if sent_message:
            await state.update_data(
                last_message_id=sent_message.message_id,
                previous_message_type='photo'
            )

def register_active_giveaways_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    """Регистрирует обработчики для управления активными розыгрышами 🎁"""

    async def update_published_posts_active(giveaway_id: str, giveaway: Dict[str, Any]):
        try:
            published_messages = get_json_field(cursor, "SELECT published_messages FROM giveaways WHERE id = %s",
                                               (giveaway_id,))
            if not published_messages:
                logger.info(f"Нет опубликованных сообщений для розыгрыша {giveaway_id}")
                return

            cursor.execute("SELECT COUNT(*) FROM participations WHERE giveaway_id = %s", (giveaway_id,))
            participants_count = cursor.fetchone()[0]

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = (giveaway['end_time']).strftime('%d.%m.%Y %H:%M (МСК)')
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            new_post_text = f"{formatted_description}"
            new_post_length = count_length_with_custom_emoji(new_post_text)
            current_message_type = 'photo' if new_post_length <= 800 else 'image'

            keyboard = InlineKeyboardBuilder()
            button_text = giveaway.get('button', '🎉 Участвовать')
            keyboard.button(
                text=f"{button_text} ({participants_count})",
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )

            image_url = None
            media_type = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                media_type = giveaway['media_type']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])
            else:
                image_url = DEFAULT_IMAGE_URL

            updated_published_messages = []

            for message in published_messages:
                chat_id = message['chat_id']
                message_id = message['message_id']
                previous_message_type = message.get('message_type', 'photo')

                try:
                    # Если тип сообщения изменился или редактирование невозможно, удаляем старое сообщение
                    if previous_message_type != current_message_type or new_post_length > MAX_CAPTION_LENGTH:
                        try:
                            await bot.delete_message(chat_id=chat_id, message_id=message_id)
                            logger.info(f"Удалено старое сообщение {message_id} в чате {chat_id} перед переопубликованием")
                            message_id = None  # Сбрасываем message_id для отправки нового сообщения
                        except Exception as e:
                            logger.warning(f"Не удалось удалить сообщение {message_id} в чате {chat_id}: {str(e)}")

                    sent_message = await send_message_auto(
                        bot,
                        chat_id,
                        new_post_text,
                        reply_markup=keyboard.as_markup(),
                        message_id=message_id,
                        parse_mode='HTML',
                        image_url=image_url,
                        media_type=media_type,
                        previous_message_type=previous_message_type
                    )

                    # Если отправлено новое сообщение, обновляем message_id
                    if sent_message and (not message_id or message_id != sent_message.message_id):
                        updated_published_messages.append({
                            'chat_id': chat_id,
                            'message_id': sent_message.message_id,
                            'message_type': current_message_type
                        })
                        logger.info(f"Создано новое сообщение {sent_message.message_id} в чате {chat_id}")
                    else:
                        updated_published_messages.append({
                            'chat_id': chat_id,
                            'message_id': message_id,
                            'message_type': current_message_type
                        })
                        logger.info(f"Отредактировано сообщение {message_id} в чате {chat_id}")

                except Exception as e:
                    logger.error(f"🚫 Ошибка обновления поста {message_id} в чате {chat_id}: {str(e)}")
                    updated_published_messages.append(message)  # Сохраняем старые данные в случае ошибки
                    continue

            # Обновляем published_messages в базе данных
            cursor.execute(
                "UPDATE giveaways SET published_messages = %s WHERE id = %s",
                (json.dumps(updated_published_messages), giveaway_id)
            )
            conn.commit()
            logger.info(f"Обновлены published_messages для розыгрыша {giveaway_id}")

        except Exception as e:
            logger.error(f"🚫 Ошибка в update_published_posts_active для розыгрыша {giveaway_id}: {str(e)}")
            conn.rollback()
            raise

    async def _show_edit_menu_active(user_id: int, giveaway_id: str, message_id: int = None, state: FSMContext = None):
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, cursor.fetchone()))
        if not giveaway:
            data = await state.get_data() if state else {}
            previous_message_type = data.get('previous_message_type', 'photo')
            sent_message = await send_message_auto(
                bot,
                user_id,
                "🔍 Розыгрыш не найден 😕",
                reply_markup=None,
                message_id=message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message and state:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )
            return

        if state:
            await state.update_data(
                user_messages=[],
                current_message_parts=[],
                limit_exceeded=False,
                last_message_time=None
            )

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="📝 Название", callback_data=f"edit_name_active:{giveaway_id}")
        keyboard.button(text="📄 Описание", callback_data=f"edit_description_active:{giveaway_id}")
        keyboard.button(text="🏆 Победители", callback_data=f"edit_winner_count_active:{giveaway_id}")
        keyboard.button(text="⏰ Дата", callback_data=f"change_end_date_active:{giveaway_id}")
        keyboard.button(text="🖼️ Медиа", callback_data=f"manage_media_active:{giveaway_id}")
        keyboard.button(text="🔗 Кнопка", callback_data=f"edit_button_active:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_active_giveaway:{giveaway_id}")
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
                    logger.info(
                        f"Удалено старое сообщение {message_id} перед отправкой нового в _show_edit_menu_active")
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
                "<tg-emoji emoji-id='5422649047334794716'>😵</tg-emoji> Ошибка загрузки меню 😔",
                reply_markup=None,
                message_id=message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if state and sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: types.CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('view_active_giveaway:'))
    async def process_view_active_giveaway(callback_query: CallbackQuery, state: FSMContext):
        """Просмотр активного розыгрыша 👀"""
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден 😕")
                data = await state.get_data()
                previous_message_type = data.get('previous_message_type', 'photo')
                sent_message = await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    "🔍 Розыгрыш не найден 😕",
                    reply_markup=None,
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL,
                    media_type=None,
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type='photo'
                    )
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
                channel_info = f"\n<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Опубликовано в:</b> {', '.join(channel_links)}"

            description = giveaway['description']
            winner_count = str(giveaway['winner_count'])
            end_time = (giveaway['end_time']).strftime('%d.%m.%Y %H:%M (МСК)')
            formatted_description = description.replace('{win}', winner_count).replace('{data}', end_time)

            giveaway_info = f"""{formatted_description}

    <tg-emoji emoji-id='5451882707875276247'>🕯</tg-emoji> <b>Участников:</b> {participants_count}
    {channel_info}
    """

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="✏️ Редактировать", callback_data=f"edit_active_post:{giveaway_id}")
            keyboard.button(text="🎉 Сообщение победителям", callback_data=f"message_winners_active:{giveaway_id}")
            keyboard.button(text="⏹️ Завершить", callback_data=f"confirm_force_end_giveaway:{giveaway_id}")
            button_text = giveaway.get('button', '🎉 Участвовать')
            keyboard.button(text=f"📱 {button_text}", url=f"https://t.me/Snapi/app?startapp={giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data="created_giveaways")
            keyboard.adjust(1)

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
            previous_message_type = data.get('previous_message_type', 'photo')
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
                    logger.info(
                        f"Удалено старое сообщение {callback_query.message.message_id} перед отправкой нового в process_view_active_giveaway")
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
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )

    @dp.callback_query(lambda c: c.data.startswith('confirm_force_end_giveaway:'))
    async def process_confirm_force_end_giveaway(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="✅ Да", callback_data=f"force_end_giveaway:{giveaway_id}")
        keyboard.button(text="❌ Нет", callback_data=f"view_active_giveaway:{giveaway_id}")
        keyboard.adjust(2)

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')
        current_message_type = 'photo'

        await bot.answer_callback_query(callback_query.id)

        # Пытаемся удалить старое сообщение, если тип изменился
        if previous_message_type != current_message_type:
            try:
                await bot.delete_message(
                    chat_id=callback_query.from_user.id,
                    message_id=callback_query.message.message_id
                )
                logger.info(
                    f"Удалено старое сообщение {callback_query.message.message_id} в process_confirm_force_end_giveaway")
            except Exception as e:
                logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

        sent_message = await send_message_auto(
            bot,
            callback_query.from_user.id,
            "<tg-emoji emoji-id='5445267414562389170'>🗑</tg-emoji> Вы уверены, что хотите завершить розыгрыш?",
            reply_markup=keyboard.as_markup(),
            message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
            parse_mode='HTML',
            image_url=DEFAULT_IMAGE_URL,
            media_type=None,
            previous_message_type=previous_message_type
        )

        if sent_message:
            await state.update_data(
                last_message_id=sent_message.message_id,
                previous_message_type=current_message_type
            )

    @dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
    async def process_force_end_giveaway(callback_query: CallbackQuery, state: FSMContext):
        global image_url, keyboard, media_type, result_message
        giveaway_id = callback_query.data.split(':')[1]
        try:
            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, cursor.fetchone()))
            if not giveaway:
                raise Exception("Розыгрыш не найден")

            # Получаем данные состояния
            data = await state.get_data()
            previous_message_type = data.get('previous_message_type', 'photo')

            image_url = None
            media_type = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                media_type = giveaway['media_type']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])
            else:
                image_url = DEFAULT_IMAGE_URL

            current_message_type = media_type or 'photo'

            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_force_end_giveaway")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                "<tg-emoji emoji-id='5386367538735104399'>⌛️</tg-emoji> Завершаем розыгрыш...",
                reply_markup=None,
                message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )

            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )

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

            winners = await select_random_winners(
                bot, participants, min(len(participants), giveaway['winner_count']), giveaway_id, conn, cursor
            )

            await end_giveaway(bot=bot, giveaway_id=giveaway_id, conn=conn, cursor=cursor, notify_creator=False)

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

            await bot.answer_callback_query(callback_query.id)

            current_message_type = media_type or (
                'image' if count_length_with_custom_emoji(result_message) > 800 else 'photo')

            # Удаляем предыдущее сообщение, если тип изменился
            last_message_id = data.get('last_message_id')
            if previous_message_type != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=callback_query.from_user.id, message_id=last_message_id)
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_force_end_giveaway (результат)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                result_message,
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type,
                previous_message_type=previous_message_type
            )

            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Ошибка при завершении 😔")
            data = await state.get_data()
            previous_message_type = data.get('previous_message_type', 'photo')
            cursor.execute("SELECT is_active, is_completed FROM giveaways WHERE id = %s", (giveaway_id,))
            result = cursor.fetchone()
            if result and result[0] == 'false' and result[1] == 'true':
                logger.info(f"Розыгрыш {giveaway_id} все же завершился, отправляем результат")
                current_message_type = media_type or (
                    'image' if count_length_with_custom_emoji(result_message) > 800 else 'photo')
                sent_message = await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    result_message,
                    reply_markup=keyboard.as_markup(),
                    message_id=None,
                    parse_mode='HTML',
                    image_url=image_url,
                    media_type=media_type,
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type=current_message_type
                    )
            else:
                sent_message = await send_message_auto(
                    bot,
                    callback_query.from_user.id,
                    "Ошибка при завершении 😔",
                    reply_markup=None,
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL,
                    media_type=None,
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type='photo'
                    )

    @dp.callback_query(lambda c: c.data.startswith('edit_active_post:'))
    async def process_edit_active_post(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await _show_edit_menu_active(callback_query.from_user.id, giveaway_id, callback_query.message.message_id)

    @dp.callback_query(lambda c: c.data.startswith('edit_name_active:'))
    async def process_edit_name_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT name FROM giveaways WHERE id = %s", (giveaway_id,))
        current_name = cursor.fetchone()[0]

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(
            giveaway_id=giveaway_id,
            last_message_id=callback_query.message.message_id,
            user_messages=[],
            current_message_parts=[],
            limit_exceeded=False,
            last_message_time=None
        )
        await state.set_state(EditGiveawayStates.waiting_for_new_name_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
        keyboard.adjust(1)

        message_text = (
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Текущее название: <b>{current_name}</b>\n\n"
            f"Отправьте новое название (до {MAX_NAME_LENGTH} символов):"
            if current_name else
            f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Отправьте название розыгрыша (до {MAX_NAME_LENGTH} символов):"
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
                    logger.info(
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_edit_name_active")
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
                media_type=None,
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
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_name_active)
    async def process_new_name_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data.get('giveaway_id')
        last_message_id = data.get('last_message_id')
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
        await process_long_message_active(
            message=message,
            state=state,
            giveaway_id=giveaway_id,
            last_message_id=last_message_id,
            field='name',
            max_length=MAX_NAME_LENGTH,
            formatting_guide="",
            image_url=image_url,
            bot=bot,
            conn=conn,
            cursor=cursor,
            update_published_posts_active=update_published_posts_active,
            _show_edit_menu_active=_show_edit_menu_active
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_description_active:'))
    async def process_edit_description_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT description FROM giveaways WHERE id = %s", (giveaway_id,))
        current_description = cursor.fetchone()[0]

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(
            giveaway_id=giveaway_id,
            last_message_id=callback_query.message.message_id,
            user_messages=[],
            current_message_parts=[],
            limit_exceeded=False,
            last_message_time=None
        )
        await state.set_state(EditGiveawayStates.waiting_for_new_description_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
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
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_edit_description_active")
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
                media_type=None,
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
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_description_active)
    async def process_new_description_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data.get('giveaway_id')
        last_message_id = data.get('last_message_id')
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_opis2.jpg'
        await process_long_message_active(
            message=message,
            state=state,
            giveaway_id=giveaway_id,
            last_message_id=last_message_id,
            field='description',
            max_length=MAX_DESCRIPTION_LENGTH,
            formatting_guide=FORMATTING_GUIDE2,
            image_url=image_url,
            bot=bot,
            conn=conn,
            cursor=cursor,
            update_published_posts_active=update_published_posts_active,
            _show_edit_menu_active=_show_edit_menu_active
        )

    @dp.callback_query(lambda c: c.data.startswith('edit_button_active:'))
    async def process_edit_button_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT button FROM giveaways WHERE id = %s", (giveaway_id,))
        current_button = cursor.fetchone()[0] or "🎉 Участвовать"

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(
            giveaway_id=giveaway_id,
            last_message_id=callback_query.message.message_id,
            user_messages=[],
            current_message_parts=[],
            limit_exceeded=False,
            last_message_time=None
        )
        await state.set_state(EditGiveawayStates.waiting_for_new_button_active)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
        keyboard.adjust(1)

        message_text = (
            f"<tg-emoji emoji-id='5271604874419647061'>🔗</tg-emoji> Текущий текст кнопки: <b>{current_button}</b>\n\n"
            f"Отправьте новый текст для кнопки (до 50 символов)"
        )

        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_button2.jpg'
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
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_edit_button_active")
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
                media_type=None,
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
                message_id=None,
                parse_mode='HTML',
                image_url=image_url,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_button_active)
    async def process_new_button_active(message: types.Message, state: FSMContext):
        logger.info(f"Received message: {message.text}")
        current_state = await state.get_state()
        logger.info(f"Current state: {current_state}")

        data = await state.get_data()
        giveaway_id = data.get('giveaway_id')
        last_message_id = data.get('last_message_id')
        image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_button2.jpg'

        if not giveaway_id:
            logger.error("No giveaway_id in state data")
            await message.reply("Ошибка: не найден идентификатор розыгрыша 😔")
            await state.clear()
            return

        combined_message = message.text
        current_length = count_length_with_custom_emoji(combined_message)
        logger.info(f"Text: {combined_message}, Length: {current_length}")

        if current_length > 50:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await message.reply(
                f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Текст кнопки превышает лимит (50 символов). Текущая длина: {current_length}",
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
            return

        try:
            cursor.execute(
                "UPDATE giveaways SET button = %s WHERE id = %s",
                (combined_message, giveaway_id)
            )
            conn.commit()
            logger.info(f"Button updated for giveaway {giveaway_id}")

            cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
            giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))
            await update_published_posts_active(giveaway_id, giveaway)

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await _show_edit_menu_active(message.from_user.id, giveaway_id, last_message_id)
            await state.clear()
        except Exception as e:
            logger.error(f"Error updating button: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            await message.reply(
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить текст кнопки 😔",
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

    @dp.callback_query(lambda c: c.data.startswith('edit_winner_count_active:'))
    async def process_edit_winner_count_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_winner_count_active)

        cursor.execute("SELECT winner_count FROM giveaways WHERE id = %s", (giveaway_id,))
        current_winner_count = cursor.fetchone()[0]

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        message_text = (
            f"<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> Текущее количество победителей: <b>{current_winner_count}</b>\n\n"
            f"Укажите новое число (максимум {MAX_WINNERS}):"
        )
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
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_edit_winner_count_active")
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
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_winner_count_active)
    async def process_new_winner_count_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']
        previous_message_type = data.get('previous_message_type', 'photo')

        try:
            new_winner_count = int(message.text)
            if new_winner_count <= 0:
                raise ValueError("Количество должно быть положительным")

            if new_winner_count > MAX_WINNERS:
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
                current_message_type = 'photo'
                await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
                if previous_message_type != current_message_type and last_message_id:
                    try:
                        await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                        logger.info(
                            f"Удалено старое сообщение {last_message_id} в process_new_winner_count_active (лимит)")
                    except Exception as e:
                        logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
                sent_message = await send_message_auto(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Слишком много победителей Максимум {MAX_WINNERS}",
                    reply_markup=keyboard.as_markup(),
                    message_id=None,
                    parse_mode='HTML',
                    image_url=DEFAULT_IMAGE_URL,
                    media_type=None,
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type=current_message_type
                    )
                return

            cursor.execute("SELECT winner_count FROM giveaways WHERE id = %s", (giveaway_id,))
            current_winner_count = cursor.fetchone()[0]

            cursor.execute("UPDATE giveaways SET winner_count = %s WHERE id = %s", (new_winner_count, giveaway_id))
            if new_winner_count > current_winner_count:
                for place in range(current_winner_count + 1, new_winner_count + 1):
                    cursor.execute(
                        "INSERT INTO congratulations (giveaway_id, place, message) VALUES (%s, %s, %s)",
                        (giveaway_id, place, f"🎉 Поздравляем Вы заняли {place} место")
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
            current_message_type = 'photo'
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            if previous_message_type != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(
                        f"Удалено старое сообщение {last_message_id} в process_new_winner_count_active (ошибка)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Введите положительное число Например, 3",
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            current_message_type = 'photo'
            if previous_message_type != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(
                        f"Удалено старое сообщение {last_message_id} в process_new_winner_count_active (общая ошибка)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить победителей 😔",
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

    @dp.callback_query(lambda c: c.data.startswith('change_end_date_active:'))
    async def process_change_end_date_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_end_time_active)

        cursor.execute("SELECT end_time FROM giveaways WHERE id = %s", (giveaway_id,))
        current_end_time = cursor.fetchone()[0]
        formatted_end_time = (current_end_time).strftime('%d.%m.%Y %H:%M (МСК)')

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Текущее время окончания: <b>{formatted_end_time}</b>

    Укажите новую дату завершения в формате ДД.ММ.ГГГГ ЧЧ:ММ по МСК

    <tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
    """
        current_message_type = 'photo' if count_length_with_custom_emoji(html_message) <= 800 else 'image'

        try:
            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_change_end_date_active")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {callback_query.message.message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                html_message,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_type != current_message_type else callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                html_message,
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        await bot.answer_callback_query(callback_query.id)

    @dp.message(EditGiveawayStates.waiting_for_new_end_time_active)
    async def process_new_end_time_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        last_message_id = data['last_message_id']
        previous_message_type = data.get('previous_message_type', 'photo')

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
            html_message = f"""<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Неправильный формат даты Используйте ДД.ММ.ГГГГ ЧЧ:ММ

    <tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Сейчас в Москве:\n<code>{current_time}</code>
    """
            current_message_type = 'photo'
            if previous_message_type != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_new_end_time_active (ошибка)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                html_message,
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
            current_message_type = 'photo'
            if previous_message_type != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=message.chat.id, message_id=last_message_id)
                    logger.info(
                        f"Удалено старое сообщение {last_message_id} в process_new_end_time_active (общая ошибка)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")
            sent_message = await send_message_auto(
                bot,
                message.chat.id,
                "<tg-emoji emoji-id='5210952531676504517'>❌</tg-emoji> Не удалось обновить дату 😔",
                reply_markup=keyboard.as_markup(),
                message_id=None,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

    @dp.callback_query(lambda c: c.data.startswith('manage_media_active:'))
    async def process_manage_media_active(callback_query: CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = dict(zip([desc[0] for desc in cursor.description], cursor.fetchone()))

        if not giveaway:
            data = await state.get_data()
            previous_message_type = data.get('previous_message_type', 'photo')
            sent_message = await send_message_auto(
                bot,
                callback_query.from_user.id,
                "🔍 Розыгрыш не найден 😕",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL,
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type='photo'
                )
            await bot.answer_callback_query(callback_query.id)
            return

        # Получаем данные состояния
        data = await state.get_data()
        previous_message_type = data.get('previous_message_type', 'photo')

        await state.update_data(giveaway_id=giveaway_id, last_message_id=callback_query.message.message_id)
        await state.set_state(EditGiveawayStates.waiting_for_new_media_active)

        keyboard = InlineKeyboardBuilder()
        if giveaway['media_type']:
            keyboard.button(text="🗑️ Удалить", callback_data=f"delete_media_active:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"edit_active_post:{giveaway_id}")
        keyboard.adjust(1, 1)

        message_text = (
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Текущее медиа: {'Фото' if giveaway['media_type'] == 'photo' else 'GIF' if giveaway['media_type'] == 'gif' else 'Видео'}.\n\nОтправьте новое или удалите текущее."
            if giveaway['media_type'] else
            f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)"
        )

        image_url = None
        media_type = None
        if giveaway['media_type'] and giveaway['media_file_id']:
            image_url = giveaway['media_file_id']
            media_type = giveaway['media_type']
            if not image_url.startswith('http'):
                image_url = await get_file_url(bot, giveaway['media_file_id'])
        else:
            image_url = DEFAULT_IMAGE_URL

        current_message_type = media_type if media_type else (
            'image' if count_length_with_custom_emoji(message_text) > 800 else 'photo')

        try:
            # Пытаемся удалить старое сообщение, если тип изменился
            if previous_message_type != current_message_type:
                try:
                    await bot.delete_message(
                        chat_id=callback_query.from_user.id,
                        message_id=callback_query.message.message_id
                    )
                    logger.info(
                        f"Удалено старое сообщение {callback_query.message.message_id} в process_manage_media_active")
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
                media_type=media_type,
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
                media_type=media_type,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
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
                await send_message_auto(
                    bot,
                    message.chat.id,
                    "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji> Отправьте фото, GIF или видео",
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
                await send_message_auto(
                    bot,
                    message.chat.id,
                    f"<tg-emoji emoji-id='5197564405650307134'>🤯</tg-emoji> Файл слишком большой Максимум {MAX_MEDIA_SIZE_MB} МБ",
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
            await send_message_auto(
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
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                f"<tg-emoji emoji-id='5235837920081887219'>📸</tg-emoji> Отправьте фото, GIF или видео (до {MAX_MEDIA_SIZE_MB} МБ)",
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
            await send_message_auto(
                bot,
                callback_query.from_user.id,
                "Не удалось удалить медиа 😔",
                reply_markup=None,
                message_id=callback_query.message.message_id,
                parse_mode='HTML',
                image_url=DEFAULT_IMAGE_URL
            )
