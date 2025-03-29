import asyncio
import json
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command
import psycopg2
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from utils import send_message_with_image, check_and_end_giveaways, check_usernames
from active_giveaways import register_active_giveaways_handlers
from create_giveaway import register_create_giveaway_handlers
from created_giveaways import register_created_giveaways_handlers
from my_participations import register_my_participations_handlers
from congratulations_messages import register_congratulations_messages
from congratulations_messages_active import register_congratulations_messages_active
from new_public import register_new_public
from aiogram.fsm.context import FSMContext

logger = logging.getLogger(__name__)

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация бота и диспетчера
BOT_TOKEN = '7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Конфигурация PostgreSQL
db_config = {
    "host": "195.200.26.251",
    "port": 5432,
    "database": "mydatabase",
    "user": "app_user",
    "password": "moxy1337"
}

# Подключение к PostgreSQL
try:
    conn = psycopg2.connect(**db_config)
    logging.info("Успешно подключились к PostgreSQL!")
except Exception as e:
    logging.error(f"Ошибка подключения к PostgreSQL: {e}")
    raise

# Создай курсор для выполнения запросов
cursor = conn.cursor()

# Переменные для хранения данных
user_selected_communities = {}
paid_users = {}

# Регистрация обработчиков из других модулей
register_active_giveaways_handlers(dp, bot, conn, cursor)
register_create_giveaway_handlers(dp, bot, conn, cursor)
register_created_giveaways_handlers(dp, bot, conn, cursor)
register_my_participations_handlers(dp, bot, conn, cursor)
register_congratulations_messages(dp, bot, conn, cursor)
register_congratulations_messages_active(dp, bot, conn, cursor)
register_new_public(dp, bot, conn, cursor)

