from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberAdministrator
from supabase import create_client, Client
from utils import send_message_with_image
from aiogram.enums import ChatMemberStatus, ChatType
import logging
import aiohttp
import uuid
import boto3
from botocore.client import Config
import io
from datetime import datetime

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Конфигурация и инициализация бота
BOT_TOKEN = '7908502974:AAHypTBbfW-c9JR94HNYFLL9ZcN-2LaJFoU'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Конфигурация Supabase
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)

# Yandex Cloud S3 configuration
YANDEX_ACCESS_KEY = 'YCAJEDluWSn-XI0tyGyfwfnVL'
YANDEX_SECRET_KEY = 'YCPkR9H9Ucebg6L6eMGvtfKuFIcO_MK7gyiffY6H'
YANDEX_BUCKET_NAME = 'raffle'
YANDEX_ENDPOINT_URL = 'https://storage.yandexcloud.net'
YANDEX_REGION = 'ru-central1'

# Initialize S3 client for Yandex Cloud
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


# Словарь для хранения ожидающих привязки каналов
pending_channels = {}


async def upload_to_storage(file_content: bytes, filename: str) -> tuple[bool, str]:
    try:
        # Check file size (5 MB limit)
        file_size_mb = len(file_content) / (1024 * 1024)
        if file_size_mb > 5:  # 5 MB limit
            return False, f"Файл слишком большой. Максимальный размер: 5 МБ"

        # Generate unique filename to avoid conflicts
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_filename = f"{timestamp}_{filename}"

        # Upload file to Yandex Cloud S3
        try:
            # First, check if the bucket exists
            try:
                s3_client.head_bucket(Bucket=YANDEX_BUCKET_NAME)
                logging.info(f"Bucket {YANDEX_BUCKET_NAME} exists and is accessible")
            except Exception as bucket_error:
                logging.error(f"Bucket error: {str(bucket_error)}")
                # If the bucket doesn't exist, try to create it
                try:
                    logging.info(f"Attempting to create bucket {YANDEX_BUCKET_NAME}")
                    s3_client.create_bucket(
                        Bucket=YANDEX_BUCKET_NAME,
                        CreateBucketConfiguration={'LocationConstraint': YANDEX_REGION}
                    )
                    logging.info(f"Bucket {YANDEX_BUCKET_NAME} created successfully")
                except Exception as create_error:
                    logging.error(f"Failed to create bucket: {str(create_error)}")
                    raise Exception(f"Cannot access or create bucket: {str(create_error)}")

            # Try to upload the file
            logging.info(f"Uploading file {unique_filename} to bucket {YANDEX_BUCKET_NAME}")
            s3_client.put_object(
                Bucket=YANDEX_BUCKET_NAME,
                Key=unique_filename,
                Body=io.BytesIO(file_content),
                ContentType="application/octet-stream",
                ACL='public-read'  # Make the object publicly readable
            )

            # Generate public URL for the uploaded file
            public_url = f"{YANDEX_ENDPOINT_URL}/{YANDEX_BUCKET_NAME}/{unique_filename}"

            logging.info(f"File uploaded successfully to Yandex Cloud: {unique_filename}")
            logging.info(f"Public URL: {public_url}")

            return True, public_url

        except Exception as s3_error:
            logging.error(f"Yandex Cloud S3 upload error: {str(s3_error)}")
            raise Exception(f"Failed to upload to Yandex Cloud: {str(s3_error)}")

    except Exception as e:
        error_msg = str(e)
        logging.error(f"Storage upload error: {error_msg}")
        return False, error_msg


