from aiogram import Dispatcher
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberAdministrator
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.fsm.storage.base import StorageKey
import logging
import aiohttp
import uuid
import boto3
from botocore.client import Config
from datetime import datetime
import io
import asyncio
from utils import send_message_auto
from created_giveaways import build_community_selection_ui

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Конфигурация Yandex Cloud S3 ☁️
YANDEX_ACCESS_KEY = 'YCAJEDluWSn-XI0tyGyfwfnVL'
YANDEX_SECRET_KEY = 'YCPkR9H9Ucebg6L6eMGvtfKuFIcO_MK7gyiffY6H'
YANDEX_BUCKET_NAME = 'raffle'
YANDEX_ENDPOINT_URL = 'https://storage.yandexcloud.net'
YANDEX_REGION = 'ru-central1'

# Инициализация S3 клиента 📦
s3_client = boto3.client(
    's3',
    region_name=YANDEX_REGION,
    aws_access_key_id=YANDEX_ACCESS_KEY,
    aws_secret_access_key=YANDEX_SECRET_KEY,
    endpoint_url=YANDEX_ENDPOINT_URL,
    config=Config(signature_version='s3v4')
)

class GiveawayStates(StatesGroup):
    binding_communities = State()

pending_channels = {}

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > 5:
            return False, "Файл слишком большой. Максимальный размер: 5 МБ"

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"
        s3_client.put_object(
            Bucket=YANDEX_BUCKET_NAME,
            Key=unique_filename,
            Body=io.BytesIO(file_content),
            ContentType="image/jpeg",
            ACL='public-read'
        )
        public_url = f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/{unique_filename}"
        logging.info(f"Файл загружен: {public_url}")
        return True, public_url
    except Exception as e:
        logging.error(f"Ошибка загрузки в Yandex Cloud: {str(e)}")
        return False, str(e)