# Обработчик команды /start
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id

    # Проверяем наличие любых розыгрышей пользователя
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s
        """,
        (user_id,)
    )
    has_any_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие активных розыгрышей, созданных пользователем
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_active = 'true'
        """,
        (user_id,)
    )
    has_active_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие участий в активных розыгрышах
    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM participations p
        JOIN giveaways g ON p.giveaway_id = g.id
        WHERE p.user_id = %s AND g.is_active = 'true'
        """,
        (user_id,)
    )
    has_active_participations = cursor.fetchone()[0] > 0
    logging.info(f"User {user_id} - has_active_participations: {has_active_participations}")

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")

    # Добавляем "Мои розыгрыши", если есть любые розыгрыши
    if has_any_giveaways:
        keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")

    # Добавляем "Мои участия", только если пользователь участвует в активных розыгрышах
    if has_active_participations:
        keyboard.button(text="🎯 Мои участия", callback_data="my_participations")

    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        message.chat.id,
        "<tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji> Добро пожаловать! Выберите действие:",
        reply_markup=keyboard.as_markup()
    )

# Обработчик возврата в главное меню
@dp.callback_query(lambda c: c.data == "back_to_main_menu")
async def back_to_main_menu(callback_query: CallbackQuery, state: FSMContext):
    await state.clear()
    await bot.answer_callback_query(callback_query.id)

    user_id = callback_query.from_user.id

    # Проверяем наличие любых розыгрышей пользователя
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s
        """,
        (user_id,)
    )
    has_any_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие активных розыгрышей, созданных пользователем
    cursor.execute(
        """
        SELECT COUNT(*) FROM giveaways 
        WHERE user_id = %s AND is_active = 'true'
        """,
        (user_id,)
    )
    has_active_giveaways = cursor.fetchone()[0] > 0

    # Проверяем наличие участий в активных розыгрышах
    cursor.execute(
        """
        SELECT COUNT(*) 
        FROM participations p
        JOIN giveaways g ON p.giveaway_id = g.id
        WHERE p.user_id = %s AND g.is_active = 'true'
        """,
        (user_id,)
    )
    has_active_participations = cursor.fetchone()[0] > 0
    logging.info(f"User {user_id} - has_active_participations: {has_active_participations}")

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")

    # Добавляем "Мои розыгрыши", если есть любые розыгрыши
    if has_any_giveaways:
        keyboard.button(text="📋 Мои розыгрыши", callback_data="created_giveaways")

    # Добавляем "Мои участия", только если пользователь участвует в активных розыгрышах
    if has_active_participations:
        keyboard.button(text="🎯 Мои участия", callback_data="my_participations")

    keyboard.adjust(1)

    await send_message_with_image(
        bot,
        callback_query.message.chat.id,
        "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Выберите действие:",
        reply_markup=keyboard.as_markup(),
        message_id=callback_query.message.message_id
    )

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    try:
        help_text = (
            "<b><tg-emoji emoji-id='5282843764451195532'>🖥</tg-emoji> Как создать розыгрыш</b>\n"
            "<blockquote expandable>Первое, что вам нужно сделать, — это нажать в главном меню кнопку «🎁 Создать розыгрыш». После этого вам потребуется пошагово ввести:\n"
            "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Название розыгрыша\n"
            "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Описание\n"
            "<tg-emoji emoji-id='5381879959335738545'>3️⃣</tg-emoji> Медиафайл (если он необходим)\n"
            "<tg-emoji emoji-id='5382054253403577563'>4️⃣</tg-emoji> Дату завершения\n"
            "<tg-emoji emoji-id='5391197405553107640'>5️⃣</tg-emoji> Количество победителей</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> Как опубликовать созданный розыгрыш</b>\n"
            "<blockquote expandable>Чтобы опубликовать розыгрыш, сначала привяжите каналы или группы. Для этого:\n"
            "<tg-emoji emoji-id='5382322671679708881'>1️⃣</tg-emoji> Перейдите в ваш созданный розыгрыш\n"
            "<tg-emoji emoji-id='5381990043642502553'>2️⃣</tg-emoji> Нажмите кнопку «Привязать сообщества»\n"
            "<tg-emoji emoji-id='5381879959335738545'>3️⃣</tg-emoji> Нажмите «➕ Новый паблик»\n"
            "<tg-emoji emoji-id='5382054253403577563'>4️⃣</tg-emoji> Добавьте бота в ваш канал или группу с правами администратора\n"
            "<tg-emoji emoji-id='5391197405553107640'>5️⃣</tg-emoji> Бот уведомит вас о успешной привязке ✅\n"
            "После привязки сообщества в разделе созданного розыгрыша нажмите кнопку «📢 Опубликовать розыгрыш» и выберите привязанные сообщества, в которых хотите разместить розыгрыш.</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5341715473882955310'>⚙️</tg-emoji> Дополнительные функции</b>\n"
            "<blockquote expandable>В созданном розыгрыше вы можете:\n"
            "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Редактировать название, описание, медиафайл, количество победителей и можно убрать текст бота в конце поста или же вернуть его\n"
            "<tg-emoji emoji-id='5443038326535759644'>💬</tg-emoji> Изменить сообщение для победителей\n"
            "<tg-emoji emoji-id='5397916757333654639'>➕</tg-emoji> Добавить задание «Пригласить друга» в условия участия</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5447410659077661506'>🌐</tg-emoji> Что можно делать, когда розыгрыш опубликован</b>\n"
            "<blockquote expandable>В главном меню перейдите в раздел «Мои розыгрыши», выберите нужный активный розыгрыш который почмечен ✅ в начале. В нем вы можете:\n"
            "<tg-emoji emoji-id='5395444784611480792'>✏️</tg-emoji> Полностью редактировать розыгрыш (все изменения отразятся в опубликованных постах)\n"
            "<tg-emoji emoji-id='5210956306952758910'>👀</tg-emoji> Смотреть статистику сколько пользователей участвуют\n"
            "<tg-emoji emoji-id='5413879192267805083'>🗓</tg-emoji> Принудительно завершить розыгрыш</blockquote>\n\n"

            "<b><tg-emoji emoji-id='5197630131534836123'>🥳</tg-emoji> Что будет, когда розыгрыш завершится</b>\n"
            "<blockquote expandable>После окончания времени розыгрыша бот автоматически:\n"
            "<tg-emoji emoji-id='5436386989857320953'>🤑</tg-emoji> Определит рандомно победителей\n"
            "<tg-emoji emoji-id='5451882707875276247'>🕯</tg-emoji> Опубликует в привязанных сообществах пост о завершении с указанием победителей и кнопкой «Результаты» (при нажатии пользователи увидят график участия)\n"
            "<tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji> Отправит победителям поздравительное сообщение, заданное вами ранее и уведомит об этом вас</blockquote>"
        )
        keyboard = InlineKeyboardBuilder()
        keyboard.button(text="🎁 Создать розыгрыш", callback_data="create_giveaway")
        keyboard.button(text="🏠 В главное меню", callback_data="back_to_main_menu")
        keyboard.adjust(1)
        await bot.send_message(
            chat_id=message.chat.id,
            text=help_text,
            parse_mode="HTML",
            reply_markup=keyboard.as_markup()
        )
    except Exception as e:
        logging.error(f"Ошибка в cmd_help: {e}")
        await message.reply("Произошла ошибка при выполнении команды /help.")

