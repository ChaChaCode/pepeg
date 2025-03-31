from typing import List, Dict, Union
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image
import json

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_selected_communities = {}
paid_users: Dict[int, str] = {}

FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>- Цитата
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>

Примечание: Максимальное количество кастомных эмодзи, которое может отображать Telegram в одном сообщении, ограничено 100 эмодзи.</blockquote>
"""

# States for the FSM
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

def register_congratulations_messages(dp: Dispatcher, bot: Bot, conn, cursor):
    @dp.callback_query(lambda c: c.data.startswith('message_winners:'))
    async def process_message_winners(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]

        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = cursor.fetchone()
        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, giveaway))

        winner_count = giveaway['winner_count']

        # Формируем клавиатуру с ограничением до 5 мест
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
        for place in range(1, min(winner_count, 5) + 1):  # Показываем до 5 мест
            keyboard.button(text=f"Место {place}", callback_data=f"congrats_message:{giveaway_id}:{place}")
        if winner_count > 5:  # Если мест больше 5, добавляем "Показать еще"
            keyboard.button(text="Показать еще", callback_data=f"show_more_winners_created:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        message_text = f"<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> Выберите место победителя для редактирования поздравления или общее поздравление для всех победителей."

        await send_message_with_image(
            bot,
            callback_query.from_user.id,
            message_text,
            reply_markup=keyboard.as_markup(),
            message_id=callback_query.message.message_id,
            parse_mode='HTML'
        )

    def extract_message(obj: Union[str, Dict, List]) -> Union[str, None]:
        if isinstance(obj, str):
            try:
                parsed = json.loads(obj)
                return extract_message(parsed)
            except json.JSONDecodeError:
                return obj.strip()

        if isinstance(obj, dict):
            if 'message' in obj:
                return obj['message']
            for value in obj.values():
                result = extract_message(value)
                if result:
                    return result

        if isinstance(obj, list):
            if obj and isinstance(obj[0], dict) and 'message' in obj[0]:
                return obj[0]['message']

        return None

    @dp.callback_query(lambda c: c.data.startswith('show_more_winners_created:'))
    async def process_show_more_winners_created(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]

        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = cursor.fetchone()
        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, giveaway))

        winner_count = giveaway['winner_count']

        # Формируем клавиатуру с отображением всех мест и кнопкой "Свернуть"
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
        for place in range(1, winner_count + 1):  # Показываем все места
            keyboard.button(text=f"Место {place}", callback_data=f"congrats_message:{giveaway_id}:{place}")
        keyboard.button(text="Свернуть", callback_data=f"collapse_winners_created:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        message_text = f"<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> Выберите место победителя для редактирования поздравления или общее поздравление для всех победителей."

        try:
            # Пробуем редактировать подпись, так как send_message_with_image отправляет медиа
            await bot.edit_message_caption(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                caption=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error editing message caption: {str(e)}")
            try:
                # Если редактирование не удалось, отправляем новое сообщение
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            except Exception as e2:
                logger.error(f"Error sending fallback message: {str(e2)}")
                await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при обновлении сообщения.")

    @dp.callback_query(lambda c: c.data.startswith('collapse_winners_created:'))
    async def process_collapse_winners_created(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]

        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = cursor.fetchone()
        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, giveaway))

        winner_count = giveaway['winner_count']

        # Формируем клавиатуру с возвратом к 5 местам
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
        for place in range(1, min(winner_count, 5) + 1):  # Показываем до 5 мест
            keyboard.button(text=f"Место {place}", callback_data=f"congrats_message:{giveaway_id}:{place}")
        if winner_count > 5:  # Если мест больше 5, добавляем "Показать еще"
            keyboard.button(text="Показать еще", callback_data=f"show_more_winners_created:{giveaway_id}")
        keyboard.button(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        keyboard.adjust(1)

        message_text = f"<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> Выберите место победителя для редактирования поздравления или общее поздравление для всех победителей."

        try:
            # Пробуем редактировать подпись
            await bot.edit_message_caption(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                caption=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Error editing message caption: {str(e)}")
            try:
                # Если редактирование не удалось, отправляем новое сообщение
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
            except Exception as e2:
                logger.error(f"Error sending fallback message: {str(e2)}")
                await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при обновлении сообщения.")


    @dp.callback_query(lambda c: c.data.startswith('congrats_message:'))
    async def process_congrats_message(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id, place = callback_query.data.split(':')[1:]

        existing_message = None
        try:
            cursor.execute(
                "SELECT message FROM congratulations WHERE giveaway_id = %s AND place = %s",
                (giveaway_id, place)
            )
            result = cursor.fetchone()
            if result:
                existing_message = result[0]
            logger.info(f"Extracted message: {existing_message}")
        except Exception as e:
            logger.error(f"Error fetching congratulation for place {place}: {str(e)}")

        await state.update_data(giveaway_id=giveaway_id, place=place)
        await state.set_state(GiveawayStates.waiting_for_congrats_message)

        message_text = f"<tg-emoji emoji-id='5253742260054409879'>✉️</tg-emoji> Напишите своё поздравление для победителя, занявшего {place} место."
        if existing_message:
            message_text += f"\n\nТекущее поздравление:\n{existing_message}"

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")

        try:
            sent_message = await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )

            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)
            else:
                logger.error("Failed to send message with image")
        except Exception as e:
            logger.error(f"Error in send_message_with_image: {str(e)}")
            try:
                sent_message = await bot.send_message(
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
                await state.update_data(original_message_id=sent_message.message_id)
            except Exception as e:
                logger.error(f"Error sending fallback message: {str(e)}")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_congrats_message)
    async def save_congrats_message(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        place = data['place']
        original_message_id = data.get('original_message_id')

        formatted_text = message.html_text if message.text else ""

        try:
            # Save the new congratulation message
            cursor.execute(
                "DELETE FROM congratulations WHERE giveaway_id = %s AND place = %s",
                (giveaway_id, place)
            )
            cursor.execute(
                """
                INSERT INTO congratulations (giveaway_id, place, message)
                VALUES (%s, %s, %s)
                """,
                (giveaway_id, place, formatted_text)
            )
            conn.commit()

            # Не очищаем состояние, чтобы пользователь мог продолжить редактирование
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Готово", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            updated_text = (
                f"Поздравление для {place} места обновлено:\n\n"
                f"{formatted_text}\n\n"
                f"Вы можете продолжить редактирование или завершить."
            )

            if original_message_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=message.chat.id,
                        message_id=original_message_id,
                        caption=updated_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                except Exception as edit_error:
                    logger.error(f"Error editing message: {str(edit_error)}")
                    new_message = await send_message_with_image(
                        bot,
                        message.chat.id,
                        updated_text,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                    await state.update_data(original_message_id=new_message.message_id)
            else:
                new_message = await send_message_with_image(
                    bot,
                    message.chat.id,
                    updated_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
                await state.update_data(original_message_id=new_message.message_id)

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        except Exception as e:
            logger.error(f"Error saving congratulation message: {str(e)}")
            conn.rollback()
            await message.reply("Произошла ошибка при сохранении поздравления. Пожалуйста, попробуйте еще раз.")

    @dp.callback_query(lambda c: c.data == 'show_common_congrats')
    async def show_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            cursor.execute(
                "SELECT message, place FROM congratulations WHERE giveaway_id = %s",
                (giveaway_id,)
            )
            congratulations = cursor.fetchall()
            congratulations = [{'place': row[1], 'message': row[0]} for row in congratulations]
            logger.info(f"Fetched congratulations: {json.dumps(congratulations, default=str)}")

            if not congratulations:
                message_text = f"В настоящее время поздравления не установлены."
            else:
                congrats_dict = {item['place']: item['message'] for item in congratulations}
                logger.info(f"Parsed congratulations: {congrats_dict}")

                if len(set(congrats_dict.values())) == 1:
                    common_message = next(iter(congrats_dict.values()))
                    message_text = f"Текущее общее поздравление:\n\n{common_message}"
                else:
                    message_text = f"В настоящее время общее поздравление не установлено. Поздравления различаются для разных мест."

            logger.info(f"Final message_text: {message_text}")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )

        except Exception as e:
            logger.error(f"Error fetching common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при получении общего поздравления.")

        await callback_query.answer()

    @dp.callback_query(lambda c: c.data.startswith('edit_common_congrats:'))
    async def edit_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        logger.info(f"Editing common congratulation for giveaway {giveaway_id}")

        try:
            cursor.execute(
                "SELECT message FROM congratulations WHERE giveaway_id = %s",
                (giveaway_id,)
            )
            existing_messages = [row[0] for row in cursor.fetchall()]

            if existing_messages and len(set(existing_messages)) == 1:
                existing_message = existing_messages[0]
            else:
                existing_message = None

            await state.update_data(giveaway_id=giveaway_id)
            await state.set_state(GiveawayStates.waiting_for_common_congrats_message)

            message_text = f"Напишите общее поздравление для всех победителей."
            if existing_message:
                message_text += f"\n\nТекущее общее поздравление:\n{existing_message}"

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")

            sent_message = await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,
                parse_mode='HTML'
            )

            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)

        except Exception as e:
            logger.error(f"Error preparing to edit common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при подготовке к редактированию общего поздравления.")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_common_congrats_message)
    async def save_common_congrats_message(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        original_message_id = data.get('original_message_id')

        formatted_text = message.html_text if message.text else ""

        try:
            cursor.execute(
                "SELECT winner_count FROM giveaways WHERE id = %s",
                (giveaway_id,)
            )
            winner_count = cursor.fetchone()[0]

            cursor.execute(
                "DELETE FROM congratulations WHERE giveaway_id = %s",
                (giveaway_id,)
            )

            congratulations = []
            for place in range(1, winner_count + 1):
                cursor.execute(
                    """
                    INSERT INTO congratulations (giveaway_id, place, message)
                    VALUES (%s, %s, %s)
                    """,
                    (giveaway_id, place, formatted_text)
                )

            conn.commit()

            # Не очищаем состояние, чтобы пользователь мог продолжить редактирование
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Готово", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            success_message = (
                f"Общее поздравление сохранено и применено ко всем местам в розыгрыше.\n\n"
                f"Текст:\n{formatted_text}\n\n"
                f"Вы можете продолжить редактирование или завершить."
            )

            if original_message_id:
                try:
                    await bot.edit_message_caption(
                        chat_id=message.chat.id,
                        message_id=original_message_id,
                        caption=success_message,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                except Exception as edit_error:
                    logger.error(f"Error editing message: {str(edit_error)}")
                    new_message = await send_message_with_image(
                        bot,
                        message.chat.id,
                        success_message,
                        reply_markup=keyboard.as_markup(),
                        parse_mode='HTML'
                    )
                    await state.update_data(original_message_id=new_message.message_id)
            else:
                new_message = await send_message_with_image(
                    bot,
                    message.chat.id,
                    success_message,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
                await state.update_data(original_message_id=new_message.message_id)

            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        except Exception as e:
            logger.error(f"Error saving common congratulation message: {str(e)}")
            conn.rollback()
            await message.reply("Произошла ошибка при сохранении поздравлений. Пожалуйста, попробуйте еще раз.")