def register_new_public(dp: Dispatcher, bot: Bot, supabase: Client):
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

        logging.info(f"Пользователь {user_id} начал процесс привязки для розыгрыша {giveaway_id}")

        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
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

    @dp.callback_query(lambda c: c.data == 'bind_partner_community')
    async def process_bind_partner_community(callback_query: types.CallbackQuery, state: FSMContext):
        user_id = callback_query.from_user.id
        await state.set_state(GiveawayStates.binding_partner_communities)
        await bot.send_message(user_id,
                               "Пожалуйста, введите Telegram ID вашего партнера, чей паблик вы хотите привязать.")

    @dp.message(GiveawayStates.binding_partner_communities)
    async def handle_partner_id(message: types.Message, state: FSMContext):
        partner_id = message.text
        user_id = message.from_user.id

        if await verify_partnership(user_id, int(partner_id)):
            await state.update_data(partner_id=partner_id)
            await message.reply(
                "Партнерство подтверждено. Пожалуйста, добавьте бота в паблик вашего партнера как администратора.")
        else:
            await message.reply(
                "Партнерство не найдено. Убедитесь, что у вас есть соглашение о партнерстве с этим пользователем.")
            await state.clear()

    # Функция для загрузки и сохранения аватарки канала/группы
    async def download_and_save_avatar(chat_id):
        try:
            # Получаем информацию о чате, включая фото профиля
            chat_info = await bot.get_chat(chat_id)

            # Проверяем, есть ли у чата фото профиля
            if not chat_info.photo:
                logging.info(f"У чата {chat_id} нет фото профиля")
                return None

            # Получаем файл фото профиля
            file_id = chat_info.photo.big_file_id
            file_info = await bot.get_file(file_id)
            file_path = file_info.file_path

            # Скачиваем файл
            file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"

            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status != 200:
                        logging.error(f"Не удалось скачать аватар: {response.status}")
                        return None

                    file_content = await response.read()

            # Генерируем уникальное имя файла
            file_name = f"{chat_id}_{uuid.uuid4()}.jpg"

            # Загружаем файл в Yandex Cloud Storage
            success, public_url = await upload_to_storage(file_content, file_name)

            if not success:
                logging.error(f"Ошибка при загрузке аватара в Yandex Cloud: {public_url}")
                return None

            logging.info(f"Аватар для чата {chat_id} успешно сохранен: {public_url}")
            return public_url

        except Exception as e:
            logging.error(f"Ошибка при загрузке аватара: {str(e)}")
            return None

    @dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_MEMBER))
    async def bot_added_as_admin(event: ChatMemberUpdated, state: FSMContext):
        chat = event.chat
        user_id = event.from_user.id

        chat_type = "channel" if chat.type == ChatType.CHANNEL else "group"
        community_name = chat.title
        community_username = chat.username if chat.username else community_name

        # Загружаем аватар канала/группы
        avatar_url = None
        if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
            avatar_url = await download_and_save_avatar(chat.id)

        if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
            success = await record_bound_community(user_id, community_username, str(chat.id), chat_type, community_name,
                                                   avatar_url)
            if success:
                logging.info(
                    f"Бот добавлен как администратор в {community_username} (ID: {chat.id}) пользователем {user_id}")
                await bot.send_message(user_id, f"Успешно привязано сообщество {community_username}")
            else:
                await bot.send_message(user_id,
                                       f"Произошла ошибка при привязке сообщества {community_username}. Пожалуйста, попробуйте еще раз или обратитесь в поддержку.")

        logging.info(
            f"Статус бота изменен в {chat_type} {chat.id} пользователем {user_id}. Новый статус: {event.new_chat_member.status}")

        state_data = await state.get_data()
        pending_data = pending_channels.get(user_id, {})

        giveaway_id = state_data.get('giveaway_id') or pending_data.get('giveaway_id')
        message_id = state_data.get('message_id') or pending_data.get('message_id')
        partner_id = state_data.get('partner_id')

        if not giveaway_id:
            if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
                await record_bound_community(user_id, community_username, str(chat.id), chat_type, community_name,
                                             avatar_url)
                logging.info(
                    f"Бот добавлен как администратор в {community_username} (ID: {chat.id}) пользователем {user_id}")
            return

        await state.update_data(giveaway_id=giveaway_id, message_id=message_id)

        if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            if isinstance(bot_member, ChatMemberAdministrator):
                required_permissions = get_required_permissions(chat_type)

                missing_permissions = [
                    perm_name for perm, perm_name in required_permissions.items()
                    if not getattr(bot_member, perm, False)
                ]

                if missing_permissions:
                    missing_perms_str = ', '.join(missing_permissions)
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
                    ])
                    await send_message_with_image(
                        bot,
                        user_id,
                        f"Вы ограничили права боту как администратору в {chat_type}. Пожалуйста, предоставьте следующие права:\n\n{missing_perms_str}\n\nПожалуйста, не изменяйте стандартный набор прав при добавлении.",
                        reply_markup=keyboard,
                        message_id=message_id
                    )
                else:
                    if partner_id:
                        if await verify_partnership(user_id, int(partner_id)):
                            await handle_successful_binding(chat.id, community_username, int(partner_id), giveaway_id,
                                                            state, message_id, chat_type, community_name, avatar_url)
                        else:
                            await bot.send_message(user_id,
                                                   "Не удалось подтвердить партнерство. Невозможно привязать паблик партнера.")
                    else:
                        await handle_successful_binding(chat.id, community_username, user_id, giveaway_id, state,
                                                        message_id, chat_type, community_name, avatar_url)
                    if user_id in pending_channels:
                        del pending_channels[user_id]
            else:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
                ])
                await send_message_with_image(
                    bot,
                    user_id,
                    "Бот не был добавлен как администратор или произошла ошибка при проверке прав. Пожалуйста, убедитесь, что вы добавили бота как администратора с необходимыми правами.",
                    reply_markup=keyboard,
                    message_id=message_id
                )
        elif event.new_chat_member.status == ChatMemberStatus.MEMBER:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
            ])
            await send_message_with_image(
                bot,
                user_id,
                "Бот был добавлен как обычный участник.\nТеперь назначьте его администратором без изменения прав — они уже настроены автоматически.",
                reply_markup=keyboard,
                message_id=message_id
            )

    async def handle_successful_binding(channel_id: int, community_username: str, user_id: int, giveaway_id: str,
                                        state: FSMContext, message_id: int, chat_type: str, community_name: str,
                                        avatar_url: str = None):
        try:
            # Check if this specific user has already bound this community to this giveaway
            response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq(
                'community_id', str(channel_id)).eq('user_id', user_id).execute()

            if response.data:
                await send_message_with_image(
                    bot,
                    user_id,
                    f"Вы уже привязали {chat_type} \"{community_username}\" к этому розыгрышу.",
                    message_id=message_id
                )
                return

            await bind_community_to_giveaway(giveaway_id, str(channel_id), community_username, chat_type, user_id,
                                             community_name, avatar_url)
            await record_bound_community(user_id, community_username, str(channel_id), chat_type, community_name,
                                         avatar_url)

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
            ])

            await send_message_with_image(
                bot,
                user_id,
                f"{chat_type.capitalize()} \"{community_username}\" успешно привязан(а) к розыгрышу!",
                reply_markup=keyboard,
                message_id=message_id
            )
            await state.clear()
            logging.info(
                f"Успешно привязан(а) {chat_type} {channel_id} к розыгрышу {giveaway_id} для пользователя {user_id}")
        except Exception as e:
            logging.error(f"Ошибка в handle_successful_binding: {str(e)}")
            await send_message_with_image(
                bot,
                user_id,
                f"Произошла ошибка при привязке {chat_type}а. Пожалуйста, попробуйте еще раз.",
                message_id=message_id
            )

    async def record_bound_community(user_id: int, community_username: str, community_id: str, community_type: str,
                                     community_name: str, media_file_ava: str = None):
        try:
            # Проверяем, существует ли уже запись для этого пользователя и сообщества
            response = supabase.table('bound_communities').select('*').eq('community_id', community_id).eq('user_id',
                                                                                                           user_id).execute()

            if response.data:
                # Если запись существует, обновляем её
                update_data = {
                    'community_username': community_username,
                    'community_type': community_type,
                    'community_name': community_name
                }

                # Добавляем URL аватарки, если он есть
                if media_file_ava:
                    update_data['media_file_ava'] = media_file_ava

                response = supabase.table('bound_communities').update(update_data).eq('community_id', community_id).eq(
                    'user_id', user_id).execute()
                logging.info(f"Обновлена привязка сообщества {community_username} для пользователя {user_id}")
            else:
                # Если записи нет, создаем новую
                insert_data = {
                    'user_id': user_id,
                    'community_username': community_username,
                    'community_id': community_id,
                    'community_type': community_type,
                    'community_name': community_name
                }

                # Добавляем URL аватарки, если он есть
                if media_file_ava:
                    insert_data['media_file_ava'] = media_file_ava

                response = supabase.table('bound_communities').insert(insert_data).execute()
                logging.info(f"Создана новая привязка сообщества {community_username} для пользователя {user_id}")

            return True
        except Exception as e:
            logging.error(f"Ошибка при записи привязанного сообщества: {str(e)}")
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

        # Добавляем URL аватарки, если он есть
        if avatar_url:
            data["media_file_ava"] = avatar_url

        try:
            response = supabase.table("giveaway_communities").insert(data).execute()
            logging.info(
                f"Привязано сообщество {community_id} (тип: {community_type}) к розыгрышу {giveaway_id} пользователем {user_id}: {response.data}")
        except Exception as e:
            logging.error(f"Ошибка при привязке сообщества: {str(e)}")
            if "community_type" in str(e):
                del data["community_type"]
                response = supabase.table("giveaway_communities").insert(data).execute()
                logging.info(
                    f"Привязано сообщество {community_id} к розыгрышу {giveaway_id} пользователем {user_id} без указания типа: {response.data}")
            else:
                raise e

    async def verify_partnership(user_id: int, partner_id: int):
        try:
            response = supabase.table('partnerships').select('*').eq('user_id', user_id).eq('partner_id',
                                                                                            partner_id).execute()
            if response.data:
                logging.info(f"Партнерство подтверждено между пользователями {user_id} и {partner_id}")
                return True
            else:
                logging.info(f"Партнерство не найдено между пользователями {user_id} и {partner_id}")
                return False
        except Exception as e:
            logging.error(f"Ошибка при проверке партнерства: {str(e)}")
            return False

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

