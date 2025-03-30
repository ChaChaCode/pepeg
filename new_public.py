from aiogram import Bot, Dispatcher, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberAdministrator
from utils import send_message_with_image
from aiogram.enums import ChatMemberStatus, ChatType
import logging
import aiohttp
import uuid
import boto3
from botocore.client import Config
from datetime import datetime
import requests
import io

# Настройка логирования с более подробным выводом
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

BOT_TOKEN = '7412394623:AAEkxMj-WqKVpPfduaY8L88YO1I_7zUIsQg'

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
    binding_partner_communities = State()

pending_channels = {}

async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        file_size_mb = len(file_content) / (1024 * 1024)
        logging.info(f"Размер файла: {file_size_mb} МБ")
        if file_size_mb > 5:
            return False, "Файл слишком большой. Максимальный размер: 5 МБ"

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"
        logging.info(f"Генерируем имя файла: {unique_filename}")

        try:
            s3_client.head_bucket(Bucket=YANDEX_BUCKET_NAME)
            logging.info(f"Бакет {YANDEX_BUCKET_NAME} доступен")
        except Exception as bucket_error:
            logging.warning(f"Бакет {YANDEX_BUCKET_NAME} недоступен: {str(bucket_error)}")
            try:
                s3_client.create_bucket(
                    Bucket=YANDEX_BUCKET_NAME,
                    CreateBucketConfiguration={'LocationConstraint': YANDEX_REGION}
                )
                logging.info(f"Бакет {YANDEX_BUCKET_NAME} создан")
            except Exception as create_error:
                logging.error(f"Ошибка создания бакета: {str(create_error)}")
                raise Exception(f"Cannot access or create bucket: {str(create_error)}")

        try:
            s3_client.put_object(
                Bucket=YANDEX_BUCKET_NAME,
                Key=unique_filename,
                Body=io.BytesIO(file_content),
                ContentType="image/jpeg",
                ACL='public-read'
            )
            logging.info(f"Файл {unique_filename} успешно загружен в Yandex Cloud напрямую")
        except Exception as s3_error:
            logging.warning(f"Ошибка прямой загрузки: {str(s3_error)}")
            presigned_url = s3_client.generate_presigned_url(
                'put_object',
                Params={'Bucket': YANDEX_BUCKET_NAME, 'Key': unique_filename, 'ContentType': 'image/jpeg'},
                ExpiresIn=3600
            )
            headers = {'Content-Type': 'image/jpeg'}
            response = requests.put(presigned_url, data=file_content, headers=headers)

            if response.status_code != 200:
                logging.error(f"Ошибка загрузки через presigned URL: {response.status_code}")
                return False, f"Ошибка загрузки через presigned URL: {response.status_code}"

            logging.info(f"Файл {unique_filename} успешно загружен через presigned URL")

        public_url = f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/{unique_filename}"
        logging.info(f"Сгенерирован URL: {public_url}")
        return True, public_url

    except Exception as e:
        logging.error(f"Ошибка загрузки в Yandex Cloud: {str(e)}")
        return False, str(e)

