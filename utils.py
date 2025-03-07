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

# Константа с руководством по форматированию
FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>
- Цитата
- Жирный: <b>текст</b>
- Курсив: <i>текст</i>
- Подчёркнутый: <u>текст</u>
- Зачёркнутый: <s>текст</s>
- Моноширинный: <pre>текст</pre>
- Скрытый: <tg-spoiler>текст</tg-spoiler>
- Ссылка: <a href="https://example.com">текст</a>
- Код: <code>текст</code>
- Кастомные эмодзи
</blockquote>
"""

async def send_message_with_image(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None, parse_mode: str = 'HTML', entities=None) -> Message | None:
    image_path = 'image/pepes.png'  # Replace with your image path
    image = FSInputFile(image_path)

    try:
        if message_id:
            # Edit existing message
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
            # Send new message
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
        # If sending/editing with image fails, try sending/editing just the text
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
            # Обновляем запрос: используем 'true' вместо True
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

        # Fetch participants
        response = supabase.table('participations').select('user_id').eq('giveaway_id', giveaway_id).execute()
        participants = response.data if response.data else []

        # Recheck participants
        valid_participants = await recheck_participants(bot, supabase, giveaway_id, participants)

        # Select winners from valid participants
        winners = await select_random_winners(bot, valid_participants, min(len(valid_participants), giveaway['winner_count']))

        # Update giveaway status
        await update_giveaway_status(supabase, giveaway_id, 'false')  # Используем 'false' вместо False

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
        new_giveaway['is_active'] = 'false'  # Используем 'false' вместо False
        new_giveaway['created_at'] = None
        new_giveaway['end_time'] = giveaway['end_time']

        # Insert the new giveaway
        new_giveaway_response = supabase.table('giveaways').insert(new_giveaway).execute()
        new_giveaway_id = new_giveaway_response.data[0]['id']

        # Duplicate giveaway_communities data
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

        # Update the old giveaway
        supabase.table('giveaways').update({
            'user_id': 1,
            'participant_counter_tasks': None,
            'published_messages': None
        }).eq('id', giveaway_id).execute()

        logging.info(f"Giveaway {giveaway_id} ended and duplicated with new id {new_giveaway_id}")

    except Exception as e:
        logging.error(f"Error in end_giveaway: {str(e)}")

async def recheck_participants(bot: Bot, supabase: Client, giveaway_id: str, participants: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    valid_participants = []
    giveaway_communities = await get_giveaway_communities(supabase, giveaway_id)

    for participant in participants:
        user_id = participant['user_id']
        is_valid = True

        for community in giveaway_communities:
            try:
                member = await bot.get_chat_member(chat_id=community['community_id'], user_id=user_id)
                if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                    is_valid = False
                    break
            except Exception as e:
                logging.error(f"Error checking membership for user {user_id} in community {community['community_id']}: {str(e)}")
                is_valid = False
                break

        if is_valid:
            valid_participants.append(participant)
        else:
            supabase.table('participations').delete().eq('giveaway_id', giveaway_id).eq('user_id', user_id).execute()

    return valid_participants

async def notify_winners_and_publish_results(bot: Bot, supabase: Client, giveaway: Dict[str, Any], winners: List[Dict[str, Any]]):
    response = supabase.table('giveaway_communities').select('community_id').eq('giveaway_id', giveaway['id']).execute()
    if not response.data:
        logging.error(f"Error fetching communities: No communities found")
        return
    communities = response.data

    if winners:
        winners_list = ', '.join([f"<a href='tg://user?id={w['user_id']}'>@{w['username']}</a>" for w in winners])
        result_message = f"""
<b><tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji> Розыгрыш завершен! <tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji></b>

<b>{giveaway['name']}</b>

<b>Победители:</b> 
<blockquote expandable>{winners_list}</blockquote>

<i>Поздравляем победителей!</i>
"""
    else:
        result_message = f"""
<b><tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji> Розыгрыш завершен! <tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji></b>

<b>{giveaway['name']}</b>

К сожалению, в этом розыгрыше не было участников.
"""

    if winners and len(winners) < giveaway['winner_count']:
        result_message += f"""
<u>Внимание:</u> Количество участников ({len(winners)}) было меньше, чем количество призовых мест ({giveaway['winner_count']}).
<tg-spoiler>Не все призовые места были распределены.</tg-spoiler>
"""

    keyboard = InlineKeyboardBuilder()
    keyboard.button(text="Результаты", url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway['id']}")

    for community in communities:
        try:
            if giveaway['media_type'] and giveaway['media_file_id']:
                media_types = {
                    'photo': InputMediaPhoto,
                    'gif': InputMediaAnimation,
                    'video': InputMediaVideo
                }
                media_type = media_types.get(giveaway['media_type'])
                if media_type:
                    if giveaway['media_type'] == 'photo':
                        await bot.send_photo(
                            chat_id=int(community['community_id']),
                            photo=giveaway['media_file_id'],
                            caption=result_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    elif giveaway['media_type'] == 'gif':
                        await bot.send_animation(
                            chat_id=int(community['community_id']),
                            animation=giveaway['media_file_id'],
                            caption=result_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
                    elif giveaway['media_type'] == 'video':
                        await bot.send_video(
                            chat_id=int(community['community_id']),
                            video=giveaway['media_file_id'],
                            caption=result_message,
                            reply_markup=keyboard.as_markup(),
                            parse_mode='HTML'
                        )
            else:
                await bot.send_message(
                    chat_id=int(community['community_id']),
                    text=result_message,
                    reply_markup=keyboard.as_markup(),
                    parse_mode='HTML'
                )
        except Exception as e:
            logging.error(f"Error publishing results in community @{community['community_id']}: {e}")

    congrats_response = supabase.table('congratulations').select('place', 'message').eq('giveaway_id', giveaway['id']).execute()
    congrats_messages = {item['place']: item['message'] for item in congrats_response.data}

    for index, winner in enumerate(winners, start=1):
        try:
            congrats_message = congrats_messages.get(index, f"<b>Поздравляем!</b> Вы выиграли в розыгрыше \"<i>{giveaway['name']}</i>\"!")
            keyboard = InlineKeyboardBuilder()
            keyboard.button(
                text="Результаты",
                url=f"https://t.me/PepeGift_Bot/open?startapp={giveaway['id']}"
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

async def select_random_winners(bot: Bot, participants: List[Dict[str, Any]], winner_count: int) -> List[Dict[str, Any]]:
    winners = random.sample(participants, min(winner_count, len(participants)))
    winner_details = []
    for winner in winners:
        try:
            user = await bot.get_chat_member(winner['user_id'], winner['user_id'])
            winner_details.append({
                'user_id': winner['user_id'],
                'username': user.user.username or f"user{winner['user_id']}",
                'name': user.user.first_name
            })
        except Exception as e:
            logging.error(f"Error fetching user details: {e}")
            winner_details.append({
                'user_id': winner['user_id'],
                'username': f"user{winner['user_id']}",
                'name': ""
            })
    return winner_details

async def update_giveaway_status(supabase: Client, giveaway_id: str, is_active: str):
    try:
        # Используем строковое значение 'true' или 'false' вместо булевого
        supabase.table('giveaways').update({'is_active': is_active}).eq('id', giveaway_id).execute()
    except Exception as e:
        logging.error(f"Error updating giveaway status: {str(e)}")

async def get_giveaway_communities(supabase: Client, giveaway_id: str) -> List[Dict[str, Any]]:
    response = supabase.table('giveaway_communities').select('community_id').eq('giveaway_id', giveaway_id).execute()
    return response.data if response.data else []

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
