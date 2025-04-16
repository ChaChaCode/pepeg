import logging
import math
from aiogram import Bot, Dispatcher
from aiogram.types import CallbackQuery, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from utils import send_message_auto, count_length_with_custom_emoji, strip_html_tags, get_file_url

logger = logging.getLogger(__name__)


def register_history_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    """Регистрация обработчиков для истории розыгрышей."""

    @dp.callback_query(lambda c: c.data == 'giveaway_history' or c.data.startswith('giveaway_history_page:'))
    async def process_giveaway_history(callback_query: CallbackQuery, state: FSMContext):
        global previous_message_length, last_message_id, previous_message_length
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if ':' in callback_query.data else 1

        try:
            # Получаем данные из состояния
            data = await state.get_data()
            last_message_id = data.get('last_message_id', callback_query.message.message_id)
            previous_message_length = data.get('previous_message_length', 'short')

            # Получаем общее количество завершенных розыгрышей
            cursor.execute(
                """
                SELECT COUNT(*) FROM giveaways 
                WHERE user_id = %s AND is_completed = true
                """,
                (user_id,)
            )
            total_giveaways = cursor.fetchone()[0]
            if total_giveaways == 0:
                message_text = "📭 Пока нет завершенных розыгрышей."
                current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                await bot.answer_callback_query(callback_query.id)
                sent_message = await send_message_auto(
                    bot,
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                    media_type=None,
                    previous_message_length=previous_message_length
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_length=current_message_type
                    )
                return

            total_pages = max(1, math.ceil(total_giveaways / ITEMS_PER_PAGE))
            offset = (current_page - 1) * ITEMS_PER_PAGE

            # Получаем завершенные розыгрыши для текущей страницы
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

            message_text = (
                "<tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Завершенные розыгрыши\n\n"
                f"Всего было завершено {total_giveaways} розыгрышей"
            )
            current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

            await bot.answer_callback_query(callback_query.id)
            sent_message = await send_message_auto(
                bot,
                chat_id=user_id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                media_type=None,
                previous_message_length=previous_message_length
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_length=current_message_type
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка в process_giveaway_history: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Упс Что-то пошло не так 😔")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            message_text = "⚠️ Упс Что-то пошло не так. Попробуйте снова"
            current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'
            sent_message = await send_message_auto(
                bot,
                chat_id=user_id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                media_type=None,
                previous_message_length=previous_message_length
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_length=current_message_type
                )

    @dp.callback_query(lambda c: c.data.startswith('view_completed_giveaway:'))
    async def process_view_completed_giveaway(callback_query: CallbackQuery, state: FSMContext):
        """Обработка просмотра детальной информации о завершенном розыгрыше с победителями."""
        global previous_message_length, last_message_id, previous_message_length
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id

        try:
            # Получаем данные из состояния
            data = await state.get_data()
            last_message_id = data.get('last_message_id', callback_query.message.message_id)
            previous_message_length = data.get('previous_message_length', 'short')

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
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="📜 Назад к списку", callback_data="giveaway_history")
                await bot.answer_callback_query(callback_query.id, text="🔍 Розыгрыш не найден или не завершен 😕")
                message_text = "🔍 Розыгрыш не найден или не завершен 😕"
                current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

                # Удаляем старое сообщение, если тип изменился
                if previous_message_length != current_message_type and last_message_id:
                    try:
                        await bot.delete_message(chat_id=user_id, message_id=last_message_id)
                        logger.info(
                            f"Удалено старое сообщение {last_message_id} в process_view_completed_giveaway (не найден)")
                    except Exception as e:
                        logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

                sent_message = await send_message_auto(
                    bot,
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=keyboard.as_markup(),
                    message_id=None if previous_message_length != current_message_type else last_message_id,
                    parse_mode='HTML',
                    image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                    media_type=None,
                    previous_message_length=previous_message_length
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_length=current_message_type
                    )
                return

            # Преобразуем результат в словарь
            columns = ['id', 'name', 'description', 'end_time', 'winner_count', 'media_type', 'media_file_id']
            giveaway = dict(zip(columns, giveaway))
            end_time_str = giveaway['end_time'].strftime("%d.%m.%Y") if giveaway['end_time'] else "Не указана"

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
                f"{giveaway['description'] or 'Описание отсутствует'}\n\n"
                f"<b>Дата завершения:</b> {end_time_str}\n"
                f"<b>Количество победителей:</b> {giveaway['winner_count']}\n\n"
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

            # Определяем image_url и media_type
            image_url = None
            media_type = None
            if giveaway['media_type'] and giveaway['media_file_id']:
                image_url = giveaway['media_file_id']
                media_type = giveaway['media_type']
                if not image_url.startswith('http'):
                    image_url = await get_file_url(bot, giveaway['media_file_id'])
            else:
                image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg'
                media_type = None

            # Определяем текущий тип сообщения
            current_message_type = media_type or (
                'photo' if count_length_with_custom_emoji(giveaway_info) <= 800 else 'image')

            # Удаляем старое сообщение, если тип изменился
            if previous_message_length != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=last_message_id)
                    logger.info(f"Удалено старое сообщение {last_message_id} в process_view_completed_giveaway")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

            await bot.answer_callback_query(callback_query.id)
            sent_message = await send_message_auto(
                bot,
                chat_id=user_id,
                text=giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_length != current_message_type else last_message_id,
                parse_mode='HTML',
                image_url=image_url,
                media_type=media_type,
                previous_message_length=previous_message_length
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_length=current_message_type
                )

        except Exception as e:
            logger.error(f"🚫 Ошибка при просмотре завершенного розыгрыша: {str(e)}")
            conn.rollback()
            await bot.answer_callback_query(callback_query.id, text="Упс Что-то сломалось 😔")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            message_text = "⚠️ Упс Что-то пошло не так. Попробуйте снова"
            current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

            # Удаляем старое сообщение, если тип изменился
            if previous_message_length != current_message_type and last_message_id:
                try:
                    await bot.delete_message(chat_id=user_id, message_id=last_message_id)
                    logger.info(
                        f"Удалено старое сообщение {last_message_id} в process_view_completed_giveaway (ошибка)")
                except Exception as e:
                    logger.warning(f"Не удалось удалить старое сообщение {last_message_id}: {str(e)}")

            sent_message = await send_message_auto(
                bot,
                chat_id=user_id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=None if previous_message_length != current_message_type else last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                media_type=None,
                previous_message_length=previous_message_length
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_length=current_message_type
                )
