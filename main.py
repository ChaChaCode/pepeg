import asyncio
import json
import logging
from collections import defaultdict
from time import time

from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.dispatcher.middlewares.base import BaseMiddleware

from utils import check_and_end_giveaways, check_usernames, send_message_auto, count_length_with_custom_emoji, \
    MAX_NAME_LENGTH
from history_practical import register_history_handlers
from active_giveaways import register_active_giveaways_handlers
from create_giveaway import register_create_giveaway_handlers, GiveawayStates, build_navigation_keyboard
from database import conn, cursor
from created_giveaways import register_created_giveaways_handlers
from my_participations import register_my_participations_handlers
from congratulations_messages import register_congratulations_messages
from congratulations_messages_active import register_congratulations_messages_active
from new_public import register_new_public
from support import register_support_handlers

logger = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BOT_TOKEN = '7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

user_selected_communities = {}
paid_users = {}

user_actions = defaultdict(list)
blocked_users = {}

class MainMenuStates(StatesGroup):
    main_menu = State()

class SpamProtectionMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        user_id = None
        if isinstance(event, types.Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id

        if user_id:
            current_time = time()

            if user_id in blocked_users:
                if current_time < blocked_users[user_id]:
                    logging.info(f"Пользователь {user_id} заблокирован до {blocked_users[user_id]}")
                    if isinstance(event, types.Message):
                        remaining_time = int(blocked_users[user_id] - time())
                        await event.reply(f"Вы временно заблокированы за спам. Осталось {remaining_time} секунд.")
                    elif isinstance(event, CallbackQuery):
                        remaining_time = int(blocked_users[user_id] - time())
                        await bot.answer_callback_query(
                            event.id,
                            f"Вы временно заблокированы за спам. Осталось {remaining_time} секунд.",
                            show_alert=True
                        )
                    return
                else:
                    logging.info(f"Блокировка пользователя {user_id} истекла")
                    del blocked_users[user_id]
                    user_actions[user_id].clear()

            user_actions[user_id] = [t for t in user_actions[user_id] if current_time - t < 1]
            actions_count = len(user_actions[user_id])

            user_actions[user_id].append(current_time)

            logging.info(f"Пользователь {user_id}: {actions_count + 1} действий за последнюю секунду")

            if len(user_actions[user_id]) > 10:
                blocked_users[user_id] = current_time + 60
                logging.info(f"Пользователь {user_id} заблокирован за спам до {blocked_users[user_id]}")
                if isinstance(event, types.Message):
                    remaining_time = int(blocked_users[user_id] - time())
                    await event.reply(f"Вы временно заблокированы за спам. Осталось {remaining_time} секунд.")
                elif isinstance(event, CallbackQuery):
                    remaining_time = int(blocked_users[user_id] - time())
                    await bot.answer_callback_query(
                        event.id,
                        f"Вы временно заблокирован за спам. Осталось {remaining_time} секунд.",
                        show_alert=True
                    )
                return

        return await handler(event, data)

@dp.message(Command("create"))
async def cmd_create(message: types.Message, state: FSMContext):
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

    # Вызов логики из process_create_giveaway
    message_text = f"<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Давайте придумаем название розыгрыша (до {MAX_NAME_LENGTH} символов):"
    image_url = 'https://storage.yandexcloud.net/raffle/snapi/snapi_name2.jpg'
    data = await state.get_data() if state else {}
    previous_message_length = data.get('previous_message_length', 'short')

    await state.update_data(
        user_messages=[],
        current_message_parts=[],
        limit_exceeded=False,
        last_message_time=None,
        last_message_id=message.message_id,
        previous_message_length=previous_message_length
    )
    await state.set_state(GiveawayStates.waiting_for_name)
    keyboard = await build_navigation_keyboard(state, GiveawayStates.waiting_for_name)

    sent_message = await send_message_auto(
        bot,
        chat_id=message.from_user.id,
        text=message_text,
        reply_markup=keyboard.as_markup(),
        message_id=message.message_id,
        parse_mode='HTML',
        image_url=image_url,
        media_type=None,
        previous_message_length=previous_message_length
    )
    if sent_message:
        await state.update_data(
            last_message_id=sent_message.message_id,
            previous_message_length=previous_message_length
        )

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

    current_state = await state.get_state()
    if current_state is not None:
        await state.clear()
        logger.info(f"Процесс создания розыгрыша прерван пользователем {message.from_user.id} командой /start")

    user_id = message.from_user.id

    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s
        """,
        (user_id,)
    )
    has_any_giveaways = cursor.fetchone()[0] > 0

    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_active = 'true'
        """,
        (user_id,)
    )
    has_active_giveaways = cursor.fetchone()[0] > 0

    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM participations p
        JOIN giveaways g ON p.giveaway_id = g.id
        WHERE p.user_id = %s AND g.is_active = 'true'
        """,
        (user_id,)
    )
    active_participations = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM giveaway_winners gw
        JOIN giveaways g ON gw.giveaway_id = g.id
        WHERE gw.user_id = %s AND g.is_completed = 'true'
        """,
        (user_id,)
    )
    won_participations = cursor.fetchone()[0]

    has_participations_or_wins = active_participations > 0 or won_participations > 0
    logging.info(f"User {user_id} - has_active_participations: {active_participations}, has_won_participations: {won_participations}")

    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_completed = 'true'
        """,
        (user_id,)
    )
    has_completed_giveaways = cursor.fetchone()[0] > 0

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")

    if has_any_giveaways:
        keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")

    if has_participations_or_wins:
        keyboard.button(text="🎯 Мои участия", callback_data="my_participations")

    if has_completed_giveaways:
        keyboard.button(text="📜 История розыгрышей", callback_data="giveaway_history")

    keyboard.adjust(1)

    message_text = "<tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji> Добро пожаловать Выберите действие:"
    current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 1024 else 'image'

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
            previous_message_type=current_message_type
        )
        await state.set_state(MainMenuStates.main_menu)

@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def back_to_main_menu(callback_query: CallbackQuery, state: FSMContext):
    await bot.answer_callback_query(callback_query.id)
    await state.clear()

    user_id = callback_query.from_user.id

    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s
        """,
        (user_id,)
    )
    has_any_giveaways = cursor.fetchone()[0] > 0

    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_active = 'true'
        """,
        (user_id,)
    )
    has_active_giveaways = cursor.fetchone()[0] > 0

    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM participations p
        JOIN giveaways g ON p.giveaway_id = g.id
        WHERE p.user_id = %s AND g.is_active = 'true'
        """,
        (user_id,)
    )
    active_participations = cursor.fetchone()[0]

    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM giveaway_winners gw
        JOIN giveaways g ON gw.giveaway_id = g.id
        WHERE gw.user_id = %s AND g.is_completed = 'true'
        """,
        (user_id,)
    )
    won_participations = cursor.fetchone()[0]

    has_participations_or_wins = active_participations > 0 or won_participations > 0
    logging.info(f"User {user_id} - has_active_participations: {active_participations}, has_won_participations: {won_participations}")

    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_completed = 'true'
        """,
        (user_id,)
    )
    has_completed_giveaways = cursor.fetchone()[0] > 0

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")

    if has_any_giveaways:
        keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")

    if has_participations_or_wins:
        keyboard.button(text="🎯 Мои участия", callback_data="my_participations")

    if has_completed_giveaways:
        keyboard.button(text="📜 История розыгрышей", callback_data="giveaway_history")

    keyboard.adjust(1)

    message_text = "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите действие:"
    current_message_type = 'photo' if count_length_with_custom_emoji(message_text) <= 1024 else 'image'

    data = await state.get_data()
    previous_message_length = data.get('previous_message_length', 'short')
    last_message_id = data.get('last_message_id', callback_query.message.message_id)

    sent_message = await send_message_auto(
        bot,
        chat_id=callback_query.message.chat.id,
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
            previous_message_type=current_message_type
        )
        await state.set_state(MainMenuStates.main_menu)