def register_new_public(dp: Dispatcher, bot: Bot, conn, cursor):
    @dp.callback_query(lambda c: c.data.startswith('bind_new_community:'))
    async def process_bind_new_community(callback_query: types.CallbackQuery, state: FSMContext):
        giveaway_id = callback_query.data.split(':')[1]
        user_id = callback_query.from_user.id
        message_id = callback_query.message.message_id

        await state.set_state(GiveawayStates.binding_communities)
        await state.update_data(giveaway_id=giveaway_id, message_id=message_id)
        pending_channels[user_id] = {
            'giveaway_id': giveaway_id,
            'message_id': message_id
        }

        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]
        ])

        bot_info = await bot.get_me()
        name_public = f"@{bot_info.username}"
        html_message = f"""
Чтобы привязать паблик/канал/группу к розыгрышу:  

1. Добавьте бота <code>{name_public}</code> в администраторы.  
2. Обязательно: вы должны быть администратором паблика/канала/группы.  
3. При добавлении бота не меняйте права — они настроены автоматически.  

Бот сам обнаружит добавление и привяжет его. Можно заранее добавлять бота в несколько пабликов, а затем привязать их.
"""
        await send_message_with_image(
            bot,
            user_id,
            html_message,
            reply_markup=keyboard,
            parse_mode='HTML',
            message_id=message_id
        )

    async def download_and_save_avatar(chat_id):
        try:
            logging.info(f"Попытка загрузки аватара для чата {chat_id}")
            chat_info = await bot.get_chat(chat_id)
            if not chat_info.photo:
                logging.info(f"У чата {chat_id} нет фото")
                return None

            file_id = chat_info.photo.big_file_id
            logging.info(f"Получен file_id аватара: {file_id}")
            file_info = await bot.get_file(file_id)
            file_path = file_info.file_path
            logging.info(f"Путь к файлу: {file_path}")

            async with aiohttp.ClientSession() as session:
                url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
                logging.info(f"Загрузка аватара с URL: {url}")
                async with session.get(url) as response:
                    if response.status != 200:
                        logging.error(f"Ошибка загрузки аватара, статус: {response.status}")
                        return None
                    file_content = await response.read()
                    logging.info(f"Аватар успешно загружен, размер: {len(file_content)} байт")

            file_name = f"{chat_id}_{uuid.uuid4()}.jpg"
            success, public_url = await upload_to_storage(file_content, file_name)
            if success:
                logging.info(f"Аватар успешно сохранен в Yandex Cloud: {public_url}")
                return public_url
            else:
                logging.error(f"Ошибка при сохранении аватара в Yandex Cloud: {public_url}")
                return None

        except Exception as e:
            logging.error(f"Ошибка в download_and_save_avatar для чата {chat_id}: {str(e)}")
            return None

    @dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_MEMBER))
    async def bot_added_as_admin(event: ChatMemberUpdated, state: FSMContext):
        chat = event.chat
        user_id = event.from_user.id

        chat_type_display = "канал" if chat.type == ChatType.CHANNEL else "группа"
        chat_type_db = "channel" if chat.type == ChatType.CHANNEL else "group"
        community_name = chat.title
        community_username = chat.username if chat.username else community_name
        community_id = str(chat.id)

        # Инициализируем переменные заранее
        required_permissions = get_required_permissions(chat_type_db)
        missing_permissions = []

        # Фоновая привязка: сохраняем сообщество в bound_communities всегда, если бот стал администратором
        if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            if isinstance(bot_member, ChatMemberAdministrator):
                missing_permissions = [
                    perm_name for perm, perm_name in required_permissions.items()
                    if not getattr(bot_member, perm, False)
                ]

                if not missing_permissions:  # Если все права есть
                    avatar_url = await download_and_save_avatar(chat.id)
                    success = await record_bound_community(
                        user_id, community_username, community_id, chat_type_db, community_name, avatar_url
                    )
                    if success:
                        logging.info(
                            f"Сообщество {community_username} успешно сохранено в bound_communities в фоновом режиме")
                    else:
                        logging.error(f"Не удалось сохранить сообщество {community_username} в bound_communities")

        # Проверяем, есть ли активный процесс привязки к розыгрышу
        state_data = await state.get_data()
        pending_data = pending_channels.get(user_id, {})
        giveaway_id = state_data.get('giveaway_id') or pending_data.get('giveaway_id')
        message_id = state_data.get('message_id') or pending_data.get('message_id')

        if not giveaway_id:  # Если нет активного процесса привязки, просто выходим
            return  # Убрали отправку сообщения

        # Если есть активный процесс привязки, продолжаем как раньше
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]
        ])

        if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            if not isinstance(bot_member, ChatMemberAdministrator):
                await send_message_with_image(
                    bot, user_id,
                    f"Бот не получил права администратора в {chat_type_display}е \"{community_name}\".",
                    reply_markup=keyboard,
                    message_id=message_id
                )
                return

            missing_permissions = [
                perm_name for perm, perm_name in required_permissions.items()
                if not getattr(bot_member, perm, False)
            ]

            if missing_permissions:
                missing_perms_str = ', '.join(missing_permissions)
                await send_message_with_image(
                    bot, user_id,
                    f"Недостаточно прав для бота в {chat_type_display}е \"{community_name}\". Требуются: {missing_perms_str}",
                    reply_markup=keyboard,
                    message_id=message_id
                )
                return

            # Если все права есть, используем уже загруженный аватар или загружаем заново
            avatar_url = await download_and_save_avatar(chat.id)
            logging.info(f"Получен URL аватара: {avatar_url}")
            await handle_successful_binding(
                chat.id, community_username, user_id, giveaway_id,
                state, message_id, chat_type_db, chat_type_display, community_name, avatar_url
            )
            if user_id in pending_channels:
                del pending_channels[user_id]

        elif event.new_chat_member.status == ChatMemberStatus.MEMBER:
            await send_message_with_image(
                bot, user_id,
                f"Бот добавлен как участник в {chat_type_display} \"{community_name}\". Назначьте его администратором с полными правами.",
                reply_markup=keyboard,
                message_id=message_id
            )

    async def handle_successful_binding(channel_id: int, community_username: str, user_id: int, giveaway_id: str,
                                        state: FSMContext, message_id: int, chat_type_db: str, chat_type_display: str,
                                        community_name: str, avatar_url: str = None):
        try:
            cursor.execute(
                """
                SELECT * FROM giveaway_communities 
                WHERE giveaway_id = %s AND community_id = %s AND user_id = %s
                """,
                (giveaway_id, str(channel_id), user_id)
            )
            existing = cursor.fetchone()

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]
            ])

            if existing:
                await send_message_with_image(
                    bot, user_id,
                    f"{chat_type_display.capitalize()} \"{community_username}\" уже привязан к этому розыгрышу.",
                    reply_markup=keyboard,
                    message_id=message_id
                )
                return

            logging.info(f"Привязываем сообщество с аватаром: {avatar_url}")
            await bind_community_to_giveaway(giveaway_id, str(channel_id), community_username, chat_type_db, user_id,
                                             community_name, avatar_url)
            # Сообщество уже сохранено в bound_communities на фоне, поэтому здесь только привязка к розыгрышу

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data=f"bind_communities:{giveaway_id}")]
            ])

            await send_message_with_image(
                bot, user_id,
                f"{chat_type_display.capitalize()} \"{community_username}\" успешно привязан к розыгрышу!",
                reply_markup=keyboard,
                message_id=message_id
            )
            await state.clear()

        except Exception as e:
            logging.error(f"Ошибка в handle_successful_binding: {str(e)}")
            await send_message_with_image(
                bot, user_id,
                f"Ошибка при привязке {chat_type_display}а \"{community_username}\". Попробуйте еще раз.",
                message_id=message_id
            )

    async def record_bound_community(user_id: int, community_username: str, community_id: str, community_type: str,
                                     community_name: str, media_file_ava: str = None):
        try:
            cursor.execute(
                """
                SELECT * FROM bound_communities 
                WHERE community_id = %s AND user_id = %s
                """,
                (community_id, user_id)
            )
            existing = cursor.fetchone()

            data = {
                'user_id': user_id,
                'community_username': community_username,
                'community_id': community_id,
                'community_type': community_type,
                'community_name': community_name
            }
            if media_file_ava:
                data['media_file_ava'] = media_file_ava
                logging.info(f"Записываем в bound_communities с аватаром: {media_file_ava}")
            else:
                logging.info("Записываем в bound_communities без аватара")

            if existing:
                # Обновляем существующую запись
                update_columns = ', '.join([f"{key} = %s" for key in data.keys()])
                cursor.execute(
                    f"""
                    UPDATE bound_communities 
                    SET {update_columns}
                    WHERE community_id = %s AND user_id = %s
                    """,
                    (*data.values(), community_id, user_id)
                )
            else:
                # Вставляем новую запись
                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))
                cursor.execute(
                    f"""
                    INSERT INTO bound_communities ({columns})
                    VALUES ({placeholders})
                    """,
                    tuple(data.values())
                )
            conn.commit()
            return True
        except Exception as e:
            logging.error(f"Ошибка при записи привязанного сообщества: {str(e)}")
            conn.rollback()
            return False

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username, community_type, user_id,
                                         community_name, avatar_url=None):
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
            logging.info(f"Записываем в giveaway_communities с аватаром: {avatar_url}")
        else:
            logging.info("Записываем в giveaway_communities без аватара")

        try:
            columns = ', '.join(data.keys())
            placeholders = ', '.join(['%s'] * len(data))
            cursor.execute(
                f"""
                INSERT INTO giveaway_communities ({columns})
                VALUES ({placeholders})
                """,
                tuple(data.values())
            )
            conn.commit()
        except Exception as e:
            logging.error(f"Ошибка при привязке сообщества: {str(e)}")
            if "community_type" in str(e):
                del data["community_type"]
                columns = ', '.join(data.keys())
                placeholders = ', '.join(['%s'] * len(data))
                cursor.execute(
                    f"""
                    INSERT INTO giveaway_communities ({columns})
                    VALUES ({placeholders})
                    """,
                    tuple(data.values())
                )
                conn.commit()
            else:
                conn.rollback()
                raise e

    def get_required_permissions(chat_type: str):
        if chat_type == "channel":
            return {
                'can_post_messages': 'Публикация сообщений',
                'can_edit_messages': 'Редактирование сообщений',
                'can_invite_users': 'Добавление подписчиков'
            }
        else:  # group
            return {
                'can_delete_messages': 'Удаление сообщений',
                'can_invite_users': 'Пригласительные ссылки',
                'can_pin_messages': 'Закрепление сообщений',
                'can_manage_video_chats': 'Управление видео чатами'
            }
