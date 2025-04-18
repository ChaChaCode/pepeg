import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_auto, count_length_with_custom_emoji

logger = logging.getLogger(__name__)

class SupportStates(StatesGroup):
    sending_messages = State()

def register_support_handlers(dp: Dispatcher, bot: Bot):
    @dp.message(Command("feedback"))
    async def cmd_support(message: types.Message, state: FSMContext):
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        message_text = (
            "<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> Все ваши последующие сообщения будут отправлены нам."
        )
        data = await state.get_data()
        previous_message_length = data.get('previous_message_length', 'short')
        last_message_id = data.get('last_message_id')

        sent_message = await send_message_auto(
            bot,
            chat_id=message.chat.id,
            text=message_text,
            reply_markup=keyboard.as_markup(),
            message_id=last_message_id,
            parse_mode="HTML",
            image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
            previous_message_length=previous_message_length
        )

        if sent_message:
            await state.update_data(
                last_message_id=sent_message.message_id,
                previous_message_type='photo' if count_length_with_custom_emoji(message_text) <= 1024 else 'image',
                previous_message_length='short'
            )
            await state.set_state(SupportStates.sending_messages)

    @dp.message(SupportStates.sending_messages)
    async def handle_support_message(message: types.Message, state: FSMContext):
        user_message = message.text or message.caption or ""
        if not user_message:
            return

        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

        data = await state.get_data()
        previous_message_length = data.get('previous_message_length', 'short')
        last_message_id = data.get('last_message_id')

        # Отправка подтверждения пользователю
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🏠 В меню", callback_data="back_to_main_menu")
        keyboard.adjust(1)

        confirmation_text = (
            f"<tg-emoji emoji-id='5467538555158943525'>💭</tg-emoji> Ваше сообщение:\n"
            f"<blockquote expandable>{message.html_text}</blockquote>\n"
            "<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> отправлено"
        )
        sent_message = await send_message_auto(
            bot,
            chat_id=message.chat.id,
            text=confirmation_text,
            reply_markup=keyboard.as_markup(),
            message_id=last_message_id,
            parse_mode="HTML",
            image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
            previous_message_length=previous_message_length
        )

        if sent_message:
            await state.update_data(
                last_message_id=sent_message.message_id,
                previous_message_type='photo' if count_length_with_custom_emoji(confirmation_text) <= 1024 else 'image',
                previous_message_length='short'
            )

        # Отправка сообщения в группу поддержки
        username = message.from_user.username or f"user_{message.from_user.id}"
        support_message = (
            f"Сообщение от @{username}\n"
            f"<blockquote expandable>{message.html_text}</blockquote>"
        )
        try:
            await bot.send_message(
                chat_id=-4638816277,
                text=support_message,
                parse_mode="HTML"
            )
            logger.info(f"Сообщение от @{username} отправлено в группу поддержки (-4638816277)")
        except Exception as e:
            logger.error(f"Ошибка при отправке сообщения в группу поддержки: {str(e)}")