dp.message.middleware(SpamProtectionMiddleware())
dp.callback_query.middleware(SpamProtectionMiddleware())

register_history_handlers(dp, bot, conn, cursor)
register_active_giveaways_handlers(dp, bot, conn, cursor)
register_create_giveaway_handlers(dp, bot, conn, cursor)
register_created_giveaways_handlers(dp, bot, conn, cursor)
register_my_participations_handlers(dp, bot, conn, cursor)
register_congratulations_messages(dp, bot, conn, cursor)
register_congratulations_messages_active(dp, bot, conn, cursor)
register_new_public(dp, bot, conn, cursor)
register_support_handlers(dp, bot)

@dp.message(Command("faq"))
async def cmd_faq(message: types.Message, state: FSMContext):
    try:
        await bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
    except Exception as e:
        logger.warning(f"Не удалось удалить сообщение пользователя {message.message_id}: {str(e)}")

    faq_text = (
        "<b><tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Как создать розыгрыш?</b>\n"
        "<blockquote expandable>Нажмите в главном меню «🎁 Создать розыгрыш» и заполните пошагово:\n"
        "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Название розыгрыша\n"
        "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Описание и медиафайл\n"
        "<tg-emoji emoji-id='5382054253403577563'>3️⃣</tg-emoji> Дату завершения\n"
        "<tg-emoji emoji-id='5391197405553107640'>4️⃣</tg-emoji> Количество победителей</blockquote>\n\n"

        "<b><tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> Что доступно в созданном розыгрыше?</b>\n"
        "<blockquote expandable>В созданном розыгрыше доступно:\n"
        "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Редактирование названия, описания, медиа, числа победителей и кнопку «Участвовать»\n"
        "<tg-emoji emoji-id='5443038326535759644'>💬</tg-emoji> Настройка сообщения для победителей\n"
        "<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Привязка сообществ и публикация розыгрыша\n"  # Убрали <b> внутри строки
        "<tg-emoji emoji-id='5397916757333654639'>➕</tg-emoji> Добавление задания «Пригласить друга» в условия</blockquote>\n\n"

        "<b><tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Как опубликовать розыгрыш?</b>\n"
        "<blockquote expandable>Сначала привяжите каналы или группы:\n"
        "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Перейдите в созданный розыгрыш\n"
        "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Нажмите «Привязать сообщества»\n"
        "<tg-emoji emoji-id='5382054253403577563'>3️⃣</tg-emoji> Добавьте бота в канал или группу с правами администратора\n"
        "<tg-emoji emoji-id='5391197405553107640'>4️⃣</tg-emoji> Получите уведомление об успешной привязке ✅\n"
        "Затем в разделе розыгрыша нажмите «📢 Опубликовать розыгрыш» и выберите сообщества для размещения.</blockquote>\n\n"

        "<b><tg-emoji emoji-id='5447410659077661506'>🌐</tg-emoji> Что доступно в активном розыгрыше?</b>\n"
        "<blockquote expandable>В разделе «Мои розыгрыши» выберите активный розыгрыш (отмечен ✅). Вы можете:\n"
        "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Редактировать розыгрыш (изменения отразятся в постах)\n"
        "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Просматривать статистику участников\n"
        "<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Завершить розыгрыш досрочно</blockquote>\n\n"

        "<b><tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Что будет после завершения розыгрыша?</b>\n"
        "<blockquote expandable>По окончании розыгрыша бот автоматически:\n"
        "<tg-emoji emoji-id='5436386989857320953'>🤑</tg-emoji> Выбирает победителей случайным образом\n"
        "<tg-emoji emoji-id='5451882707875276247'>🕯</tg-emoji> Публикует в сообществах пост с результатами и кнопкой «Результаты» (отображает график участия)\n"
        "<tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji> Отправляет победителям ваше поздравительное сообщение и уведомляет вас</blockquote>"
    )
    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")
    keyboard.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
    keyboard.adjust(1)

    current_message_type = 'image' if count_length_with_custom_emoji(faq_text) > 1024 else 'photo'

    data = await state.get_data()
    previous_message_length = data.get('previous_message_length', 'short')
    last_message_id = data.get('last_message_id')

    sent_message = await send_message_auto(
        bot,
        chat_id=message.chat.id,
        text=faq_text,
        reply_markup=keyboard.as_markup(),
        message_id=last_message_id,
        parse_mode="HTML",
        image_url='https://storage.yandexcloud.net/raffle/snapi/snapi2.jpg',
        previous_message_length=previous_message_length
    )

    if sent_message:
        await state.update_data(
            last_message_id=sent_message.message_id,
            previous_message_type=current_message_type
        )
        await state.set_state(MainMenuStates.main_menu)

