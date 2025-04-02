import logging
import math
import re
from aiogram import Bot, types
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from utils import send_message_with_image

logger = logging.getLogger(__name__)

def strip_html_tags(text: str) -> str:
    """Удаляет HTML-теги из текста 🧹"""
    return re.sub(r'<[^>]+>', '', text)

def register_history_handlers(dp, bot: Bot, conn, cursor):
    """Регистрация обработчиков для истории розыгрышей."""

    @dp.callback_query(lambda c: c.data == 'giveaway_history' or c.data.startswith('giveaway_history_page:'))
    async def process_giveaway_history(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if ':' in callback_query.data else 1

        try:
            # Получаем общее количество завершенных розыгрышей (is_completed = true)
            cursor.execute(
                """
                SELECT COUNT(*) FROM giveaways 
                WHERE user_id = %s AND is_completed = true
                """,
                (user_id,)
            )
            total_giveaways = cursor.fetchone()[0]
            if total_giveaways == 0:
                await bot.answer_callback_query(callback_query.id,
                                                text="📭 Пока нет завершенных розыгрышей.")
                return

            total_pages = max(1, math.ceil(total_giveaways / ITEMS_PER_PAGE))
            offset = (current_page - 1) * ITEMS_PER_PAGE

            # Получаем только завершенные розыгрыши (is_completed = true) для текущей страницы
            cursor.execute(
                """
                SELECT * FROM giveaways 
                WHERE user_id = %s AND is_completed = true
                ORDER BY end_time DESC
                LIMIT %s OFFSET %s
                """,
                (user_id, ITEMS_PER_PAGE, offset)
            )
            completed_giveaways = cursor.fetchall()

            keyboard = InlineKeyboardBuilder()
            for giveaway in completed_giveaways:
                name = str(giveaway[2]) if giveaway[2] is not None else "Без названия"
                clean_name = strip_html_tags(name)[:61] + "..." if len(name) > 64 else strip_html_tags(name)
                callback_data = f"view_completed_giveaway:{giveaway[0]}"
                keyboard.row(InlineKeyboardButton(
                    text=f"{clean_name}",
                    callback_data=callback_data
                ))

            nav_buttons = []
            if total_pages > 1:
                prev_page = current_page - 1 if current_page > 1 else total_pages
                nav_buttons.append(
                    InlineKeyboardButton(text="◀️", callback_data=f"giveaway_history_page:{prev_page}"))

                nav_buttons.append(InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="ignore"))

                next_page = current_page + 1 if current_page < total_pages else 1
                nav_buttons.append(
                    InlineKeyboardButton(text="▶️", callback_data=f"giveaway_history_page:{next_page}"))

            if nav_buttons:
                keyboard.row(*nav_buttons)
            keyboard.row(InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu"))

            message_text = "<tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Завершенные розыгрыши:"

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(
                bot,
                user_id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id
            )
        except Exception as e:
            logger.error(f"🚫 Ошибка: {str(e)}")
            await bot.answer_callback_query(callback_query.id,
                                            text="Упс! Что-то пошло не так 😔")

    @dp.callback_query(lambda c: c.data.startswith('view_completed_giveaway:'))
    async def process_view_completed_giveaway(callback_query: CallbackQuery, state: FSMContext):
        """Обработка просмотра детальной информации о завершенном розыгрыше с победителями."""
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id

        try:
            # Получение данных о розыгрыше
            cursor.execute(
                """
                SELECT id, name, description, end_time, winner_count, media_type, media_file_id 
                FROM giveaways 
                WHERE id = %s AND user_id = %s AND is_completed = 'true'
                """,
                (giveaway_id, user_id)
            )
            giveaway = cursor.fetchone()

            if not giveaway:
                await bot.answer_callback_query(callback_query.id,
                                                text="🔍 Розыгрыш не найден или не завершен 😕")
                return

            giveaway_id, name, description, end_time, winner_count, media_type, media_file_id = giveaway
            end_time_str = end_time.strftime("%d.%m.%Y") if end_time else "Не указана"

            # Получение списка победителей
            cursor.execute(
                """
                SELECT place, username, name 
                FROM giveaway_winners 
                WHERE giveaway_id = %s 
                ORDER BY place
                """,
                (giveaway_id,)
            )
            winners = cursor.fetchall()

            # Форматирование списка победителей
            winners_text = "Победители не определены"
            if winners:
                winners_formatted = []
                for place, username, winner_name in winners:
                    medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(place, "🏅")
                    display_name = f"@{username}" if username else winner_name or f"Участник {place}"
                    winners_formatted.append(f"{medal} {place}. {display_name}")
                winners_text = "\n".join(winners_formatted)

            # Формирование информации о розыгрыше
            giveaway_info = (
                f"<b>{name}</b>\n\n"
                f"{description or 'Описание отсутствует'}\n\n"
                f"<b>Дата завершения:</b> {end_time_str}\n"
                f"<b>Количество победителей:</b> {winner_count}\n\n"
                f"<b>Победители:</b>\n<blockquote expandable>{winners_text}</blockquote>"
            )

            # Создание клавиатуры с кнопкой "Результаты"
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text="📊 Результаты",
                url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
            )
            keyboard.button(text="📜 Назад к списку", callback_data="giveaway_history")
            keyboard.adjust(1)

            await bot.answer_callback_query(callback_query.id)

            # Отправка сообщения с медиа или без
            if media_type and media_file_id:
                media_types = {
                    'photo': types.InputMediaPhoto,
                    'gif': types.InputMediaAnimation,
                    'video': types.InputMediaVideo
                }
                await bot.edit_message_media(
                    chat_id=callback_query.message.chat.id,
                    message_id=callback_query.message.message_id,
                    media=media_types[media_type](
                        media=media_file_id,
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
                    message_id=callback_query.message.message_id,
                    parse_mode='HTML'
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка при просмотре завершенного розыгрыша: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id, text="Упс! Что-то сломалось 😔")
            await bot.send_message(
                callback_query.from_user.id,
                "⚠️ Упс! Что-то пошло не так. Попробуйте снова!"
            )
