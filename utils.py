import logging
from aiogram import Bot, types
from aiogram.types import FSInputFile, Message

async def send_message_with_image(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None) -> Message | None:
    image_path = 'image/pepes.png'  # Замените на путь к вашему изображению
    image = FSInputFile(image_path)

    try:
        if message_id:
            # Редактируем сообщение
            return await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=types.InputMediaPhoto(media=image, caption=text),
                reply_markup=reply_markup
            )
        else:
            # Отправляем новое сообщение, если message_id не указан
            return await bot.send_photo(chat_id=chat_id, photo=image, caption=text, reply_markup=reply_markup)
    except Exception as e:
        logging.error(f"Error in send_message_with_image: {str(e)}")
        # Если не получилось отредактировать или отправить, логируем ошибку и возвращаем None
        return None

