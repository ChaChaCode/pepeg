from typing import List, Dict, Union
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from supabase import create_client, Client
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image
import json
from postgrest import APIResponse

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

user_selected_communities = {}
paid_users: Dict[int, str] = {}

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
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>
</blockquote>
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
    waiting_for_congrats_message_active = State()
    waiting_for_common_congrats_message_active = State()
    waiting_for_edit_name = State()
    waiting_for_edit_description = State()
    waiting_for_edit_winner_count = State()
    creating_giveaway = State()
    binding_communities = State()

def register_congratulations_messages_active(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data.startswith('message_winners_active:'))
    async def process_message_winners_active(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]

        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if not giveaway:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        winner_count = giveaway['winner_count']

        keyboard = InlineKeyboardBuilder()
        for place in range(1, winner_count + 1):
            keyboard.button(text=f"Место {place}", callback_data=f"congrats_message_active:{giveaway_id}:{place}")
        keyboard.button(text="Общее поздравление", callback_data=f"edit_common_congrats_active:{giveaway_id}")
        keyboard.button(text="Назад", callback_data=f"view_active_giveaway:{giveaway_id}")
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

    def extract_message(obj: Union[str, Dict, List, APIResponse]) -> Union[str, None]:
        if isinstance(obj, APIResponse):
            return extract_message(obj.data)

        if isinstance(obj, str):
            try:
                parsed = json.loads(obj)
                return extract_message(parsed)
            except json.JSONDecodeError:
                return obj.strip()

        if isinstance(obj, dict):
            if 'data' in obj and isinstance(obj['data'], list):
                if obj['data'] and 'message' in obj['data'][0]:
                    return obj['data'][0]['message']
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

    @dp.callback_query(lambda c: c.data.startswith('congrats_message_active:'))
    async def process_congrats_message_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id, place = callback_query.data.split(':')[1:]

        existing_message = None
        try:
            response = supabase.table('congratulations').select('message').eq('giveaway_id', giveaway_id).eq('place',
                                                                                                             place).execute()
            logging.info(f"Supabase response: {json.dumps(response, default=str)}")
            existing_message = extract_message(response)
            logging.info(f"Extracted message: {existing_message}")
        except Exception as e:
            logging.error(f"Error fetching congratulation for place {place}: {str(e)}")

        await state.update_data(giveaway_id=giveaway_id, place=place)
        await state.set_state(GiveawayStates.waiting_for_congrats_message_active)

        message_text = f"<tg-emoji emoji-id='5253742260054409879'>✉️</tg-emoji> Напишите своё поздравление для победителя, занявшего {place} место."
        if existing_message:
            message_text += f"\n\nТекущее поздравление:\n{existing_message}"

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data=f"message_winners_active:{giveaway_id}")

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
                logging.error("Failed to send message with image")
        except Exception as e:
            logging.error(f"Error in send_message_with_image: {str(e)}")
            try:
                sent_message = await bot.send_message(
                    callback_query.from_user.id,
                    message_text,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
                await state.update_data(original_message_id=sent_message.message_id)
            except Exception as e:
                logging.error(f"Error sending fallback message: {str(e)}")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_congrats_message_active)
    async def save_congrats_message_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        place = data['place']
        original_message_id = data.get('original_message_id')

        formatted_text = message.html_text if message.text else ""

        try:
            # Save the new congratulation message
            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).eq('place', place).execute()
            supabase.table('congratulations').insert({
                'giveaway_id': giveaway_id,
                'place': place,
                'message': formatted_text
            }).execute()

            # Не очищаем состояние, чтобы пользователь мог продолжить редактирование
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Готово", callback_data=f"message_winners_active:{giveaway_id}")
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
                    logging.error(f"Error editing message: {str(edit_error)}")
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
            logging.error(f"Error saving congratulation message: {str(e)}")
            await message.reply("Произошла ошибка при сохранении поздравления. Пожалуйста, попробуйте еще раз.")

    @dp.callback_query(lambda c: c.data == 'show_common_congrats_active')
    async def show_common_congrats_active(callback_query: types.CallbackQuery, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']

        try:
            response = supabase.table('congratulations').select('message', 'place').eq('giveaway_id',
                                                                                       giveaway_id).execute()
            logging.info(f"Fetched congratulations: {json.dumps(response.data, default=str)}")

            if not response.data:
                message_text = f"В настоящее время поздравления не установлены."
            else:
                congratulations = {item['place']: item['message'] for item in response.data if
                                   'message' in item and 'place' in item}
                logging.info(f"Parsed congratulations: {congratulations}")

                if len(set(congratulations.values())) == 1:
                    common_message = next(iter(congratulations.values()))
                    message_text = f"Текущее общее поздравление:\n\n{common_message}"
                else:
                    message_text = f"В настоящее время общее поздравление не установлено. Поздравления различаются для разных мест."

            logging.info(f"Final message_text: {message_text}")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Изменить общее поздравление", callback_data=f"edit_common_congrats_active:{giveaway_id}")
            keyboard.button(text="Назад", callback_data=f"message_winners_active:{giveaway_id}")
            keyboard.adjust(1)

            await send_message_with_image(
                bot,
                callback_query.from_user.id,
                message_text,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

        except Exception as e:
            logging.error(f"Error fetching common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при получении общего поздравления.")

        await callback_query.answer()

    @dp.callback_query(lambda c: c.data.startswith('edit_common_congrats_active:'))
    async def edit_common_congrats_active(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        logging.info(f"Editing common congratulation for giveaway {giveaway_id}")

        try:
            response = supabase.table('congratulations').select('message').eq('giveaway_id', giveaway_id).execute()
            existing_messages = [item['message'] for item in response.data if 'message' in item]

            if existing_messages and len(set(existing_messages)) == 1:
                existing_message = existing_messages[0]
            else:
                existing_message = None

            await state.update_data(giveaway_id=giveaway_id)
            await state.set_state(GiveawayStates.waiting_for_common_congrats_message_active)

            message_text = f"Напишите общее поздравление для всех победителей."
            if existing_message:
                message_text += f"\n\nТекущее общее поздравление:\n{existing_message}"

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад", callback_data=f"message_winners_active:{giveaway_id}")

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
            logging.error(f"Error preparing to edit common congratulation: {str(e)}")
            await callback_query.answer("Произошла ошибка при подготовке к редактированию общего поздравления.")

        await callback_query.answer()

    @dp.message(GiveawayStates.waiting_for_common_congrats_message_active)
    async def save_common_congrats_message_active(message: types.Message, state: FSMContext):
        data = await state.get_data()
        giveaway_id = data['giveaway_id']
        original_message_id = data.get('original_message_id')

        formatted_text = message.html_text if message.text else ""

        try:
            giveaway_response = supabase.table('giveaways').select('winner_count').eq('id',
                                                                                      giveaway_id).single().execute()
            winner_count = giveaway_response.data['winner_count']

            supabase.table('congratulations').delete().eq('giveaway_id', giveaway_id).execute()

            congratulations = []
            for place in range(1, winner_count + 1):
                congratulations.append({
                    'giveaway_id': giveaway_id,
                    'place': place,
                    'message': formatted_text
                })

            supabase.table('congratulations').insert(congratulations).execute()

            # Не очищаем состояние, чтобы пользователь мог продолжить редактирование
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Готово", callback_data=f"message_winners_active:{giveaway_id}")
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
                    logging.error(f"Error editing message: {str(edit_error)}")
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
            logging.error(f"Error saving common congratulation message: {str(e)}")
            await message.reply("Произошла ошибка при сохранении поздравлений. Пожалуйста, попробуйте еще раз.")