def register_new_public(dp: Dispatcher, bot, conn, cursor):
    avatar_file_ids = {}

    async def download_and_save_avatar(chat_id: str, current_url: str = None) -> str | None:
        try:
            chat_info = await bot.get_chat(chat_id)
            if not chat_info.photo:
                if current_url:
                    old_key = current_url.split(f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/")[1]
                    s3_client.delete_object(Bucket=YANDEX_BUCKET_NAME, Key=old_key)
                    logging.info(f"Старая аватарка удалена из S3: {old_key}")
                return None

            new_file_id = chat_info.photo.big_file_id
            if chat_id in avatar_file_ids and avatar_file_ids[chat_id] == new_file_id and current_url:
                logging.info(f"Аватарка для чата {chat_id} не изменилась (file_id: {new_file_id})")
                return current_url

            file_info = await bot.get_file(new_file_id)
            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/file/bot{bot.token}/{file_info.file_path}"
                async with session.get(url) as response:
                    if response.status != 200:
                        return None
                    file_content = await response.read()
            file_name = f"{chat_id}_{uuid.uuid4()}.jpg"
            success, public_url = await upload_to_storage(file_content, file_name)
            if success:
                if current_url and current_url != public_url:
                    old_key = current_url.split(f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/")[1]
                    s3_client.delete_object(Bucket=YANDEX_BUCKET_NAME, Key=old_key)
                    logging.info(f"Старая аватарка удалена из S3: {old_key}")
                avatar_file_ids[chat_id] = new_file_id
                return public_url
            return None
        except Exception as e:
            logging.error(f"Ошибка загрузки аватара для чата {chat_id}: {str(e)}")
            return None

    @dp.my_chat_member()
    async def bot_added_to_chat(event: ChatMemberUpdated, state: FSMContext):
        """Обработчик изменения статуса бота в чате."""
        chat = event.chat
        user_id = str(event.from_user.id)
        user_id_int = int(user_id)
        new_status = event.new_chat_member.status

        chat_type_display = "канал" if chat.type == ChatType.CHANNEL else "группа"
        chat_type_db = "channel" if chat.type == ChatType.CHANNEL else "group"
        community_name = chat.title or "Без названия"
        community_username = chat.username or community_name
        community_id = str(chat.id)

        logging.info(
            f"Событие my_chat_member: {chat_type_display} '{community_name}' (ID: {community_id}), статус бота: {new_status}, пользователь: {user_id}"
        )

        # Если бот исключен из чата
        if new_status in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
            logging.info(f"Бот исключен из {chat_type_display} '{community_name}' (ID: {community_id})")
            try:
                # Получаем всех пользователей, связанных с этим сообществом
                cursor.execute(
                    "SELECT user_id FROM bound_communities WHERE community_id = %s",
                    (community_id,)
                )
                user_ids = [row[0] for row in cursor.fetchall()]
                logging.info(f"Пользователи, связанные с {community_id}: {user_ids}")

                # Удаляем записи для всех пользователей
                cursor.execute(
                    "DELETE FROM bound_communities WHERE community_id = %s",
                    (community_id,)
                )
                cursor.execute(
                    "DELETE FROM giveaway_communities WHERE community_id = %s",
                    (community_id,)
                )
                conn.commit()
                logging.info(f"Записи для {community_id} удалены из bound_communities и giveaway_communities")

                # Уведомляем каждого связанного пользователя
                notification = f"Бот был исключён из {chat_type_display}а '{community_name}'."
                for uid in user_ids:
                    try:
                        uid_int = int(uid)
                        # Создаём новый контекст состояния для каждого пользователя
                        storage_key = StorageKey(
                            bot_id=bot.id, chat_id=uid_int, user_id=uid_int, destiny="default"
                        )
                        user_state = FSMContext(dp.storage, key=storage_key)
                        await user_state.update_data(admin_notification=notification)
                        await update_community_selection_interface(bot, uid_int, user_state)
                        logging.info(f"Уведомление отправлено пользователю {uid} об исключении из {community_id}")
                    except Exception as e:
                        logging.error(f"Ошибка обновления интерфейса для пользователя {uid}: {str(e)}")

            except Exception as e:
                logging.error(f"Ошибка удаления записей для {community_id}: {str(e)}")
                conn.rollback()
            return

        # Проверяем, является ли пользователь администратором чата
        try:
            user_member = await bot.get_chat_member(community_id, user_id_int)
            if user_member.status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                logging.info(f"Пользователь {user_id} не администратор в {chat_type_display} '{community_name}'")
                return
        except Exception as e:
            logging.error(f"Ошибка проверки статуса пользователя {user_id} в {community_id}: {str(e)}")
            return

        # Если бот стал администратором
        if new_status == ChatMemberStatus.ADMINISTRATOR:
            try:
                bot_member = await bot.get_chat_member(chat.id, bot.id)
                if not isinstance(bot_member, ChatMemberAdministrator):
                    await send_message_auto(
                        bot, user_id_int,
                        f"Бот не получил права администратора в {chat_type_display}е '{community_name}'.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                        ]),
                        message_id=None
                    )
                    return

                required_permissions = get_required_permissions(chat_type_db)
                missing_permissions = [
                    perm_name for perm, perm_name in required_permissions.items()
                    if not getattr(bot_member, perm, False)
                ]
                if missing_permissions:
                    await send_message_auto(
                        bot, user_id_int,
                        f"Недостаточно прав у бота в {chat_type_display}е '{community_name}': {', '.join(missing_permissions)}",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                        ]),
                        message_id=None
                    )
                    return

                # Получаем всех администраторов чата
                admins = await bot.get_chat_administrators(community_id)
                admin_ids = [str(admin.user.id) for admin in admins if not admin.user.is_bot]
                logging.info(f"Администраторы {community_id}: {admin_ids}")

                # Загружаем аватарку
                cursor.execute(
                    "SELECT media_file_ava FROM bound_communities WHERE community_id = %s AND user_id = %s",
                    (community_id, user_id)
                )
                result = cursor.fetchone()
                current_url = result[0] if result else None
                avatar_url = await download_and_save_avatar(community_id, current_url)

                # Обрабатываем всех администраторов
                for admin_id in admin_ids:
                    admin_id_int = int(admin_id)
                    try:
                        # Записываем сообщество в базу для администратора
                        success = await record_bound_community(
                            admin_id, community_username, community_id, chat_type_db, community_name, avatar_url
                        )
                        if success:
                            logging.info(f"Сообщество '{community_name}' привязано для администратора {admin_id}")
                            notification = f"{chat_type_display.capitalize()} '{community_name}' успешно привязан! Теперь вы можете привязывать его к вашим розыгрышам."
                            # Создаём контекст состояния для администратора
                            storage_key = StorageKey(
                                bot_id=bot.id, chat_id=admin_id_int, user_id=admin_id_int, destiny="default"
                            )
                            admin_state = FSMContext(dp.storage, key=storage_key)
                            await admin_state.update_data(admin_notification=notification)
                            try:
                                cursor.execute(
                                    """
                                    INSERT INTO user_binding_state (user_id, admin_notification)
                                    VALUES (%s, %s)
                                    ON CONFLICT (user_id)
                                    DO UPDATE SET admin_notification = EXCLUDED.admin_notification,
                                                  updated_at = CURRENT_TIMESTAMP
                                    """,
                                    (admin_id_int, notification)
                                )
                                conn.commit()
                            except Exception as e:
                                logging.error(f"Ошибка сохранения уведомления для {admin_id}: {str(e)}")

                            # Проверяем контекст розыгрыша для администратора
                            state_data = await admin_state.get_data()
                            giveaway_id = state_data.get('giveaway_id')
                            message_id = state_data.get('message_id')

                            if giveaway_id:
                                await handle_successful_binding(
                                    community_id, community_username, admin_id, giveaway_id, admin_state,
                                    message_id, chat_type_db, chat_type_display, community_name, avatar_url
                                )
                            # Обновляем интерфейс для администратора
                            await update_community_selection_interface(bot, admin_id_int, admin_state)
                        else:
                            logging.error(f"Не удалось привязать сообщество '{community_name}' для {admin_id}")
                            notification = f"Не удалось привязать {chat_type_display} '{community_name}'. Попробуйте снова."
                            storage_key = StorageKey(
                                bot_id=bot.id, chat_id=admin_id_int, user_id=admin_id_int, destiny="default"
                            )
                            admin_state = FSMContext(dp.storage, key=storage_key)
                            await admin_state.update_data(admin_notification=notification)
                            await update_community_selection_interface(bot, admin_id_int, admin_state)
                    except Exception as e:
                        logging.error(f"Ошибка обработки администратора {admin_id} для {community_id}: {str(e)}")

            except Exception as e:
                logging.error(f"Ошибка обработки администраторского статуса бота для {community_id}: {str(e)}")
                await send_message_auto(
                    bot, user_id_int,
                    f"Произошла ошибка при добавлении {chat_type_display}а '{community_name}'. Попробуйте снова.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                    ]),
                    message_id=None
                )

    @dp.chat_member()
    async def user_status_updated(event: ChatMemberUpdated, state: FSMContext):
        """Обработчик изменения статуса пользователя в чате (например, назначение или снятие администратора)."""
        chat = event.chat
        user_id = str(event.new_chat_member.user.id)
        user_id_int = int(user_id)
        new_status = event.new_chat_member.status
        old_status = event.old_chat_member.status

        chat_type_display = "канал" if chat.type == ChatType.CHANNEL else "группа"
        chat_type_db = "channel" if chat.type == ChatType.CHANNEL else "group"
        community_name = chat.title or "Без названия"
        community_username = chat.username or community_name
        community_id = str(chat.id)

        logging.info(
            f"Событие chat_member: {chat_type_display} '{community_name}' (ID: {community_id}), "
            f"пользователь: {user_id}, старый статус: {old_status}, новый статус: {new_status}"
        )

        # Проверяем, стал ли пользователь администратором
        if new_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and \
                old_status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            try:
                # Проверяем, является ли бот администратором чата
                bot_member = await bot.get_chat_member(community_id, bot.id)
                if bot_member.status != ChatMemberStatus.ADMINISTRATOR:
                    logging.info(f"Бот не администратор в {chat_type_display} '{community_name}', пропускаем для {user_id}")
                    return

                # Проверяем права бота
                required_permissions = get_required_permissions(chat_type_db)
                missing_permissions = [
                    perm_name for perm, perm_name in required_permissions.items()
                    if not getattr(bot_member, perm, False)
                ]
                if missing_permissions:
                    logging.info(
                        f"Недостаточно прав у бота в {chat_type_display} '{community_name}': {', '.join(missing_permissions)}")
                    await send_message_auto(
                        bot, user_id_int,
                        f"Бот не имеет достаточных прав в {chat_type_display}е '{community_name}': {', '.join(missing_permissions)}. Назначьте их для использования.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                        ]),
                        message_id=None
                    )
                    return

                # Загружаем аватарку
                cursor.execute(
                    "SELECT media_file_ava FROM bound_communities WHERE community_id = %s AND user_id = %s",
                    (community_id, user_id)
                )
                result = cursor.fetchone()
                current_url = result[0] if result else None
                avatar_url = await download_and_save_avatar(community_id, current_url)

                # Записываем сообщество в базу
                success = await record_bound_community(
                    user_id, community_username, community_id, chat_type_db, community_name, avatar_url
                )
                if success:
                    logging.info(f"Сообщество '{community_name}' привязано для нового администратора {user_id}")
                    notification = (
                        f"Вы стали администратором в {chat_type_display} '{community_name}'! "
                        f"Теперь вы можете привязывать его к вашим розыгрышам."
                    )
                    await state.update_data(admin_notification=notification)
                    try:
                        cursor.execute(
                            """
                            INSERT INTO user_binding_state (user_id, admin_notification)
                            VALUES (%s, %s)
                            ON CONFLICT (user_id)
                            DO UPDATE SET admin_notification = EXCLUDED.admin_notification,
                                          updated_at = CURRENT_TIMESTAMP
                            """,
                            (user_id_int, notification)
                        )
                        conn.commit()
                    except Exception as e:
                        logging.error(f"Ошибка сохранения уведомления для {user_id}: {str(e)}")
                    await update_community_selection_interface(bot, user_id_int, state)
                else:
                    logging.error(f"Не удалось привязать сообщество '{community_name}' для {user_id}")
                    await send_message_auto(
                        bot, user_id_int,
                        f"Не удалось привязать {chat_type_display} '{community_name}'. Попробуйте снова.",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                            [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                        ]),
                        message_id=None
                    )

            except Exception as e:
                logging.error(f"Ошибка обработки нового администратора {user_id} в {community_id}: {str(e)}")
                await send_message_auto(
                    bot, user_id_int,
                    f"Произошла ошибка при обработке {chat_type_display}а '{community_name}'. Попробуйте снова.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                    ]),
                    message_id=None
                )

        # Проверяем, потерял ли пользователь статус администратора
        elif old_status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR] and \
                new_status not in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
            try:
                logging.info(
                    f"Пользователь {user_id} потерял статус администратора в {chat_type_display} '{community_name}'")
                cursor.execute(
                    "DELETE FROM bound_communities WHERE community_id = %s AND user_id = %s",
                    (community_id, user_id)
                )
                cursor.execute(
                    "DELETE FROM giveaway_communities WHERE community_id = %s AND user_id = %s",
                    (community_id, user_id)
                )
                conn.commit()
                logging.info(f"Записи для {community_id} удалены для пользователя {user_id}")

                notification = f"Вы больше не администратор в {chat_type_display} '{community_name}'. Доступ к управлению в боте удалён."
                await state.update_data(admin_notification=notification)
                try:
                    cursor.execute(
                        """
                        INSERT INTO user_binding_state (user_id, admin_notification)
                        VALUES (%s, %s)
                        ON CONFLICT (user_id)
                        DO UPDATE SET admin_notification = EXCLUDED.admin_notification,
                                      updated_at = CURRENT_TIMESTAMP
                        """,
                        (user_id_int, notification)
                    )
                    conn.commit()
                except Exception as e:
                    logging.error(f"Ошибка сохранения уведомления для {user_id}: {str(e)}")
                await update_community_selection_interface(bot, user_id_int, state)

            except Exception as e:
                logging.error(f"Ошибка обработки снятия администратора {user_id} в {community_id}: {str(e)}")
                conn.rollback()
                await send_message_auto(
                    bot, user_id_int,
                    f"Произошла ошибка при обработке {chat_type_display}а '{community_name}'. Попробуйте снова.",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="◀️ Назад", callback_data="start")]
                    ]),
                    message_id=None
                )

    async def update_community_selection_interface(bot, user_id: int, state: FSMContext):
        """Обновляет интерфейс выбора сообществ для пользователя, если он находится в режиме привязки."""
        state_data = await state.get_data()
        giveaway_id = state_data.get('giveaway_id')
        message_id = state_data.get('message_id')
        notification = state_data.get('admin_notification')

        # Убрана проверка базы данных для восстановления состояния
        # Если giveaway_id и message_id найдены, обновляем UI
        if giveaway_id and message_id:
            try:
                bot_info = await bot.get_me()
                message_text, keyboard, image_url = await build_community_selection_ui(
                    user_id, giveaway_id, bot, bot_info, notification=notification
                )
                # Используем send_message_auto для единообразного отображения
                await send_message_auto(
                    bot,
                    user_id,
                    message_text,
                    reply_markup=keyboard,
                    message_id=message_id,
                    parse_mode='HTML',
                    image_url=image_url
                )
                logging.info(
                    f"Интерфейс выбора сообществ обновлен для пользователя {user_id}, giveaway_id: {giveaway_id}"
                )

                # Очищаем уведомление после отображения
                if notification:
                    await state.update_data(admin_notification=None)
                    try:
                        cursor.execute(
                            "UPDATE user_binding_state SET admin_notification = NULL WHERE user_id = %s",
                            (user_id,)
                        )
                        conn.commit()
                    except Exception as e:
                        logging.error(f"Ошибка очистки уведомления в базе для {user_id}: {str(e)}")

            except Exception as e:
                logging.error(f"Ошибка при обновлении интерфейса для {user_id}: {str(e)}")
                # Очищаем уведомление при ошибке
                if notification:
                    await state.update_data(admin_notification=None)
                    try:
                        cursor.execute(
                            "UPDATE user_binding_state SET admin_notification = NULL WHERE user_id = %s",
                            (user_id,)
                        )
                        conn.commit()
                    except Exception as e:
                        logging.error(f"Ошибка очистки уведомления в базе для {user_id}: {str(e)}")
        else:
            logging.info(f"Пользователь {user_id} не в режиме привязки сообществ, обновление UI пропущено")
            if notification:
                try:
                    await send_message_auto(
                        bot,
                        user_id,
                        f"<tg-emoji emoji-id='5206607081334906820'>✔️</tg-emoji> {notification}",
                        reply_markup=None,
                        message_id=None,
                        parse_mode='HTML',
                        image_url='https://storage.yandexcloud.net/raffle/snapi/snapi.jpg'  # Дефолтное изображение
                    )
                    await state.update_data(admin_notification=None)
                    try:
                        cursor.execute(
                            "UPDATE user_binding_state SET admin_notification = NULL WHERE user_id = %s",
                            (user_id,)
                        )
                        conn.commit()
                    except Exception as e:
                        logging.error(f"Ошибка очистки уведомления в базе для {user_id}: {str(e)}")
                except Exception as e:
                    logging.error(f"Ошибка отправки уведомления для {user_id}: {str(e)}")

    async def handle_successful_binding(community_id: str, community_username: str, user_id: str, giveaway_id: str,
                                        state: FSMContext, message_id: int, chat_type_db: str, chat_type_display: str,
                                        community_name: str, avatar_url: str = None):
        try:
            cursor.execute(
                "SELECT * FROM giveaway_communities WHERE giveaway_id = %s AND community_id = %s AND user_id = %s",
                (giveaway_id, community_id, user_id)
            )
            if cursor.fetchone():
                notification = f"{chat_type_display.capitalize()} '{community_username}' уже привязан."
                await state.update_data(admin_notification=notification)
                return

            await bind_community_to_giveaway(
                giveaway_id, community_id, community_username, chat_type_db, user_id, community_name, avatar_url
            )
            notification = f"{chat_type_display.capitalize()} '{community_username}' успешно привязан!"
            await state.update_data(admin_notification=notification)
            # Убрали вызов update_community_selection_interface, так как он вызывается в bot_added_to_chat или user_status_updated
        except Exception as e:
            logging.error(f"Ошибка привязки сообщества {community_id} к розыгрышу {giveaway_id}: {str(e)}")
            conn.rollback()

    async def record_bound_community(user_id: str, community_username: str, community_id: str, community_type: str,
                                     community_name: str, media_file_ava: str = None):
        try:
            data = {
                'user_id': user_id,
                'community_username': community_username,
                'community_id': community_id,
                'community_type': community_type,
                'community_name': community_name
            }
            if media_file_ava:
                data['media_file_ava'] = media_file_ava

            cursor.execute(
                "SELECT * FROM bound_communities WHERE community_id = %s AND user_id = %s",
                (community_id, user_id)
            )
            if cursor.fetchone():
                update_columns = ', '.join([f"{key} = %s" for key in data.keys()])
                cursor.execute(
                    f"UPDATE bound_communities SET {update_columns} WHERE community_id = %s AND user_id = %s",
                    (*data.values(), community_id, user_id)
                )
            else:
                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))
                cursor.execute(
                    f"INSERT INTO bound_communities ({columns}) VALUES ({placeholders})",
                    tuple(data.values())
                )
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Ошибка записи в bound_communities: {str(e)}")
            conn.rollback()
            return False

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username, community_type, user_id,
                                         community_name, avatar_url=None):
        try:
            data = {
                "giveaway_id": giveaway_id,
                "community_id": community_id,
                "community_username": community_username,
                "community_type": community_type,
                "user_id": user_id,
                "community_name": community_name
            }
            if avatar_url:
                data["media_file_ava"] = avatar_url
            columns = ', '.join(data.keys())
            placeholders = ', '.join(['%s'] * len(data))
            cursor.execute(
                f"INSERT INTO giveaway_communities ({columns}) VALUES ({placeholders})",
                tuple(data.values())
            )
            conn.commit()
        except Exception as e:
            logging.error(f"Ошибка привязки сообщества к розыгрышу: {str(e)}")
            conn.rollback()

    def get_required_permissions(chat_type: str):
        if chat_type == "channel":
            return {
                'can_post_messages': 'Публикация сообщений',
                'can_edit_messages': 'Редактирование сообщений',
                'can_invite_users': 'Добавление подписчиков'
            }
        return {
            'can_delete_messages': 'Удаление сообщений',
            'can_invite_users': 'Пригласительные ссылки',
            'can_pin_messages': 'Закрепление сообщений'
        }

    async def check_and_update_avatars():
        while True:
            try:
                cursor.execute("SELECT community_id, user_id, media_file_ava FROM bound_communities")
                communities = cursor.fetchall()
                for community_id, user_id, current_url in communities:
                    try:
                        chat_info = await bot.get_chat(community_id)
                        if not chat_info.photo:
                            if current_url:
                                cursor.execute(
                                    "UPDATE bound_communities SET media_file_ava = NULL WHERE community_id = %s AND user_id = %s",
                                    (community_id, user_id)
                                )
                                old_key = current_url.split(f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/")[1]
                                s3_client.delete_object(Bucket=YANDEX_BUCKET_NAME, Key=old_key)
                                logging.info(f"Старая аватарка удалена из S3: {old_key}")
                                conn.commit()
                                logging.info(f"Аватарка удалена для сообщества {community_id}")
                            continue

                        new_url = await download_and_save_avatar(community_id, current_url)
                        if new_url and new_url != current_url:
                            cursor.execute(
                                "UPDATE bound_communities SET media_file_ava = %s WHERE community_id = %s AND user_id = %s",
                                (new_url, community_id, user_id)
                            )
                            conn.commit()
                            logging.info(f"Аватарка обновлена для сообщества {community_id}: {new_url}")
                    except Exception as e:
                        logging.error(f"Ошибка при проверке аватарки для сообщества {community_id}: {str(e)}")
            except Exception as e:
                logging.error(f"Ошибка в check_and_update_avatars: {str(e)}")
            await asyncio.sleep(36000)

    async def check_bot_chats_and_admins():
        while True:
            try:
                cursor.execute("SELECT community_id FROM bound_communities")
                known_chats = set(row[0] for row in cursor.fetchall())
                current_chats = set()

                for chat_id in known_chats:
                    try:
                        chat_member = await bot.get_chat_member(chat_id, bot.id)
                        if chat_member.status not in [ChatMemberStatus.LEFT, ChatMemberStatus.KICKED]:
                            current_chats.add(chat_id)
                            admins = await bot.get_chat_administrators(chat_id)
                            chat_info = await bot.get_chat(chat_id)

                            chat_type_db = "channel" if chat_info.type == ChatType.CHANNEL else "group"
                            community_name = chat_info.title or "Без названия"
                            community_username = chat_info.username or community_name

                            cursor.execute(
                                "SELECT media_file_ava FROM bound_communities WHERE community_id = %s",
                                (chat_id,)
                            )
                            result = cursor.fetchone()
                            current_url = result[0] if result else None
                            avatar_url = await download_and_save_avatar(chat_id, current_url)

                            for admin in admins:
                                if admin.user.is_bot:
                                    continue
                                admin_id = str(admin.user.id)
                                admin_id_int = int(admin_id)
                                await record_bound_community(
                                    admin_id, community_username, chat_id, chat_type_db, community_name, avatar_url
                                )
                                logging.info(f"Администратор {admin_id} обновлен для чата {chat_id}")
                                storage_key = StorageKey(
                                    bot_id=bot.id, chat_id=admin_id_int, user_id=admin_id_int, destiny="default"
                                )
                                await update_community_selection_interface(
                                    bot, admin_id_int, FSMContext(dp.storage, key=storage_key)
                                )
                        else:
                            cursor.execute("DELETE FROM bound_communities WHERE community_id = %s", (chat_id,))
                            cursor.execute("DELETE FROM giveaway_communities WHERE community_id = %s", (chat_id,))
                            conn.commit()
                            logging.info(f"Бот исключен из чата {chat_id}, записи удалены")
                    except Exception as e:
                        logging.error(f"Ошибка проверки чата {chat_id}: {str(e)}")
                        cursor.execute("DELETE FROM bound_communities WHERE community_id = %s", (chat_id,))
                        cursor.execute("DELETE FROM giveaway_communities WHERE community_id = %s", (chat_id,))
                        conn.commit()

                logging.info(f"Текущие чаты бота: {current_chats}")
            except Exception as e:
                logging.error(f"Ошибка в check_bot_chats_and_admins: {str(e)}")
            await asyncio.sleep(3600)

    @dp.startup()
    async def on_startup():
        logging.info("Бот запущен, стартуем фоновые задачи")
        asyncio.create_task(check_and_update_avatars())
        asyncio.create_task(check_bot_chats_and_admins())
