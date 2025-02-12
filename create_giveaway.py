from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from supabase import Client
from datetime import datetime
import pytz
from utils import send_message_with_image


class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_media_choice = State()
    waiting_for_media_upload = State()
    waiting_for_end_time = State()
    waiting_for_winner_count = State()


async def save_giveaway(supabase: Client, user_id: int, name: str, description: str, end_time: str, winner_count: int,
                        media_type: str = None, media_file_id: str = None):
    moscow_tz = pytz.timezone('Europe/Moscow')
    end_time_dt = datetime.strptime(end_time, "%d.%m.%Y %H:%M")
    end_time_tz = moscow_tz.localize(end_time_dt)

    giveaway_data = {
        'user_id': user_id,
        'name': name,
        'description': description,
        'end_time': end_time_tz.isoformat(),
        'winner_count': winner_count,
        'is_active': False,
        'media_type': media_type,
        'media_file_id': media_file_id
    }

    try:
        response = supabase.table('giveaways').insert(giveaway_data).execute()
        if response.data:
            giveaway_id = response.data[0]['id']
            return True, giveaway_id
        else:
            return False, None
    except Exception as e:
        print(f"Error saving giveaway: {str(e)}")
        return False, None


def register_create_giveaway_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'create_giveaway')
    async def process_create_giveaway(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.waiting_for_name)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
        await send_message_with_image(bot, callback_query.from_user.id, "Напишите название розыгрыша",
                                      reply_markup=keyboard, message_id=callback_query.message.message_id)
        await state.update_data(last_message_id=callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_name)
    async def process_name(message: types.Message, state: FSMContext):
        await state.update_data(name=message.text)
        await state.set_state(GiveawayStates.waiting_for_description)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]])
        data = await state.get_data()
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await send_message_with_image(bot, message.chat.id, "Напишите описание для розыгрыша", reply_markup=keyboard,
                                      message_id=data['last_message_id'])

    @dp.message(GiveawayStates.waiting_for_description)
    async def process_description(message: types.Message, state: FSMContext):
        await state.update_data(description=message.text)
        await state.set_state(GiveawayStates.waiting_for_media_choice)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Да", callback_data="add_media")
        keyboard.button(text="Пропустить", callback_data="skip_media")
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        keyboard.adjust(2, 1)
        data = await state.get_data()
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        await send_message_with_image(bot, message.chat.id, "Хотите добавить фото, GIF или видео?",
                                      reply_markup=keyboard.as_markup(), message_id=data['last_message_id'])

    @dp.callback_query(lambda c: c.data in ["add_media", "skip_media"])
    async def process_media_choice(callback_query: CallbackQuery, state: FSMContext):
        await bot.answer_callback_query(callback_query.id)
        if callback_query.data == "add_media":
            await state.set_state(GiveawayStates.waiting_for_media_upload)
            await send_message_with_image(bot, callback_query.from_user.id,
                                          "Пожалуйста, отправьте фото, GIF или видео.",
                                          reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                              [InlineKeyboardButton(text="В меню",
                                                                    callback_data="back_to_main_menu")]]),
                                          message_id=callback_query.message.message_id)
        else:
            await process_end_time_request(callback_query.from_user.id, state, callback_query.message.message_id)

    @dp.message(GiveawayStates.waiting_for_media_upload)
    async def process_media_upload(message: types.Message, state: FSMContext):
        if message.photo:
            file_id = message.photo[-1].file_id
            media_type = 'photo'
        elif message.animation:
            file_id = message.animation.file_id
            media_type = 'gif'
        elif message.video:
            file_id = message.video.file_id
            media_type = 'video'
        else:
            await message.reply("Пожалуйста, отправьте фото, GIF или видео.")
            return

        await state.update_data(media_type=media_type, media_file_id=file_id)
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        data = await state.get_data()
        previous_message_id = data.get('last_message_id')
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="В меню", callback_data="back_to_main_menu")
        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
Укажите новую дату завершения розыгрыша в формате ДД.ММ.ГГГГ ЧЧ:ММ

Текущая дата и время: <code>{current_time}</code>
"""

        if previous_message_id:
            await send_message_with_image(
                bot,
                message.chat.id,
                html_message,
                reply_markup=keyboard.as_markup(),
                message_id=previous_message_id,
                parse_mode='HTML'
            )
        else:
            new_message = await send_message_with_image(
                bot,
                message.chat.id,
                f"Медиафайл успешно добавлен к розыгрышу.\n\n{html_message}",
                parse_mode='HTML'
            )
            await state.update_data(last_message_id=new_message.message_id)

        await state.set_state(GiveawayStates.waiting_for_end_time)

    async def process_end_time_request(chat_id: int, state: FSMContext, message_id: int = None):
        await state.set_state(GiveawayStates.waiting_for_end_time)
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="В меню", callback_data="back_to_main_menu")

        current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
        html_message = f"""
Укажите новую дату завершения розыгрыша в формате ДД.ММ.ГГГГ ЧЧ:ММ

Текущая дата и время: <code>{current_time}</code>
"""

        await send_message_with_image(
            bot,
            chat_id,
            html_message,
            reply_markup=keyboard.as_markup(),
            message_id=message_id,
            parse_mode='HTML'
        )

    @dp.message(GiveawayStates.waiting_for_end_time)
    async def process_end_time(message: types.Message, state: FSMContext):
        try:
            new_end_time = datetime.strptime(message.text, "%d.%m.%Y %H:%M")
            await state.update_data(end_time=message.text)
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await state.set_state(GiveawayStates.waiting_for_winner_count)
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Укажите количество победителей",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        except ValueError:
            # Delete the message with incorrect date
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")

            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""
Вы ввели неправильный формат даты. Сообщение удалено.
Укажите дату завершения розыгрыша (текущая дата и время: <code>{current_time}</code>)
"""

            await bot.edit_message_text(
                html_message,
                chat_id=message.chat.id,
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        try:
            winner_count = int(message.text)
            data = await state.get_data()
            success, giveaway_id = await save_giveaway(
                supabase,
                message.from_user.id,
                data['name'],
                data['description'],
                data['end_time'],
                winner_count,
                data.get('media_type'),
                data.get('media_file_id')
            )

            if success:
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Розыгрыш успешно создан и сохранен!",
                    message_id=data.get('last_message_id')
                )
                # Переход к созданным розыгрышам
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="К созданным розыгрышам", callback_data="created_giveaways")
                keyboard.button(text="В главное меню", callback_data="back_to_main_menu")
                keyboard.adjust(1)
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Что вы хотите сделать дальше?",
                    reply_markup=keyboard.as_markup(),
                    message_id=data.get('last_message_id')
                )
            else:
                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Произошла ошибка при сохранении розыгрыша. Пожалуйста, попробуйте еще раз.",
                    message_id=data.get('last_message_id')
                )
        except ValueError:
            data = await state.get_data()
            await send_message_with_image(
                bot,
                message.chat.id,
                "Пожалуйста, введите целое число.",
                message_id=data.get('last_message_id')
            )
        finally:
            await state.clear()
