from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
import logging
from utils import send_message_auto, count_length_with_custom_emoji, strip_html_tags, get_file_url
import math

# Логирование
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def replace_variables(description, winner_count, end_time):
    """Заменяет переменные {win} и {data} в описании на актуальные значения."""
    formatted_end_time = end_time.strftime('%d.%m.%Y %H:%M (МСК)')
    description = description.replace('{win}', str(winner_count))
    description = description.replace('{data}', formatted_end_time)
    return description

def register_my_participations_handlers(dp: Dispatcher, bot: Bot, conn, cursor):
    @dp.callback_query(lambda c: c.data == 'my_participations' or c.data.startswith('my_participations_page:'))
    async def process_my_participations(callback_query: CallbackQuery, state: FSMContext):
        global last_message_id, previous_message_type
        user_id = callback_query.from_user.id
        ITEMS_PER_PAGE = 5
        current_page = int(callback_query.data.split(':')[1]) if callback_query.data.startswith(
            'my_participations_page:') else 1

        try:
            # Получаем данные из состояния
            data = await state.get_data()
            last_message_id = data.get('last_message_id', callback_query.message.message_id)
            previous_message_type = data.get('previous_message_type')

            # Получаем активные розыгрыши, в которых пользователь участвует
            cursor.execute(
                """
                SELECT COUNT(DISTINCT g.id) 
                FROM participations p
                JOIN giveaways g ON p.giveaway_id = g.id
                WHERE p.user_id = %s AND g.is_active = 'true' AND g.user_id != 1
                """,
                (user_id,)
            )
            active_participations = cursor.fetchone()[0]

            # Получаем завершенные розыгрыши, где пользователь выиграл
            cursor.execute(
                """
                SELECT COUNT(DISTINCT gw.giveaway_id)
                FROM giveaway_winners gw
                JOIN giveaways g ON gw.giveaway_id = g.id
                WHERE gw.user_id = %s AND g.is_completed = 'true'
                """,
                (user_id,)
            )
            won_participations = cursor.fetchone()[0]

            total_participations = active_participations + won_participations
            if total_participations == 0:
                message_text = "<tg-emoji emoji-id='5199885118214255386'>😔</tg-emoji> У вас нет активных участий или выигранных розыгрышей."
                current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Назад", callback_data="back_to_main_menu")
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
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type=current_message_type
                    )
                return

            total_pages = max(1, math.ceil(total_participations / ITEMS_PER_PAGE))
            offset = (current_page - 1) * ITEMS_PER_PAGE

            # Получаем активные розыгрыши
            cursor.execute(
                """
                SELECT DISTINCT g.id AS giveaway_id, g.user_id AS creator_user_id, g.name, g.description, 
                               g.end_time, g.media_type, g.media_file_id, 'active' AS status
                FROM participations p
                JOIN giveaways g ON p.giveaway_id = g.id
                WHERE p.user_id = %s AND g.is_active = 'true' AND g.user_id != 1
                ORDER BY g.end_time
                """,
                (user_id,)
            )
            active_results = cursor.fetchall()

            # Получаем завершенные розыгрыши, где пользователь выиграл
            cursor.execute(
                """
                SELECT DISTINCT g.id AS giveaway_id, g.user_id AS creator_user_id, g.name, g.description, 
                               g.end_time, g.media_type, g.media_file_id, 'won' AS status, gw.place
                FROM giveaway_winners gw
                JOIN giveaways g ON gw.giveaway_id = g.id
                WHERE gw.user_id = %s AND g.is_completed = 'true'
                ORDER BY g.end_time DESC
                """,
                (user_id,)
            )
            won_results = cursor.fetchall()

            # Объединяем результаты
            columns_active = [desc[0] for desc in cursor.description]
            active_participations_list = [dict(zip(columns_active, row)) for row in active_results]
            columns_won = [desc[0] for desc in cursor.description]
            won_participations_list = [dict(zip(columns_won, row)) for row in won_results]

            all_participations = active_participations_list + won_participations_list
            all_participations.sort(key=lambda x: x['end_time'], reverse=True)  # Сортировка по дате завершения

            # Пагинация
            current_participations = all_participations[offset:offset + ITEMS_PER_PAGE]

            # Генерируем клавиатуру с пагинацией
            keyboard = InlineKeyboardBuilder()

            # Добавляем кнопки для участий
            for participation in current_participations:
                clean_name = strip_html_tags(participation['name'])
                if participation['status'] == 'active':
                    button_text = clean_name
                else:  # 'won'
                    button_text = f"🏆 {clean_name} (Место {participation['place']})"
                if len(button_text) > 64:
                    button_text = button_text[:61] + "..."
                keyboard.row(types.InlineKeyboardButton(
                    text=button_text,
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

            message_text = f"<tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Ваши участия и победы"
            if total_pages > 1:
                message_text += f" (Страница {current_page} из {total_pages}):"
            else:
                message_text += ":"
            current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'

            await bot.answer_callback_query(callback_query.id)
            sent_message = await send_message_auto(
                bot,
                chat_id=callback_query.from_user.id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

        except Exception as e:
            logger.error(f"Error in process_my_participations: {str(e)}")
            message_text = "<tg-emoji emoji-id='5199885118214255386'>😔</tg-emoji> Произошла ошибка при получении ваших участий."
            current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data="back_to_main_menu")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении ваших участий.")
            sent_message = await send_message_auto(
                bot,
                chat_id=user_id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )

    @dp.callback_query(lambda c: c.data == "ignore")
    async def process_ignore(callback_query: CallbackQuery):
        await bot.answer_callback_query(callback_query.id)

    @dp.callback_query(lambda c: c.data.startswith('giveaway_'))
    async def process_giveaway_details(callback_query: CallbackQuery, state: FSMContext):
        global previous_message_type, last_message_id
        giveaway_id = callback_query.data.split('_')[1]
        user_id = callback_query.from_user.id
        try:
            # Получаем данные из состояния
            data = await state.get_data()
            last_message_id = data.get('last_message_id', callback_query.message.message_id)
            previous_message_type = data.get('previous_message_type')

            # Запрос к таблице giveaways, включая winner_count и is_active
            cursor.execute(
                """
                SELECT id, name, description, end_time, media_type, media_file_id, winner_count, is_active, is_completed
                FROM giveaways WHERE id = %s
                """,
                (giveaway_id,)
            )
            giveaway = cursor.fetchone()

            if not giveaway:
                message_text = "<tg-emoji emoji-id='5199885118214255386'>😔</tg-emoji> Розыгрыш не найден."
                current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="◀️ Назад к списку", callback_data="my_participations")
                await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
                sent_message = await send_message_auto(
                    bot,
                    chat_id=user_id,
                    text=message_text,
                    reply_markup=keyboard.as_markup(),
                    message_id=last_message_id,
                    parse_mode='HTML',
                    image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                    media_type=None,
                    previous_message_type=previous_message_type
                )
                if sent_message:
                    await state.update_data(
                        last_message_id=sent_message.message_id,
                        previous_message_type=current_message_type
                    )
                return

            # Преобразуем результат в словарь
            columns = [desc[0] for desc in cursor.description]
            giveaway = dict(zip(columns, giveaway))

            # Проверяем, выиграл ли пользователь и какое место
            place = None
            congrats_message = None
            if giveaway['is_completed']:
                cursor.execute(
                    """
                    SELECT place FROM giveaway_winners WHERE giveaway_id = %s AND user_id = %s
                    """,
                    (giveaway_id, user_id)
                )
                result = cursor.fetchone()
                if result:
                    place = result[0]
                    cursor.execute(
                        """
                        SELECT message FROM congratulations WHERE giveaway_id = %s AND place = %s
                        """,
                        (giveaway_id, place)
                    )
                    congrats_result = cursor.fetchone()
                    congrats_message = congrats_result[0] if congrats_result else "Поздравление не задано."

            # Заменяем переменные в description
            description_with_vars = replace_variables(
                giveaway['description'],
                giveaway['winner_count'],
                giveaway['end_time']
            )

            # Формируем текст с превью медиа
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

            giveaway_info = (
                f"<a href=\"{image_url}\"> </a>"
                f"{description_with_vars}\n\n"
                f"<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> <b>Дата завершения:</b> {giveaway['end_time'].strftime('%d.%m.%Y %H:%M')} по МСК"
            )

            if place is not None:
                giveaway_info += (
                    f"\n\n<tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji> <b>Вы выиграли {place} место</b>"
                    f"\n<tg-emoji emoji-id='5253742260054409879'>✉️</tg-emoji> <b>Сообщение для вас:</b>\n{congrats_message}"
                )

            current_message_type = 'photo' if count_length_with_custom_emoji(giveaway_info) <= 800 else 'image'

            keyboard = InlineKeyboardBuilder()
            if giveaway['is_active']:
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

            sent_message = await send_message_auto(
                bot,
                chat_id=callback_query.message.chat.id,
                text=giveaway_info,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
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
            logger.error(f"Error in process_giveaway_details: {str(e)}")
            message_text = "<tg-emoji emoji-id='5199885118214255386'>😔</tg-emoji> Произошла ошибка при получении информации о розыгрыше."
            current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 800 else 'image'
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад к списку", callback_data="my_participations")
            try:
                await bot.answer_callback_query(callback_query.id,
                                                text="Произошла ошибка при получении информации о розыгрыше.")
            except TelegramBadRequest:
                logger.warning("Failed to answer callback query due to timeout")
            sent_message = await send_message_auto(
                bot,
                chat_id=user_id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=last_message_id,
                parse_mode='HTML',
                image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
                media_type=None,
                previous_message_type=previous_message_type
            )
            if sent_message:
                await state.update_data(
                    last_message_id=sent_message.message_id,
                    previous_message_type=current_message_type
                )
