from typing import List, Dict, Union
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
import json
import re

from utils import send_message_auto

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_selected_communities = {}
paid_users: Dict[int, str] = {}

# Константы для ограничения длины текста
MAX_CONGRATS_LENGTH = 1000  # Максимальная длина поздравительного сообщения

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

<b>Переменные</b>
Используйте их для автоматической подстановки данных:  
- <code>{win}</code> — количество победителей  
- <code>{data}</code> — дата и время, например, 30.03.2025 20:45 (по МСК)  

Примечание: Максимальная длина текста — 1000 символов. Максимум 100 кастомных эмодзи в сообщении.</blockquote>
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

# Функция для подсчета длины текста без HTML-тегов
def count_length_with_custom_emoji(text: str) -> int:
    # Удаляем HTML-теги
    tag_pattern = r'<[^>]+>'
    cleaned_text = re.sub(tag_pattern, '', text)

    # Подсчитываем базовую длину текста без тегов
    length = len(cleaned_text)

    # Добавляем фиксированную длину для переменных
    length += text.count('{win}') * (5 - len('{win}'))  # 5 символов минус длина самой строки "{win}"
    length += text.count('{data}') * (16 - len('{data}'))  # 16 символов минус длина самой строки "{data}"

    return length

def register_congratulations_messages(dp: Dispatcher, bot: Bot, conn, cursor):
    """Регистрация обработчиков для поздравительных сообщений."""

    @dp.callback_query(lambda c: c.data.startswith('message_winners:') or c.data.startswith('message_winners_page:'))
    async def process_message_winners(callback_query: types.CallbackQuery):
        """Обработка выбора места для редактирования поздравлений с пагинацией."""
        data_parts = callback_query.data.split(':')
        giveaway_id = data_parts[1]
        current_page = int(data_parts[2]) if len(data_parts) > 2 else 1
        ITEMS_PER_PAGE = 20

        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = cursor.fetchone()
        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, giveaway))

        winner_count = giveaway['winner_count']
        total_pages = max(1, (winner_count + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
        start_place = (current_page - 1) * ITEMS_PER_PAGE + 1
        end_place = min(current_page * ITEMS_PER_PAGE, winner_count)

        keyboard = InlineKeyboardBuilder()
        buttons = []
        buttons.append(InlineKeyboardButton(
            text="Общее поздравление",
            callback_data=f"edit_common_congrats:{giveaway_id}"
        ))
        for place in range(start_place, end_place + 1):
            buttons.append(InlineKeyboardButton(
                text=f"Место {place}",
                callback_data=f"congrats_message:{giveaway_id}:{place}"
            ))

        for button in buttons:
            keyboard.add(button)
        keyboard.adjust(1)

        if total_pages > 1:
            prev_page = current_page - 1 if current_page > 1 else total_pages
            next_page = current_page + 1 if current_page < total_pages else 1
            keyboard.row(
                InlineKeyboardButton(text="◀️", callback_data=f"message_winners_page:{giveaway_id}:{prev_page}"),
                InlineKeyboardButton(text=f"📄 {current_page}/{total_pages}", callback_data="ignore"),
                InlineKeyboardButton(text="▶️", callback_data=f"message_winners_page:{giveaway_id}:{next_page}")
            )

        keyboard.row(
            InlineKeyboardButton(text="◀️ Назад", callback_data=f"view_created_giveaway:{giveaway_id}")
        )

        message_text = (
            f"<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> "
            f"Выберите место победителя для редактирования поздравления или общее поздравление для всех победителей.\n"
            f"Отображаются места {start_place}-{end_place} из {winner_count}."
        )

        try:
            await send_message_auto(
                bot=bot,
                chat_id=callback_query.from_user.id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,  # Редактируем текущее сообщение
                parse_mode='HTML'
            )
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            # Если редактирование не удалось, отправляем новое сообщение
            await send_message_auto(
                bot=bot,
                chat_id=callback_query.from_user.id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        await callback_query.answer()

    def extract_message(obj: Union[str, Dict, List]) -> Union[str, None]:
        """Извлечение текста сообщения из объекта."""
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

    @dp.callback_query(lambda c: c.data.startswith('congrats_message:'))
    async def process_congrats_message(callback_query: types.CallbackQuery, state: FSMContext):
        """Обработка ввода поздравления для конкретного места."""
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

        message_text = (
            f"<tg-emoji emoji-id='5253742260054409879'>✉️</tg-emoji> Напишите своё поздравление для победителя, занявшего {place} место "
            f"(до {MAX_CONGRATS_LENGTH} символов).\n{FORMATTING_GUIDE}"
        )
        if existing_message:
            message_text += f"\n\nТекущее поздравление:\n{existing_message}"

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")

        try:
            # Используем message_id из callback_query для редактирования текущего сообщения
            sent_message = await send_message_auto(
                bot=bot,
                chat_id=callback_query.from_user.id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,  # Передаем ID текущего сообщения
                parse_mode='HTML'
            )
            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)
            else:
                logger.error("Failed to edit message with send_message_auto")
        except Exception as e:
            logger.error(f"Ошибка редактирования сообщения: {str(e)}")
            # Если редактирование не удалось, отправляем новое сообщение
            sent_message = await send_message_auto(
                bot=bot,
                chat_id=callback_query.from_user.id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_congrats_message)
    async def save_congrats_message(message: types.Message, state: FSMContext):
        """Сохранение поздравительного сообщения для конкретного места с проверкой длины."""
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        place = data['place']
        original_message_id = data.get('original_message_id')

        formatted_text = message.html_text if message.text else ""
        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_CONGRATS_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")
            error_message = (
                f"⚠️ Поздравление слишком длинное! Максимум {MAX_CONGRATS_LENGTH} символов, сейчас {text_length}. "
                f"Сократите текст!\n{FORMATTING_GUIDE}"
            )
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_auto(
                bot=bot,
                chat_id=message.chat.id,
                text=error_message,
                reply_markup=keyboard.as_markup(),
                message_id=original_message_id,
                parse_mode='HTML'
            )
            return

        try:
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

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Готово", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            updated_text = (
                f"Поздравление для {place} места обновлено:\n\n"
                f"{formatted_text}\n\n"
                f"Вы можете продолжить редактирование или завершить."
            )

            await send_message_auto(
                bot=bot,
                chat_id=message.chat.id,
                text=updated_text,
                reply_markup=keyboard.as_markup(),
                message_id=original_message_id,
                parse_mode='HTML'
            )
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        except Exception as e:
            logger.error(f"Ошибка сохранения поздравления: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")
            await send_message_auto(
                bot=bot,
                chat_id=message.chat.id,
                text="Произошла ошибка при сохранении поздравления. Попробуйте снова.",
                reply_markup=keyboard.as_markup(),
                message_id=original_message_id,
                parse_mode='HTML'
            )

    @dp.callback_query(lambda c: c.data == 'show_common_congrats')
    async def show_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        """Показать текущее общее поздравление."""
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            cursor.execute(
                "SELECT message, place FROM congratulations WHERE giveaway_id = %s",
                (giveaway_id,)
            )
            congratulations = cursor.fetchall()
            congratulations = [{'place': row[1], 'message': row[0]} for row in congratulations]

            if not congratulations:
                message_text = f"В настоящее время поздравления не установлены."
            else:
                congrats_dict = {item['place']: item['message'] for item in congratulations}
                if len(set(congrats_dict.values())) == 1:
                    common_message = next(iter(congrats_dict.values()))
                    message_text = f"Текущее общее поздравление:\n\n{common_message}"
                else:
                    message_text = f"В настоящее время общее поздравление не установлено. Поздравления различаются для разных мест."

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить общее поздравление", callback_data=f"edit_common_congrats:{giveaway_id}")
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            await bot.edit_message_text(
                chat_id=callback_query.from_user.id,
                message_id=callback_query.message.message_id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

        except Exception as e:
            logger.error(f"Ошибка получения общего поздравления: {str(e)}")
            await callback_query.answer("Произошла ошибка при получении общего поздравления.")

        await callback_query.answer()

    @dp.callback_query(lambda c: c.data.startswith('edit_common_congrats:'))
    async def edit_common_congrats(callback_query: types.CallbackQuery, state: FSMContext):
        """Редактирование общего поздравления."""
        giveaway_id = callback_query.data.split(':')[1]

        try:
            cursor.execute(
                "SELECT message FROM congratulations WHERE giveaway_id = %s",
                (giveaway_id,)
            )
            existing_messages = [row[0] for row in cursor.fetchall()]
            existing_message = existing_messages[0] if existing_messages and len(set(existing_messages)) == 1 else None

            await state.update_data(giveaway_id=giveaway_id)
            await state.set_state(GiveawayStates.waiting_for_common_congrats_message)

            message_text = (
                f"Напишите общее поздравление для всех победителей (до {MAX_CONGRATS_LENGTH} символов).\n"
                f"{FORMATTING_GUIDE}"
            )
            if existing_message:
                message_text += f"\n\nТекущее общее поздравление:\n{existing_message}"

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")

            sent_message = await send_message_auto(
                bot=bot,
                chat_id=callback_query.from_user.id,
                text=message_text,
                reply_markup=keyboard.as_markup(),
                message_id=callback_query.message.message_id,  # Редактируем текущее сообщение
                parse_mode='HTML'
            )
            if sent_message:
                await state.update_data(original_message_id=sent_message.message_id)

        except Exception as e:
            logger.error(f"Ошибка подготовки к редактированию общего поздравления: {str(e)}")
            await callback_query.answer("Произошла ошибка при подготовке к редактированию общего поздравления.")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_common_congrats_message)
    async def save_common_congrats_message(message: types.Message, state: FSMContext):
        """Сохранение общего поздравительного сообщения для всех мест с проверкой длины."""
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        original_message_id = data.get('original_message_id')

        formatted_text = message.html_text if message.text else ""
        text_length = count_length_with_custom_emoji(formatted_text)

        if text_length > MAX_CONGRATS_LENGTH:
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")
            error_message = (
                f"⚠️ Поздравление слишком длинное! Максимум {MAX_CONGRATS_LENGTH} символов, сейчас {text_length}. "
                f"Сократите текст!\n{FORMATTING_GUIDE}"
            )
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await send_message_auto(
                bot=bot,
                chat_id=message.chat.id,
                text=error_message,
                reply_markup=keyboard.as_markup(),
                message_id=original_message_id,
                parse_mode='HTML'
            )
            return

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

            for place in range(1, winner_count + 1):
                cursor.execute(
                    """
                    INSERT INTO congratulations (giveaway_id, place, message)
                    VALUES (%s, %s, %s)
                    """,
                    (giveaway_id, place, formatted_text)
                )

            conn.commit()

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Готово", callback_data=f"message_winners:{giveaway_id}")
            keyboard.adjust(1)

            success_message = (
                f"Общее поздравление сохранено и применено ко всем местам в розыгрыше.\n\n"
                f"Текст:\n{formatted_text}\n\n"
                f"Вы можете продолжить редактирование или завершить."
            )

            await send_message_auto(
                bot=bot,
                chat_id=message.chat.id,
                text=success_message,
                reply_markup=keyboard.as_markup(),
                message_id=original_message_id,
                parse_mode='HTML'
            )
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        except Exception as e:
            logger.error(f"Ошибка сохранения общего поздравления: {str(e)}")
            conn.rollback()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="◀️ Назад", callback_data=f"message_winners:{giveaway_id}")
            await send_message_auto(
                bot=bot,
                chat_id=message.chat.id,
                text="Произошла ошибка при сохранении поздравления. Попробуйте снова.",
                reply_markup=keyboard.as_markup(),
                message_id=original_message_id,
                parse_mode='HTML'
            )
