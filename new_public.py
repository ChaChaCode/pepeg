from aiogram import Bot, Dispatcher, types
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.filters import ChatMemberUpdatedFilter, IS_MEMBER
from aiogram.types import ChatMemberUpdated, InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberAdministrator
from supabase import create_client, Client
from utils import send_message_with_image
from aiogram.enums import ChatMemberStatus
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

# Bot configuration and initialization
BOT_TOKEN = '7908502974:AAHypTBbfW-c9JR94HNYFLL9ZcN-2LaJFoU'
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Supabase configuration
supabase_url = 'https://olbnxtiigxqcpailyecq.supabase.co'
supabase_key = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im9sYm54dGlpZ3hxY3BhaWx5ZWNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzAxMjQwNzksImV4cCI6MjA0NTcwMDA3OX0.dki8TuMUhhFCoUVpHrcJo4V1ngKEnNotpLtZfRjsePY'
supabase: Client = create_client(supabase_url, supabase_key)


class GiveawayStates(StatesGroup):
    binding_communities = State()


# Dictionary to store pending channel bindings
pending_channels = {}


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

        logging.info(f"User {user_id} started binding process for giveaway {giveaway_id}")

        await bot.answer_callback_query(callback_query.id)

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
        ])

        bot_info = await bot.get_me()
        name_public = f"@{bot_info.username}"
        html_message = f"""
Чтобы привязать паблик, добавьте этого бота <code>{name_public}</code> в администраторы вашего паблика или группы.
Бот автоматически обнаружит новый паблик/группу и привяжет его к розыгрышу.\n
Пожалуйста, убедитесь, что при добавлении бота как администратора вы предоставили следующие права:\n
- Публикация сообщений
- Редактирование сообщений
- Добавление подписчиков\n
Также, не находясь в состоянии привязки паблика, вы можете добавить бота в качестве администратора сразу в нескольких пабликах. После этого, перейдя в состояние привязки, вы увидите все паблики, в которых бот уже назначен администратором.\n\n
Эти права должны быть включены автоматически при добавлении бота. Не изменяйте стандартный набор прав при добавлении.\n
"""
        await send_message_with_image(
            bot,
            user_id,
            html_message,
            reply_markup=keyboard,
            parse_mode='HTML',
            message_id=message_id
            )

    @dp.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_MEMBER))
    async def bot_added_as_admin(event: ChatMemberUpdated, state: FSMContext):
        chat = event.chat
        user_id = event.from_user.id

        logging.info(
            f"Статус бота изменен в канале/группе {chat.id} пользователем {user_id}. Новый статус: {event.new_chat_member.status}")

        state_data = await state.get_data()
        pending_data = pending_channels.get(user_id, {})

        giveaway_id = state_data.get('giveaway_id') or pending_data.get('giveaway_id')
        message_id = state_data.get('message_id') or pending_data.get('message_id')

        if not giveaway_id:
            # Бот добавлен не во время процесса привязки к розыгрышу
            if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
                # Записываем информацию в базу данных
                await record_bound_community(user_id, chat.username, str(chat.id))
                logging.info(
                    f"Бот добавлен как администратор в {chat.username} (ID: {chat.id}) пользователем {user_id}")
            return

        # Всегда сохраняем giveaway_id и message_id в состоянии
        await state.update_data(giveaway_id=giveaway_id, message_id=message_id)

        if event.new_chat_member.status == ChatMemberStatus.ADMINISTRATOR:
            # Проверяем права бота
            bot_member = await bot.get_chat_member(chat.id, bot.id)
            if isinstance(bot_member, ChatMemberAdministrator):
                required_permissions = {
                    'can_post_messages': 'Публикация сообщений',
                    'can_edit_messages': 'Редактирование сообщений',
                    'can_invite_users': 'Добавление подписчиков'
                }

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
                        f"Вы ограничили права боту как администратору. Пожалуйста, удалите бота из администраторов и повторите добавление бота, предоставив следующие права:\n\n{missing_perms_str}\n\nЭти права должны быть включены автоматически при добавлении бота как администратора. Пожалуйста, не изменяйте стандартный набор прав при добавлении.",
                        reply_markup=keyboard,
                        message_id=message_id
                    )
                else:
                    await handle_successful_binding(chat.id, chat.username, user_id, giveaway_id, state, message_id)
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
                "Бот был добавлен как обычный участник, а не как администратор. Пожалуйста, добавьте бота как администратора с необходимыми правами.",
                reply_markup=keyboard,
                message_id=message_id
            )

    async def handle_successful_binding(channel_id: int, channel_username: str, user_id: int, giveaway_id: str,
                                        state: FSMContext, message_id: int):
        try:
            response = supabase.table('giveaway_communities').select('*').eq('giveaway_id', giveaway_id).eq(
                'community_id', str(channel_id)).execute()

            if response.data:
                await send_message_with_image(
                    bot,
                    user_id,
                    f"Паблик \"{channel_username}\" уже привязан к этому розыгрышу.",
                    message_id=message_id
                )
                return

            await bind_community_to_giveaway(giveaway_id, str(channel_id), channel_username)
            await record_bound_community(user_id, channel_username, str(channel_id))

            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Назад", callback_data=f"bind_communities:{giveaway_id}")]
            ])

            await send_message_with_image(
                bot,
                user_id,
                f"Паблик \"{channel_username}\" успешно привязан к розыгрышу!",
                reply_markup=keyboard,
                message_id=message_id
            )
            await state.clear()
            logging.info(f"Successfully bound channel {channel_id} to giveaway {giveaway_id} for user {user_id}")
        except Exception as e:
            logging.error(f"Error in handle_successful_binding: {str(e)}")
            await send_message_with_image(
                bot,
                user_id,
                "Произошла ошибка при привязке паблика. Пожалуйста, попробуйте еще раз.",
                message_id=message_id
            )

    async def fetch_latest_giveaway(user_id: int):
        try:
            response = supabase.table('giveaways').select('id').eq('creator_id', str(user_id)).order('created_at',
                                                                                                     desc=True).limit(
                1).execute()
            if response.data:
                giveaway_id = response.data[0]['id']
                logging.info(f"Retrieved latest giveaway ID {giveaway_id} for user {user_id}")
                return giveaway_id, None  # We don't have a message_id in this case
            else:
                logging.warning(f"No giveaways found for user {user_id}")
                return None, None
        except Exception as e:
            logging.error(f"Error fetching latest giveaway: {str(e)}")
            return None, None

    async def record_bound_community(user_id: int, community_username: str, community_id: str):
        try:
            response = supabase.table('bound_communities').select('*').eq('user_id', user_id).eq('community_id',
                                                                                                 community_id).execute()
            if response.data:
                logging.info(f"Community {community_username} is already recorded for user {user_id}")
                return True

            response = supabase.table('bound_communities').insert({
                'user_id': user_id,
                'community_username': community_username,
                'community_id': community_id
            }).execute()
            if response.data:
                logging.info(f"Bound community recorded: {response.data}")
                return True
            else:
                logging.error(f"Unexpected response format: {response}")
                return False
        except Exception as e:
            logging.error(f"Error recording bound community: {str(e)}")
            return False

    async def bind_community_to_giveaway(giveaway_id, community_id, community_username):
        data = {
            "giveaway_id": giveaway_id,
            "community_id": community_id,
            "community_username": community_username
        }
        response = supabase.table("giveaway_communities").insert(data).execute()
        logging.info(f"Bound community {community_id} to giveaway {giveaway_id}: {response.data}")

