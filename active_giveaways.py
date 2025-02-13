from utils import end_giveaway, send_message_with_image
import logging
from aiogram import Bot, Dispatcher, types
from datetime import datetime, timedelta
from supabase import Client
from aiogram.utils.keyboard import InlineKeyboardBuilder
import aiogram.exceptions

def register_active_giveaways_handlers(dp: Dispatcher, bot: Bot, supabase: Client):
    @dp.callback_query(lambda c: c.data == 'active_giveaways')
    async def process_active_giveaways(callback_query: types.CallbackQuery):
        user_id = callback_query.from_user.id
        try:
            response = supabase.table('giveaways').select('*').eq('is_active', True).eq('user_id', user_id).order(
                'end_time').execute()
            giveaways = response.data

            if not giveaways:
                await bot.answer_callback_query(callback_query.id, text="У вас нет активных розыгрышей.")
                return

            keyboard = InlineKeyboardBuilder()
            for giveaway in giveaways:
                keyboard.button(text=giveaway['name'], callback_data=f"view_active_giveaway:{giveaway['id']}")
            keyboard.button(text="Назад", callback_data="back_to_main_menu")
            keyboard.adjust(1)

            await bot.answer_callback_query(callback_query.id)
            await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                          message_id=callback_query.message.message_id,
                                          text="Выберите активный розыгрыш:",
                                          reply_markup=keyboard.as_markup())
        except Exception as e:
            logging.error(f"Error in process_active_giveaways: {str(e)}")
            await bot.answer_callback_query(callback_query.id,
                                            text="Произошла ошибка при получении активных розыгрышей.")

    @dp.callback_query(lambda c: c.data.startswith('view_active_giveaway:'))
    async def process_view_active_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()

        if not response.data:
            await bot.answer_callback_query(callback_query.id, text="Розыгрыш не найден.")
            return

        giveaway = response.data

        # Получение количества участников
        participants_response = supabase.table('participations').select('count').eq('giveaway_id',
                                                                                    giveaway_id).execute()
        participants_count = participants_response.data[0]['count']

        # Add the participants count to the giveaway_info
        giveaway_info = f"""
Активный розыгрыш:

Название: {giveaway['name']}
Описание: {giveaway['description']}

Дата завершения: {(datetime.fromisoformat(giveaway['end_time']) + timedelta(hours=3)).strftime('%d.%m.%Y %H:%M')}
Количество победителей: {giveaway['winner_count']}
Участвуют: {participants_count}
        """

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Принудительное завершение", callback_data=f"force_end_giveaway:{giveaway_id}")
        keyboard.button(text="Назад к списку", callback_data="active_giveaways")
        keyboard.adjust(1)

        try:
            await bot.answer_callback_query(callback_query.id)
        except aiogram.exceptions.TelegramBadRequest as e:
            if "query is too old" in str(e):
                logging.warning(f"Callback query is too old: {e}")
            else:
                raise

        # Check if giveaway has media
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
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise
        else:
            # If no media, use the default image
            try:
                await send_message_with_image(
                    bot,
                    callback_query.from_user.id,
                    giveaway_info,
                    reply_markup=keyboard.as_markup(),
                    message_id=callback_query.message.message_id
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e):
                    logging.warning(f"Message to edit not found: {e}")
                    # Fallback: send a new message
                    await send_new_giveaway_message(callback_query.message.chat.id, giveaway, giveaway_info, keyboard)
                else:
                    raise

    @dp.callback_query(lambda c: c.data.startswith('force_end_giveaway:'))
    async def process_force_end_giveaway(callback_query: types.CallbackQuery):
        giveaway_id = callback_query.data.split(':')[1]
        await bot.answer_callback_query(callback_query.id, text="Завершение розыгрыша...")

        # Pass bot, giveaway_id, and supabase to end_giveaway function
        await end_giveaway(bot=bot, giveaway_id=giveaway_id, supabase=supabase)

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="Назад", callback_data="back_to_main_menu")
        await send_message_with_image(bot, chat_id=callback_query.from_user.id,
                                      message_id=callback_query.message.message_id,
                                      text="Розыгрыш успешно завершен. Результаты опубликованы в связанных сообществах.",
                                      reply_markup=keyboard.as_markup())

    async def send_new_giveaway_message(chat_id, giveaway, giveaway_info, keyboard):
        if giveaway['media_type'] and giveaway['media_file_id']:
            media_type = giveaway['media_type']
            if media_type == 'photo':
                await bot.send_photo(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                     reply_markup=keyboard.as_markup())
            elif media_type == 'gif':
                await bot.send_animation(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                         reply_markup=keyboard.as_markup())
            elif media_type == 'video':
                await bot.send_video(chat_id, giveaway['media_file_id'], caption=giveaway_info,
                                     reply_markup=keyboard.as_markup())
        else:
            await send_message_with_image(bot, chat_id, giveaway_info, reply_markup=keyboard.as_markup())

    return send_new_giveaway_message
