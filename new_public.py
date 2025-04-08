from aiogram import Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberAdministrator
from aiogram.enums import ChatMemberStatus, ChatType
import logging
import aiohttp
import uuid
import boto3
from botocore.client import Config
from datetime import datetime
import io
import asyncio
from utils import send_message_with_image

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
    # Временное хранилище file_id для отслеживания изменений внутри сессии
    avatar_file_ids = {}

    async def download_and_save_avatar(chat_id: str, current_url: str = None) -> str | None:
        try:
            chat_info = await bot.get_chat(chat_id)
            if not chat_info.photo:
                return None

            new_file_id = chat_info.photo.big_file_id
            # Проверяем, изменился ли file_id по сравнению с предыдущим для этого чата
            if chat_id in avatar_file_ids and avatar_file_ids[chat_id] == new_file_id and current_url:
                logging.info(f"Аватарка для чата {chat_id} не изменилась (file_id: {new_file_id})")
                return current_url  # Возвращаем текущий URL, если аватарка не изменилась

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
                avatar_file_ids[chat_id] = new_file_id  # Обновляем file_id в памяти
                return public_url
            return None
        except Exception as e:
            logging.error(f"Ошибка загрузки аватара для чата {chat_id}: {str(e)}")
            return None

    @dp.callback_query(lambda c: c.data.startswith('bind_new_community:'))
    async def process_bind_new_community(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = str(callback_query.from_user.id)
        message_id = callback_query.message.message_id

        await bot.answer_callback_query(callback_query.id)
        await state.set_state(GiveawayStates.binding_communities)
        await state.update_data(giveaway_id=giveaway_id, message_id=message_id)
        pending_channels[user_id] = {'giveaway_id': giveaway_id, 'message_id': message_id}

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]])
        bot_info = await bot.get_me()
        html_message = f"""
Чтобы привязать паблик/канал/группу:  
1. Добавьте бота <code>@{bot_info.username}</code> в администраторы.  
2. Вы должны быть администратором.  
3. Не меняйте права бота при добавлении.  
Бот автоматически обнаружит добавление.
"""
        await send_message_with_image(bot, int(user_id), html_message, reply_markup=keyboard, message_id=message_id)

    @dp.my_chat_member()
    async def bot_added_to_chat(event: ChatMemberUpdated, state: FSMContext):
        chat = event.chat
        user_id = str(event.from_user.id)
        new_status = event.new_chat_member.status

        chat_type_display = "канал" if chat.type == ChatType.CHANNEL else "группа"
        chat_type_db = "channel" if chat.type == ChatType.CHANNEL else "group"
        community_name = chat.title or "Без названия"
        community_username = chat.username or community_name
        community_id = str(chat.id)

        logging.info(
            f"Бот добавлен в {chat_type_display} '{community_name}' (ID: {community_id}), статус: {new_status}")

        if new_status == ChatMemberStatus.LEFT:
            logging.info(f"Бот покинул {chat_type_display} '{community_name}' (ID: {community_id}). Ничего не делаем.")
            return

        if new_status != ChatMemberStatus.ADMINISTRATOR:
            try:
                await bot.get_chat(community_id)
            except Exception as e:
                logging.warning(f"Чат {community_id} недоступен: {str(e)}")
                cursor.execute("DELETE FROM bound_communities WHERE community_id = %s AND user_id = %s",
                               (community_id, user_id))
                cursor.execute("DELETE FROM giveaway_communities WHERE community_id = %s AND user_id = %s",
                               (community_id, user_id))
                conn.commit()
                return

        if new_status == ChatMemberStatus.ADMINISTRATOR:
            try:
                bot_member = await bot.get_chat_member(chat.id, bot.id)
                if isinstance(bot_member, ChatMemberAdministrator):
                    required_permissions = get_required_permissions(chat_type_db)
                    missing_permissions = [perm_name for perm, perm_name in required_permissions.items() if
                                           not getattr(bot_member, perm, False)]
                    if not missing_permissions:
                        # Получаем текущий URL из базы для проверки
                        cursor.execute(
                            "SELECT media_file_ava FROM bound_communities WHERE community_id = %s AND user_id = %s",
                            (community_id, user_id))
                        result = cursor.fetchone()
                        current_url = result[0] if result else None
                        avatar_url = await download_and_save_avatar(community_id, current_url)
                        success = await record_bound_community(user_id, community_username, community_id, chat_type_db,
                                                               community_name, avatar_url)
                        logging.info(
                            f"Фоновая привязка {'успешна' if success else 'не удалась'} для {community_username}")
            except Exception as e:
                logging.warning(
                    f"Не удалось получить данные администратора для чата {community_id}: {str(e)}. Продолжаем с предположением, что бот администратор.")
                cursor.execute("SELECT media_file_ava FROM bound_communities WHERE community_id = %s AND user_id = %s",
                               (community_id, user_id))
                result = cursor.fetchone()
                current_url = result[0] if result else None
                avatar_url = await download_and_save_avatar(community_id, current_url)
                success = await record_bound_community(user_id, community_username, community_id, chat_type_db,
                                                       community_name, avatar_url)
                logging.info(
                    f"Фоновая привязка {'успешна' if success else 'не удалась'} для {community_username} (без проверки прав)")

        state_data = await state.get_data()
        pending_data = pending_channels.get(user_id, {})
        giveaway_id = state_data.get('giveaway_id') or pending_data.get('giveaway_id')
        message_id = state_data.get('message_id') or pending_data.get('message_id')

        if not giveaway_id:
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]])

        if new_status == ChatMemberStatus.ADMINISTRATOR:
            try:
                bot_member = await bot.get_chat_member(chat.id, bot.id)
                if not isinstance(bot_member, ChatMemberAdministrator):
                    await send_message_with_image(bot, int(user_id),
                                                  f"Бот не получил права администратора в {chat_type_display}е '{community_name}'.",
                                                  reply_markup=keyboard, message_id=message_id)
                    return

                required_permissions = get_required_permissions(chat_type_db)
                missing_permissions = [perm_name for perm, perm_name in required_permissions.items() if
                                       not getattr(bot_member, perm, False)]
                if missing_permissions:
                    await send_message_with_image(bot, int(user_id),
                                                  f"Недостаточно прав в {chat_type_display}е '{community_name}': {', '.join(missing_permissions)}",
                                                  reply_markup=keyboard, message_id=message_id)
                    return

                cursor.execute("SELECT media_file_ava FROM bound_communities WHERE community_id = %s AND user_id = %s",
                               (community_id, user_id))
                result = cursor.fetchone()
                current_url = result[0] if result else None
                avatar_url = await download_and_save_avatar(community_id, current_url)
                await handle_successful_binding(community_id, community_username, user_id, giveaway_id, state,
                                                message_id, chat_type_db, chat_type_display, community_name, avatar_url)
            except Exception as e:
                logging.warning(
                    f"Не удалось проверить права администратора для чата {community_id}: {str(e)}. Предполагаем успех.")
                cursor.execute("SELECT media_file_ava FROM bound_communities WHERE community_id = %s AND user_id = %s",
                               (community_id, user_id))
                result = cursor.fetchone()
                current_url = result[0] if result else None
                avatar_url = await download_and_save_avatar(community_id, current_url)
                await handle_successful_binding(community_id, community_username, user_id, giveaway_id, state,
                                                message_id, chat_type_db, chat_type_display, community_name, avatar_url)

            if user_id in pending_channels:
                del pending_channels[user_id]

        elif new_status == ChatMemberStatus.MEMBER:
            await send_message_with_image(bot, int(user_id),
                                          f"Бот добавлен как участник в {chat_type_display} '{community_name}'. Назначьте его администратором.",
                                          reply_markup=keyboard, message_id=message_id)

    async def handle_successful_binding(community_id: str, community_username: str, user_id: str, giveaway_id: str,
                                        state: FSMContext, message_id: int, chat_type_db: str, chat_type_display: str,
                                        community_name: str, avatar_url: str = None):
        cursor.execute(
            "SELECT * FROM giveaway_communities WHERE giveaway_id = %s AND community_id = %s AND user_id = %s",
            (giveaway_id, community_id, user_id))
        if cursor.fetchone():
            await send_message_with_image(bot, int(user_id),
                                          f"{chat_type_display.capitalize()} '{community_username}' уже привязан.",
                                          reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                                              text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]]),
                                          message_id=message_id)
            return

        await bind_community_to_giveaway(giveaway_id, community_id, community_username, chat_type_db, user_id,
                                         community_name, avatar_url)
        await send_message_with_image(bot, int(user_id),
                                      f"{chat_type_display.capitalize()} '{community_username}' успешно привязан!",
                                      reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
                                          text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]]),
                                      message_id=message_id)
        await state.clear()

    async def record_bound_community(user_id: str, community_username: str, community_id: str, community_type: str,
                                     community_name: str, media_file_ava: str = None):
        try:
            data = {'user_id': user_id, 'community_username': community_username, 'community_id': community_id,
                    'community_type': community_type, 'community_name': community_name}
            if media_file_ava:
                data['media_file_ava'] = media_file_ava

            cursor.execute("SELECT * FROM bound_communities WHERE community_id = %s AND user_id = %s",
                           (community_id, user_id))
            if cursor.fetchone():
                update_columns = ', '.join([f"{key} = %s" for key in data.keys()])
                cursor.execute(
                    f"UPDATE bound_communities SET {update_columns} WHERE community_id = %s AND user_id = %s",
                    (*data.values(), community_id, user_id))
            else:
                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))
                cursor.execute(f"INSERT INTO bound_communities ({columns}) VALUES ({placeholders})",
                               tuple(data.values()))
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Ошибка записи в bound_communities: {str(e)}")
            conn.rollback()
            return False

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username, community_type, user_id,
                                         community_name, avatar_url=None):
        data = {"giveaway_id": giveaway_id, "community_id": community_id, "community_username": community_username,
                "community_type": community_type, "user_id": user_id, "community_name": community_name}
        if avatar_url:
            data["media_file_ava"] = avatar_url
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['%s'] * len(data))
        cursor.execute(f"INSERT INTO giveaway_communities ({columns}) VALUES ({placeholders})", tuple(data.values()))
        conn.commit()

    def get_required_permissions(chat_type: str):
        if chat_type == "channel":
            return {'can_post_messages': 'Публикация сообщений', 'can_edit_messages': 'Редактирование сообщений',
                    'can_invite_users': 'Добавление подписчиков'}
        return {'can_delete_messages': 'Удаление сообщений', 'can_invite_users': 'Пригласительные ссылки',
                'can_pin_messages': 'Закрепление сообщений'}

    async def check_and_update_avatars():
        while True:
            try:
                cursor.execute("SELECT community_id, user_id, media_file_ava FROM bound_communities")
                communities = cursor.fetchall()
                for community_id, user_id, current_url in communities:
                    try:
                        chat_info = await bot.get_chat(community_id)
                        if not chat_info.photo:
                            if current_url:  # Если аватарка была, а теперь удалена
                                cursor.execute(
                                    "UPDATE bound_communities SET media_file_ava = NULL WHERE community_id = %s AND user_id = %s",
                                    (community_id, user_id))
                                conn.commit()
                                logging.info(f"Аватарка удалена для сообщества {community_id}")
                            continue

                        new_url = await download_and_save_avatar(community_id, current_url)
                        if new_url and new_url != current_url:  # Если аватарка изменилась
                            cursor.execute(
                                "UPDATE bound_communities SET media_file_ava = %s WHERE community_id = %s AND user_id = %s",
                                (new_url, community_id, user_id))
                            conn.commit()
                            logging.info(f"Аватарка обновлена для сообщества {community_id}: {new_url}")
                    except Exception as e:
                        logging.error(f"Ошибка при проверке аватарки для сообщества {community_id}: {str(e)}")
            except Exception as e:
                logging.error(f"Ошибка в check_and_update_avatars: {str(e)}")
            await asyncio.sleep(36000)  # Проверка каждые 60 минут

    # Регистрация задачи при запуске бота
    @dp.startup()
    async def on_startup():
        logging.info("Бот запущен, стартуем фоновую задачу проверки аватарок")
        asyncio.create_task(check_and_update_avatars())