async def periodic_username_check():
    while True:
        await check_usernames(bot, conn, cursor)
        await asyncio.sleep(60)

async def update_participant_counters(bot: Bot, conn, cursor):
    previous_counts = {}

    while True:
        try:
            cursor.execute("SELECT * FROM giveaways WHERE is_active = %s", ('true',))
            giveaways = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            giveaways = [dict(zip(columns, row)) for row in giveaways]

            for giveaway in giveaways:
                giveaway_id = giveaway['id']

                cursor.execute(
                    "SELECT COUNT(*) FROM participations WHERE giveaway_id = %s",
                    (giveaway_id,)
                )
                participant_count = cursor.fetchone()[0]

                participant_counter_tasks = giveaway.get('participant_counter_tasks')
                if participant_counter_tasks:
                    if isinstance(participant_counter_tasks, str):
                        try:
                            participant_counter_tasks = json.loads(participant_counter_tasks)
                        except json.JSONDecodeError as e:
                            logger.error(f"Ошибка декодирования JSON для розыгрыша {giveaway_id}: {str(e)}")
                            continue

                    for task in participant_counter_tasks:
                        chat_id = task.get('chat_id')
                        message_id = task.get('message_id')

                        if chat_id and message_id:
                            message_key = f"{giveaway_id}_{chat_id}_{message_id}"
                            previous_count = previous_counts.get(message_key, None)

                            if previous_count == participant_count:
                                logger.info(
                                    f"Количество участников для розыгрыша {giveaway_id} в канале {chat_id} не изменилось: {participant_count} участников")
                                continue

                            try:
                                keyboard = InlineKeyboardBuilder()
                                button_text = giveaway.get('button', '🎉 Участвовать')  # Используем текст из столбца button
                                keyboard.button(
                                    text=f"{button_text} ({participant_count})",
                                    url=f"https://t.me/Snapi/app?startapp={giveaway_id}"
                                )
                                await bot.edit_message_reply_markup(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    reply_markup=keyboard.as_markup()
                                )
                                previous_counts[message_key] = participant_count
                                logger.info(
                                    f"Обновлен счетчик участников для розыгрыша {giveaway_id} в канале {chat_id}: {participant_count} участников")
                            except Exception as e:
                                if "message is not modified" in str(e).lower():
                                    previous_counts[message_key] = participant_count
                                    logger.info(
                                        f"Количество участников для розыгрыша {giveaway_id} в канале {chat_id} не изменилось: {participant_count} участников")
                                else:
                                    logger.error(
                                        f"Ошибка при обновлении счетчика участников в канале {chat_id}, сообщение {message_id}: {str(e)}")

        except Exception as e:
            logger.error(f"Ошибка в функции update_participant_counters: {str(e)}")

        await asyncio.sleep(60)

async def main():
    check_task = asyncio.create_task(check_and_end_giveaways(bot, conn, cursor))
    username_check_task = asyncio.create_task(periodic_username_check())
    participant_counter_task = asyncio.create_task(update_participant_counters(bot, conn, cursor))

    try:
        await dp.start_polling(bot)
    finally:
        check_task.cancel()
        username_check_task.cancel()
        participant_counter_task.cancel()
        cursor.close()
        conn.close()
        logging.info("Соединение с PostgreSQL закрыто.")

if __name__ == '__main__':
    asyncio.run(main())
