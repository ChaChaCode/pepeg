import logging
from aiogram import Bot, types
from aiogram.types import FSInputFile


async def send_message_with_image(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None):
    image_path = 'image/pepes.png'
    image = FSInputFile(image_path)

    if message_id:
        try:
            await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=types.InputMediaPhoto(media=image, caption=text),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.error(f"Error editing message with image: {e}")
            await bot.send_photo(chat_id=chat_id, photo=image, caption=text, reply_markup=reply_markup)
    else:
        await bot.send_photo(chat_id=chat_id, photo=image, caption=text, reply_markup=reply_markup)

