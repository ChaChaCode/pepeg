from aiogram import Dispatcher, Bot, types
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramBadRequest
from supabase import Client
from datetime import datetime, timedelta
import logging
from utils import send_message_with_image

def register_my_participations_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'my_participations')
    async def process_my_participations(callback_query: CallbackQuery):
        user_id = callback_query.from_user.id
        try:
            response = supabase.table('participations').select('*, giveaways(*)').eq('user_id', user_id).execute()
            participations = response.data

            if not participations:
                await bot.answer_callback_query(callback_query.id, text="Вы не участвуете ни в одном розыгрыше.")
                return

            keyboard = InlineKeyboardBuilder()
            for participation in participations:
                giveaway = participation['giveaways']
                keyboard.button(text=giveaway['name'], callback_data=f"giveaway_{giveaway['id']}")
            keyboard.button(text="Назад", callback_data="back_to_main_menu")
            keyboard.adjust(1)

            await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                          message_id=callback_query.message.message_id,
                                          text="Список розыгрышей, в которых вы участвуете:",
                                          reply_markup=keyboard.as_markup())
        except Exception as e:
            logging.error(f"Error in process_my_participations: {str(e)}")
            await bot.answer_callback_query(callback_query.id, text="Произошла ошибка при получении ваших участий.")

    @dp.callback_query(lambda c: c.data.startswith('giveaway_'))
    async def process_giveaway_details(callback_query: CallbackQuery):
        giveaway_id = callback_query.data.split('_')[1]
        try:
            response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
            giveaway = response.data

            if not giveaway:
                await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
                return

            giveaway_info = (f"Название: {giveaway['name']}\n"
                             f"Описание: {giveaway['description']}\n"
                             f"Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}")

            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="Назад к списку", callback_data="my_participations")

            try:
                await bot.answer_callback_query(callback_query.id)
            except TelegramBadRequest as e:
                if "query is too old" in str(e):
                    logging.warning(f"Callback query is too old: {e}")
                else:
                    raise

            if giveaway['media_type'] and giveaway['media_file_id']:
                try:
                    if giveaway['media_type'] == 'photo':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaPhoto(media=giveaway['media_file_id'], caption=giveaway_info),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'gif':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaAnimation(media=giveaway['media_file_id'], caption=giveaway_info),
                            reply_markup=keyboard.as_markup()
                        )
                    elif giveaway['media_type'] == 'video':
                        await bot.edit_message_media(
                            chat_id=callback_query.message.chat.id,
                            message_id=callback_query.message.message_id,
                            media=types.InputMediaVideo(media=giveaway['media_file_id'], caption=giveaway_info),
                            reply_markup=keyboard.as_markup()
                        )
                except TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logging.warning(f"Message to edit not found: {e}")
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
                        message_id=callback_query.message.message_id
                    )
                except TelegramBadRequest as e:
                    if "message to edit not found" in str(e):
                        logging.warning(f"Message to edit not found: {e}")
                        await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                    else:
                        raise

        except Exception as e:
            logging.error(f"Error in process_giveaway_details: {str(e)}")
            try:
                await bot.answer_callback_query(callback_query.id,
                                                text="Произошла ошибка при получении информации о розыгрыше.")
            except TelegramBadRequest:
                logging.warning("Failed to answer callback query due to timeout")

            await bot.send_message(
                chat_id=callback_query.from_user.id,
                text="Произошла ошибка при получении информации о розыгрыше. Пожалуйста, попробуйте еще раз."
            )

    async def send_new_giveaway_message(chat_id, giveaway, g_info, keyboard):
        if giveaway['media_type'] and giveaway['media_file_id']:
            media_type = giveaway['media_type']
            if media_type == 'photo':
                await bot.send_photo(chat_id, giveaway['media_file_id'], caption=g_info,
                                     reply_markup=keyboard.as_markup())
            elif media_type == 'gif':
                await bot.send_animation(chat_id, giveaway['media_file_id'], caption=g_info,
                                         reply_markup=keyboard.as_markup())
            elif media_type == 'video':
                await bot.send_video(chat_id, giveaway['media_file_id'], caption=g_info,
                                     reply_markup=keyboard.as_markup())
        else:
            await send_message_with_image(bot, chat_id, g_info, reply_markup=keyboard.as_markup())

    return send_new_giveaway_message