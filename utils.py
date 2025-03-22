import logging
from aiogram import Bot, types
from aiogram.types import FSInputFile, Message, InputMediaPhoto, InputMediaAnimation, InputMediaVideo
from supabase import Client
import random
import aiogram.exceptions
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any
from aiogram.enums import ChatMemberStatus
from datetime import datetime
import pytz

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>
- Цитата
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://t.me/PepeGift_Bot">текст</a>
- Код: <code>текст</code>
- Кастомные эмодзи <tg-emoji emoji-id='5199885118214255386'>👋</tg-emoji>

Примечание: Максимальное количество кастомных эмодзи, которое может отображать Telegram в одном сообщении, ограничено 100 эмодзи.</blockquote>
"""


async def send_message_with_image(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None,
                                  parse_mode: str = 'HTML', entities=None) -> Message | None:
    image_path = 'image/pepes.png'  # Replace with your image path
    image = FSInputFile(image_path)

    try:
        if message_id:
            return await bot.edit_message_media(
                chat_id=chat_id,
                message_id=message_id,
                media=types.InputMediaPhoto(
                    media=image,
                    caption=text,
                    parse_mode=parse_mode,
                    caption_entities=entities
                ),
                reply_markup=reply_markup
            )
        else:
            return await bot.send_photo(
                chat_id=chat_id,
                photo=image,
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                caption_entities=entities
            )
    except Exception as e:
        logging.error(f"Error in send_message_with_image: {str(e)}")
        try:
            if message_id:
                return await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    entities=entities
                )
            else:
                return await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    entities=entities
                )
        except Exception as text_e:
            logging.error(f"Error sending/editing text message: {str(text_e)}")
        return None


async def check_and_end_giveaways(bot: Bot, supabase: Client):
    while True:
        now = datetime.now(pytz.utc)
        try:
            response = supabase.table('giveaways').select('*').eq('is_active', 'true').execute()
            if response.data:
                for giveaway in response.data:
                    end_time = datetime.fromisoformat(giveaway['end_time'])
                    if end_time <= now:
                        try:
                            await end_giveaway(bot, supabase, giveaway['id'])
                        except Exception as e:
                            logging.error(f"Error ending giveaway {giveaway['id']}: {str(e)}")
            else:
                logging.info("No active giveaways found")
        except Exception as e:
            logging.error(f"Error fetching active giveaways: {str(e)}")

        await asyncio.sleep(30)  # Check every 30 seconds


async def end_giveaway(bot: Bot, supabase: Client, giveaway_id: str):
    try:
        # Fetch giveaway details
        response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        if not response.data:
            logging.error(f"Error fetching giveaway: Giveaway not found")
            return
        giveaway = response.data

        # Fetch all participants with pagination
        participants = []
        limit = 1000
        offset = 0
        while True:
            response = supabase.table('participations').select('user_id').eq('giveaway_id', giveaway_id).limit(
                limit).offset(offset).execute()
            if not response.data:
                break
            participants.extend(response.data)
            offset += limit
            if len(response.data) < limit:
                break

        logging.info(f"Total participants fetched for giveaway {giveaway_id}: {len(participants)}")

        # Select winners with subscription check
        winners = await select_random_winners(bot, participants,
                                              min(len(participants), giveaway['winner_count']),
                                              giveaway_id, supabase)

        # Update giveaway status
        await update_giveaway_status(supabase, giveaway_id, 'false')

        # Save winners (if any)
        if winners:
            for index, winner in enumerate(winners, start=1):
                supabase.table('giveaway_winners').insert({
                    'giveaway_id': giveaway_id,
                    'user_id': winner['user_id'],
                    'username': winner['username'],
                    'name': winner.get('name', ''),
                    'place': index
                }).execute()

        # Notify winners and publish results
        await notify_winners_and_publish_results(bot, supabase, giveaway, winners)

        # Create a new giveaway with the same details
        new_giveaway = giveaway.copy()
        new_giveaway.pop('id', None)
        new_giveaway['is_active'] = 'false'
        new_giveaway['created_at'] = None
        new_giveaway['end_time'] = giveaway['end_time']

        new_giveaway_response = supabase.table('giveaways').insert(new_giveaway).execute()
        new_giveaway_id = new_giveaway_response.data[0]['id']

        congratulations_response = supabase.table('congratulations').select('*').eq('giveaway_id',
                                                                                    giveaway_id).execute()
        if congratulations_response.data:
            new_congratulations = []
            for congrat in congratulations_response.data:
                new_congrat = congrat.copy()
                new_congrat.pop('id', None)
                new_congrat['giveaway_id'] = new_giveaway_id
                new_congratulations.append(new_congrat)

            if new_congratulations:
                supabase.table('congratulations').insert(new_congratulations).execute()

        supabase.table('giveaways').update({
            'user_id': 1,
            'published_messages': None
        }).eq('id', giveaway_id).execute()

        logging.info(f"Giveaway {giveaway_id} ended and duplicated with new id {new_giveaway_id}")

    except Exception as e:
        logging.error(f"Error in end_giveaway: {str(e)}")


async def check_participant(bot: Bot, user_id: int, communities: List[Dict[str, Any]]) -> bool:
    """Проверка подписки участника на все указанные каналы."""
    for community in communities:
        try:
            member = await bot.get_chat_member(chat_id=community['community_id'], user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return False
        except Exception as e:
            logging.error(
                f"Error checking membership for user {user_id} in community {community['community_id']}: {str(e)}")
            return False
    return True


async def select_random_winners(bot: Bot, participants: List[Dict[str, Any]], winner_count: int, giveaway_id: str,
                                supabase: Client) -> List[Dict[str, Any]]:
    # Устанавливаем сид для воспроизводимости
    random.seed(giveaway_id)

    # Получаем список каналов для проверки
    giveaway_communities = await get_giveaway_communities(supabase, giveaway_id)
    if not giveaway_communities:
        logging.warning(f"No communities found for giveaway {giveaway_id}, all participants considered valid")
        shuffled_participants = participants.copy()
        random.shuffle(shuffled_participants)
        winners = random.sample(shuffled_participants, min(winner_count, len(shuffled_participants)))
    else:
        # Параллельная проверка всех участников
        tasks = [check_participant(bot, p['user_id'], giveaway_communities) for p in participants]
        results = await asyncio.gather(*tasks)
        valid_participants = [p for p, valid in zip(participants, results) if valid]

        logging.info(
            f"Found {len(valid_participants)} valid participants out of {len(participants)} for giveaway {giveaway_id}")

        # Перемешиваем и выбираем победителей из валидных
        if valid_participants:
            random.shuffle(valid_participants)
            winners = random.sample(valid_participants, min(winner_count, len(valid_participants)))
        else:
            winners = []
            logging.warning(f"No valid participants found for giveaway {giveaway_id}")

    # Формируем детали победителей
    winner_details = []
    for winner in winners:
        user_id = winner['user_id']
        try:
            user = await bot.get_chat_member(user_id, user_id)
            winner_details.append({
                'user_id': user_id,
                'username': user.user.username or f"user{user_id}",
                'name': user.user.first_name
            })
        except Exception as e:
            logging.error(f"Error fetching user details for {user_id}: {e}")
            winner_details.append({
                'user_id': user_id,
                'username': f"user{user_id}",
                'name': ""
            })

    logging.info(f"Selected winners for giveaway {giveaway_id}: {[w['user_id'] for w in winner_details]}")
    return winner_details


async def update_giveaway_status(supabase: Client, giveaway_id: str, is_active: str):
    try:
        supabase.table('giveaways').update({'is_active': is_active}).eq('id', giveaway_id).execute()
    except Exception as e:
        logging.error(f"Error updating giveaway status: {str(e)}")


async def get_giveaway_communities(supabase: Client, giveaway_id: str) -> List[Dict[str, Any]]:
    response = supabase.table('giveaway_communities').select('community_id').eq('giveaway_id', giveaway_id).execute()
    return response.data if response.data else []


async def notify_winners_and_publish_results(bot: Bot, supabase: Client, giveaway: Dict[str, Any],
                                             winners: List[Dict[str, Any]]):
    participant_counter_tasks = giveaway.get('participant_counter_tasks')
    target_chat_ids = []
    channel_links = []
    if participant_counter_tasks:
        try:
            import json
            tasks = json.loads(participant_counter_tasks)
            target_chat_ids = [task['chat_id'] for task in tasks if 'chat_id' in task]
            for chat_id in set(target_chat_ids):
                try:
                    chat = await bot.get_chat(chat_id)
                    channel_name = chat.title
                    invite_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                    channel_links.append(f"<a href=\"{invite_link}\">{channel_name}</a>")
                except Exception as e:
                    logging.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                    channel_links.append("Неизвестный канал")
        except Exception as e:
            logging.error(f"Error parsing participant_counter_tasks for giveaway {giveaway['id']}: {str(e)}")

    if not target_chat_ids:
        logging.error(f"No valid chat_ids found in participant_counter_tasks for giveaway {giveaway['id']}")
        return

    if winners:
        winners_formatted = []
        for idx, winner in enumerate(winners, start=1):
            medal = ""
            if idx == 1:
                medal = "<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji> "
            elif idx == 2:
                medal = "<tg-emoji emoji-id='5447203607294265305'>🥈</tg-emoji> "
            elif idx == 3:
                medal = "<tg-emoji emoji-id='5453902265922376865'>🥉</tg-emoji> "
            winners_formatted.append(
                f"{medal}{idx}. <a href='tg://user?id={winner['user_id']}'>@{winner['username']}</a>")

        winners_list = '\n'.join(winners_formatted)
        result_message = f"""