async def periodic_username_check():
    while True:
        await check_usernames(bot, conn, cursor)
        await asyncio.sleep(60)

async def update_participant_counters(bot: Bot, conn, cursor):
    """
    Функция для обновления счетчика участников в кнопках розыгрышей.
    Проверяет активные розыгрыши каждые 10 секунд, подсчитывает участников
    и обновляет текст кнопки "Участвовать" с указанием количества участников.
    """
    # Словарь для хранения предыдущих значений счетчиков
    previous_counts = {}

    while True:
        try:
            # Получаем все активные розыгрыши
            cursor.execute("SELECT * FROM giveaways WHERE is_active = %s", ('true',))
            giveaways = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            giveaways = [dict(zip(columns, row)) for row in giveaways]

            for giveaway in giveaways:
                giveaway_id = giveaway['id']

                # Подсчитываем количество участников для данного розыгрыша
                cursor.execute(
                    "SELECT COUNT(*) FROM participations WHERE giveaway_id = %s",
                    (giveaway_id,)
                )
                participant_count = cursor.fetchone()[0]

                # Получаем информацию о каналах, где опубликован розыгрыш
                participant_counter_tasks = giveaway.get('participant_counter_tasks')
                if participant_counter_tasks:
                    # Преобразуем JSON в список, если это строка
                    if isinstance(participant_counter_tasks, str):
                        try:
                            participant_counter_tasks = json.loads(participant_counter_tasks)
                        except json.JSONDecodeError as e:
                            logger.error(f"Ошибка декодирования JSON для розыгрыша {giveaway_id}: {str(e)}")
                            continue

                    # Обновляем кнопку в каждом канале
                    for task in participant_counter_tasks:
                        chat_id = task.get('chat_id')
                        message_id = task.get('message_id')

                        if chat_id and message_id:
                            # Создаем уникальный ключ для этого сообщения
                            message_key = f"{giveaway_id}_{chat_id}_{message_id}"

                            # Проверяем, изменилось ли количество участников
                            previous_count = previous_counts.get(message_key, None)

                            if previous_count == participant_count:
                                # Если количество не изменилось, просто логируем это
                                logger.info(
                                    f"Количество участников для розыгрыша {giveaway_id} в канале {chat_id} не изменилось: {participant_count} участников")
                                continue

                            try:
                                # Создаем новую клавиатуру с обновленным текстом
                                keyboard = InlineKeyboardBuilder()

                                # Добавляем кнопку "Участвовать" с количеством участников и URL
                                keyboard.button(
                                    text=f"🎉 Участвовать ({participant_count})",
                                    url=f"https://t.me/Snapi/app?startapp={giveaway['link']}"
                                )

                                # Обновляем сообщение с новой клавиатурой
                                await bot.edit_message_reply_markup(
                                    chat_id=chat_id,
                                    message_id=message_id,
                                    reply_markup=keyboard.as_markup()
                                )

                                # Сохраняем новое количество участников
                                previous_counts[message_key] = participant_count

                                logger.info(
                                    f"Обновлен счетчик участников для розыгрыша {giveaway_id} в канале {chat_id}: {participant_count} участников")
                            except Exception as e:
                                # Проверяем сообщение об ошибке
                                if "message is not modified" in str(e).lower():
                                    # Если сообщение не изменилось, обновляем счетчик в словаре
                                    previous_counts[message_key] = participant_count
                                    logger.info(
                                        f"Количество участников для розыгрыша {giveaway_id} в канале {chat_id} не изменилось: {participant_count} участников")
                                else:
                                    logger.error(
                                        f"Ошибка при обновлении счетчика участников в канале {chat_id}, сообщение {message_id}: {str(e)}")

        except Exception as e:
            logger.error(f"Ошибка в функции update_participant_counters: {str(e)}")

        # Ждем 10 секунд перед следующей проверкой
        await asyncio.sleep(60)

# Обновленная функция main()
async def main():
    # Создаем задачи для проверки розыгрышей и обновления имен пользователей
    check_task = asyncio.create_task(check_and_end_giveaways(bot, conn, cursor))
    username_check_task = asyncio.create_task(periodic_username_check())

    # Добавляем новую задачу для обновления счетчика участников
    participant_counter_task = asyncio.create_task(update_participant_counters(bot, conn, cursor))

    try:
        await dp.start_polling(bot)
    finally:
        # Отменяем все задачи при завершении работы бота
        check_task.cancel()
        username_check_task.cancel()
        participant_counter_task.cancel()  # Отменяем новую задачу

        cursor.close()
        conn.close()
        logging.info("Соединение с PostgreSQL закрыто.")

if __name__ == '__main__':
    asyncio.run(main())
