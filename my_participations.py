from aiogram import Dispatcher, Bot, types
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import logging
from utils import send_message_with_image
import math
import re
from datetime import timedelta

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def strip_html_tags(text):
    """Удаляет HTML-теги из текста, оставляя только видимую часть."""
    clean_text = re.sub(r'<[^>]+>', '', text)
    return clean_text

def replace_variables(description, winner_count, end_time):
    """Заменяет переменные {win} и {data} в описании на актуальные значения."""
    formatted_end_time = (end_time + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M (МСК)')
    description = description.replace('{win}', str(winner_count))
    description = description.replace('{data}', formatted_end_time)
    return description

def register_my_participations_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    @dp.callback_query(lambda c: c.data == 'my_participations' or c.data.startswith('my_participations_page:'))
    async def process_my_participations(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if callback_query.data.startswith(
            'my_participations_page:') else 1

        try:
            # Получаем общее количество уникальных активных розыгрышей, в которых участвует пользователь
            cursor.execute(
                """
                SELECT COUNT(DISTINCT g.id) 
                FROM participations p
                JOIN giveaways g ON p.giveaway_id = g.id
                WHERE p.user_id = %s AND g.is_active = 'true' AND g.user_id != 1
                """,
                (user_id,)
            )
            total_participations = cursor.fetchone()[0]
            if total_participations == 0:
                await bot.answer_callback_query(callback_query.id,
                                                text="Вы не участвуете ни в одном активном розыгрыше.")
                return

            total_pages = max(1, math.ceil(total_participations / ITEMS_PER_PAGE))
            offset = (current_page - 1) * ITEMS_PER_PAGE

            # Получаем только нужные записи для текущей страницы
            cursor.execute(
                """
                SELECT DISTINCT g.id AS giveaway_id, g.user_id AS creator_user_id, g.name, g.description, 
                               g.end_time, g.media_type, g.media_file_id
                FROM participations p
                JOIN giveaways g ON p.giveaway_id = g.id
                WHERE p.user_id = %s AND g.is_active = 'true' AND g.user_id != 1
                ORDER BY g.end_time
                LIMIT %s OFFSET %s
                """,
                (user_id, ITEMS_PER_PAGE, offset)
            )
            current_participations = cursor.fetchall()

            # Преобразуем результат в список словарей
            columns = [desc[0] for desc in cursor.description]
            current_participations = [dict(zip(columns, row)) for row in current_participations]

            # Генерируем клавиатуру с пагинацией
            keyboard = InlineKeyboardBuilder()

            # Добавляем кнопки для участий
            for participation in current_participations:
                clean_name = strip_html_tags(participation['name'])
                if len(clean_name) > 64:
                    clean_name = clean_name[:61] + "..."
                keyboard.row(types.InlineKeyboardButton(
                    text=clean_name,
                    callback_data=f"giveaway_{participation['giveaway_id']}"
                ))

            # Создаем навигационные кнопки с кольцевой логикой
            nav_buttons = []
            if total_pages > 1:
                prev_page = current_page - 1 if current_page > 1 else total_pages
                nav_buttons.append(types.InlineKeyboardButton(
                    text="◀️",
                    callback_data=f"my_participations_page:{prev_page}"
                ))

                nav_buttons.append(types.InlineKeyboardButton(
                    text=f"📄 {current_page}/{total_pages}",
                    callback_data="ignore"
                ))

                next_page = current_page + 1 if current_page < total_pages else 1
                nav_buttons.append(types.InlineKeyboardButton(
                    text="▶️",
                    callback_data=f"my_participations_page:{next_page}"
                ))

            if nav_buttons:
                keyboard.row(*nav_buttons)

            keyboard.row(types.InlineKeyboardButton(
                text="◀️ Назад",
                callback_data="back_to_main_menu"
            ))

            message_text = f"<tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Список активных розыгрышей, в которых вы участвуете"
            if total_pages > 1:
                message_text += f" (Страница {current_page} из {total_pages}):"
            else:
                message_text += ":"

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(
                bot,
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                text=message_text,
                reply_markup=keyboard.as_markup()
            )
        except Exception as e:
            logger.error(f"Error in process_my_participations: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении ваших участий.")

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('giveaway_'))
    async def process_giveaway_details(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split('_')[1]
        try:
            # Запрос к таблице giveaways, включая winner_count
            cursor.execute(
                "SELECT id, name, description, end_time, media_type, media_file_id, winner_count "
                "FROM giveaways WHERE id = %s",
                (giveaway_id,)
            )
            giveaway = cursor.fetchone()

            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
                return

            # Преобразуем результат в словарь
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, giveaway))

            # Заменяем переменные в description
            description_with_vars = replace_variables(
                giveaway['description'],
                giveaway['winner_count'],
                giveaway['end_time']
            )

            # Формируем текст с сохранением HTML-форматирования
            giveaway_info = f"""
{giveaway['name']}

{description_with_vars}

<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Дата завершения:</b> {(giveaway['end_time'] + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}
"""

            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text="Открыть",
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )
            keyboard.button(text="◀️ Назад к списку", callback_data="my_participations")
            keyboard.adjust(1)

            try:
                await bot.answer_callback_query(callback_query.id)
            except TelegramBadRequest as e:
                if "query is too old" in str(e):
                    logger.warning(f"Callback query is too old: {e}")
                else:
                    raise

            if giveaway['media_type'] and giveaway['media_file_id']:
                try:
                    if giveaway['media_type'] == 'photo':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaPhoto(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'gif':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaAnimation(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'video':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaVideo(
                                media=giveaway['media_file_id'],
                                caption=giveaway_info,
                                parse_mode='HTML'
                            ),
                            reply_markup=keyboard.as_markup()
                        )
                except TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logger.warning(f"Message to edit not found: {e}")
                        await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                    else:
                        raise
            else:
                try:
                    await send_message_with_image(
                        bot,
                        callback_query.from_user.id,
                        giveaway_info,
                        reply_markup=keyboard.as_markup(),
                        message_id=callback_query.message.message_id,
                        parse_mode='HTML'
                    )
                except TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logger.warning(f"Message to edit not found: {e}")
                        await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                    else:
                        raise

        except Exception as e:
            logger.error(f"Error in process_giveaway_details: {str(e)}")
            try:
                await bot.answer_callback_query(callback_query.id,
                                                text="Произошла ошибка при получении информации о розыгрыше.")
            except TelegramBadRequest:
                logger.warning("Failed to answer callback query due to timeout")

            await bot.send_message(
                chat_id=callback_query.from_user.id,
                text="Произошла ошибка при получении информации о розыгрыше. Пожалуйста, попробуйте еще раз.",
                parse_mode='HTML'
            )

    async def send_new_giveaway_message(chat_id, giveaway, g_info, keyboard):
        if giveaway['media_type'] and giveaway['media_file_id']:
            media_type = giveaway['media_type']
            if media_type == 'photo':
                await bot.send_photo(
                    chat_id,
                    giveaway['media_file_id'],
                    caption=g_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            elif media_type == 'gif':
                await bot.send_animation(
                    chat_id,
                    giveaway['media_file_id'],
                    caption=g_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            elif media_type == 'video':
                await bot.send_video(
                    chat_id,
                    giveaway['media_file_id'],
                    caption=g_info,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
        else:
            await send_message_with_image(
                bot,
                chat_id,
                g_info,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

    return send_new_giveaway_message