<b>Розыгрыш завершен <tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji></b>

<b>{giveaway['name']}</b>

<b>Победители:</b> 
<blockquote expandable>
{winners_list}
</blockquote>
"""
    else:
        result_message = f"""
<b>Розыгрыш завершен</b>

<b>{giveaway['name']}</b>

К сожалению, в этом розыгрыше не было участников.
"""

    if winners and len(winners) < giveaway['winner_count']:
        result_message += f"""
Не все призовые места были распределены.
"""

    if channel_links:
        result_message_for_creator = result_message + f"""
<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji> <b>Результаты опубликованы в:</b> {', '.join(channel_links)}
"""
    else:
        result_message_for_creator = result_message

    channel_keyboard = InlineKeyboardBuilder()
    channel_keyboard.button(text="Результаты", url=f"https://t.me/Snapi/app?startapp={giveaway['id']}")

    for chat_id in target_chat_ids:
        try:
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': InputMediaPhoto,
                    'gif': InputMediaAnimation,
                    'video': InputMediaVideo
                }
                media_type = media_types.get(giveaway['media_type'])
                if media_type:
                    try:
                        if giveaway['media_type'] == 'photo':
                            await bot.send_photo(
                                chat_id=int(chat_id),
                                photo=giveaway['media_file_id'],
                                caption=result_message,
                                reply_markup=channel_keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        elif giveaway['media_type'] == 'gif':
                            await bot.send_animation(
                                chat_id=int(chat_id),
                                animation=giveaway['media_file_id'],
                                caption=result_message,
                                reply_markup=channel_keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        elif giveaway['media_type'] == 'video':
                            await bot.send_video(
                                chat_id=int(chat_id),
                                video=giveaway['media_file_id'],
                                caption=result_message,
                                reply_markup=channel_keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                    except aiogram.exceptions.TelegramBadRequest as e:
                        if "message caption is too long" in str(e).lower():
                            logging.warning(f"Caption too long for media in chat {chat_id}, sending as text instead")
                            await bot.send_message(
                                chat_id=int(chat_id),
                                text=result_message,
                                reply_markup=channel_keyboard.as_markup(),
                                parse_mode='HTML'
                            )
                        else:
                            raise
            else:
                await bot.send_message(
                    chat_id=int(chat_id),
                    text=result_message,
                    reply_markup=channel_keyboard.as_markup(),
                    parse_mode='HTML'
                )
        except Exception as e:
            logging.error(f"Error publishing results in chat {chat_id}: {e}")

    congrats_response = supabase.table('congratulations').select('place', 'message').eq('giveaway_id',
                                                                                        giveaway['id']).execute()
    congrats_messages = {item['place']: item['message'] for item in congrats_response.data}

    for index, winner in enumerate(winners, start=1):
        try:
            congrats_message = congrats_messages.get(index,
                                                     f"<b>Поздравляем!</b> Вы выиграли в розыгрыше \"<i>{giveaway['name']}</i>\"!")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text="Результаты",
                url=f"https://t.me/Snapi/app?startapp={giveaway['id']}"
            )
            keyboard.button(text="В меню", callback_data="back_to_main_menu")
            await bot.send_message(
                chat_id=winner['user_id'],
                text=congrats_message,
                reply_markup=keyboard.as_markup(),
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Error notifying winner {winner['user_id']}: {e}")

    creator_id = giveaway.get('user_id')
    if creator_id:
        creator_keyboard = InlineKeyboardBuilder()
        creator_keyboard.button(text="В меню", callback_data="back_to_main_menu")

        try:
            await send_message_with_image(
                bot,
                chat_id=creator_id,
                text=result_message_for_creator,
                reply_markup=creator_keyboard.as_markup(),
                parse_mode='HTML'
            )
        except Exception as e:
            logging.error(f"Error notifying creator {creator_id}: {str(e)}")


async def check_usernames(bot: Bot, supabase: Client):
    try:
        users_response = supabase.table('users').select('user_id, telegram_username').execute()
        users = users_response.data

        for user in users:
            try:
                chat = await bot.get_chat(user['user_id'])
                current_username = chat.username

                if current_username != user.get('telegram_username'):
                    supabase.table('users').update({
                        'telegram_username': current_username
                    }).eq('user_id', user['user_id']).execute()
                    logging.info(
                        f"Updated username for user {user['user_id']}: {user.get('telegram_username')} -> {current_username}")
            except Exception as e:
                logging.error(f"Error checking user {user['user_id']}: {str(e)}")

        communities_response = supabase.table('bound_communities').select(
            'community_id, community_username, community_name').execute()
        communities = communities_response.data

        for community in communities:
            try:
                chat = await bot.get_chat(community['community_id'])
                current_username = chat.username or chat.title
                current_name = chat.title

                if (current_username != community.get('community_username') or
                        current_name != community.get('community_name')):
                    supabase.table('bound_communities').update({
                        'community_username': current_username,
                        'community_name': current_name
                    }).eq('community_id', community['community_id']).execute()

                    supabase.table('giveaway_communities').update({
                        'community_username': current_username,
                        'community_name': current_name
                    }).eq('community_id', community['community_id']).execute()

                    logging.info(
                        f"Обновлены данные для сообщества {community['community_id']}:\n"
                        f"Username: {community.get('community_username')} -> {current_username}\n"
                        f"Name: {community.get('community_name')} -> {current_name}"
                    )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    community_name = community.get('community_username', 'Неизвестное сообщество')
                    logging.warning(f"Нет доступа к сообществу {community_name} (ID: {community['community_id']}). "
                                    f"Возможно, бот был удален из администраторов или сообщество было удалено.")
                else:
                    logging.error(f"Неожиданная ошибка при проверке сообщества {community['community_id']}: {str(e)}")
            except Exception as e:
                logging.error(f"Ошибка при проверке сообщества {community['community_id']}: {str(e)}")

    except Exception as e:
        logging.error(f"Ошибка в функции check_usernames: {str(e)}")
