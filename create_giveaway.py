from aiogram import Dispatcher, Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from supabase import create_client, Client
from datetime import datetime
import pytz
from utils import send_message_with_image
import logging
import asyncio

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

# Storage configuration
BUCKET_NAME = 'pepeg'


class GiveawayStates(StatesGroup):
    waiting_for_name = State()
    waiting_for_description = State()
    waiting_for_media_choice = State()
    waiting_for_media_upload = State()
    waiting_for_end_time = State()
    waiting_for_winner_count = State()


async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        # Generate unique filename to avoid conflicts
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        # Upload file to the existing bucket
        response = supabase.storage.from_(BUCKET_NAME).upload(
            path=unique_filename,
            file=file_content,
            file_options={
                "content-type": "application/octet-stream",
                "upsert": False  # Don't overwrite if file exists
            }
        )

        if hasattr(response, 'error') and response.error:
            logger.error(f"Upload error: {response.error}")
            raise Exception(response.error)

        # Get public URL
        public_url = supabase.storage.from_(BUCKET_NAME).get_public_url(unique_filename)

        # Verify upload was successful
        if not public_url:
            raise Exception("Failed to get public URL after upload")

        logger.info(f"File uploaded successfully: {unique_filename}")
        logger.info(f"Public URL: {public_url}")

        return True, public_url

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Storage upload error: {error_msg}")
        return False, error_msg


async def save_giveaway(supabase, user_id: int, name: str, description: str, end_time: str, winner_count: int,
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
            logger.info(f"Giveaway saved successfully: {response.data}")

            # Create default congratulatory message for all winners
            default_congrats_message = f"Поздравляем! Вы выиграли в розыгрыше \"{name}\"!"

            # Save the default congratulatory message for all places
            for place in range(1, winner_count + 1):
                try:
                    supabase.table('congratulations').insert({
                        'giveaway_id': giveaway_id,
                        'place': place,
                        'message': default_congrats_message
                    }).execute()
                except Exception as e:
                    logger.error(f"Error saving default congratulatory message for place {place}: {str(e)}")

            return True, giveaway_id
        else:
            logger.error(f"Unexpected response format: {response}")
            return False, None
    except Exception as e:
        logger.error(f"Error saving giveaway: {str(e)}")
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
        try:
            # Get the last message ID from state
            data = await state.get_data()
            last_message_id = data.get('last_message_id')

            # Show loading message
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="В меню", callback_data="back_to_main_menu")]
            ])
            await send_message_with_image(
                bot,
                message.chat.id,
                "Загрузка...",
                reply_markup=keyboard,
                message_id=last_message_id
            )

            if message.photo:
                file_id = message.photo[-1].file_id
                media_type = 'photo'
                file_ext = 'jpg'
            elif message.animation:
                file_id = message.animation.file_id
                media_type = 'gif'
                file_ext = 'gif'
            elif message.video:
                file_id = message.video.file_id
                media_type = 'video'
                file_ext = 'mp4'
            else:
                await message.reply("Пожалуйста, отправьте фото, GIF или видео.")
                return

            # Get file from Telegram
            file = await bot.get_file(file_id)
            file_content = await bot.download_file(file.file_path)

            # Generate unique filename
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{timestamp}_{message.message_id}.{file_ext}"

            # Upload to Supabase Storage
            success, result = await upload_to_storage(file_content.read(), filename)

            if not success:
                raise Exception(f"Failed to upload to storage: {result}")

            # Store the public URL in state
            await state.update_data(media_type=media_type, media_file_id=result)

            # Continue with the giveaway creation process
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            await process_end_time_request(message.chat.id, state, last_message_id)

        except Exception as e:
            logger.error(f"Error processing media upload: {str(e)}")
            await message.reply("Произошла ошибка при обработке медиафайла. Пожалуйста, попробуйте еще раз.")
            return

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
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")

            current_time = datetime.now(pytz.timezone('Europe/Moscow')).strftime('%d.%m.%Y %H:%M')
            html_message = f"""
Вы ввели неправильный формат даты. Сообщение удалено.

Пожалуйста, введите дату завершения розыгрыша в формате ДД.ММ.ГГГГ ЧЧ:ММ
(текущая дата и время: <code>{current_time}</code>)
    """
            # Changed from bot.edit_message_text to send_message_with_image
            await send_message_with_image(
                bot,
                message.chat.id,
                html_message,
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )

    @dp.message(GiveawayStates.waiting_for_winner_count)
    async def process_winner_count(message: types.Message, state: FSMContext):
        # Delete the user's message
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)

        try:
            # Validate winner count
            winner_count = int(message.text)
            if winner_count <= 0:
                raise ValueError("Winner count must be positive")

            data = await state.get_data()

            # Show loading message
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Розыгрыш создается...",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

            # Save giveaway
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
                # Clear the state
                await state.clear()

                # Wait for the giveaway to be available in the database
                await asyncio.sleep(1)  # Wait 1 second

                # Create a dummy callback query
                callback_data = f"view_created_giveaway:{giveaway_id}"

                # Create an Update object with the callback query
                from aiogram.types import Update
                update = Update(
                    update_id=0,
                    callback_query=types.CallbackQuery(
                        id="dummy_id",
                        from_user=message.from_user,
                        chat_instance="dummy_instance",
                        message=types.Message(
                            message_id=data.get('last_message_id'),
                            date=datetime.now(),
                            chat=message.chat,
                            from_user=message.from_user,
                            text=""
                        ),
                        data=callback_data
                    )
                )

                # Process the update
                await dp.feed_update(bot=bot, update=update)
            # Modify the error handling part in process_winner_count function
            else:
                # Handle error case
                keyboard = InlineKeyboardBuilder()
                keyboard.button(text="Создать повторно", callback_data="create_giveaway")
                keyboard.button(text="В меню", callback_data="back_to_main_menu")
                keyboard.adjust(1)  # One button per row

                await send_message_with_image(
                    bot,
                    message.chat.id,
                    "Произошла ошибка при сохранении розыгрыша. Пожалуйста, попробуйте еще раз.",
                    message_id=data.get('last_message_id'),
                    reply_markup=keyboard.as_markup()
                )

        except ValueError:
            # Handle invalid input
            data = await state.get_data()
            keyboard = InlineKeyboardBuilder()
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await send_message_with_image(
                bot,
                message.chat.id,
                "Пожалуйста, введите положительное целое число для количества победителей.",
                message_id=data.get('last_message_id'),
                reply_markup=keyboard.as_markup()
            )

