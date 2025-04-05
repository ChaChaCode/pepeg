import logging
from aiogram import Bot, types
from aiogram.types import FSInputFile, Message, InputMediaPhoto, InputMediaAnimation, InputMediaVideo
import random
import aiogram.exceptions
import asyncio
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import List, Dict, Any
from aiogram.enums import ChatMemberStatus
from datetime import datetime
import pytz
import json

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FORMATTING_GUIDE = """
Поддерживаемые форматы текста:
<blockquote expandable>- Цитата
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
                                parse_mode: str = 'HTML', entities=None, effect_id: str = None) -> Message | None:
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
                caption_entities=entities,
                message_effect_id=effect_id  # Добавляем effect_id для новых сообщений
            )
    except Exception as e:
        logger.error(f"Error in send_message_with_image: {str(e)}")
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
                    entities=entities,
                    message_effect_id=effect_id  # Добавляем effect_id для текстовых сообщений
                )
        except Exception as text_e:
            logger.error(f"Error sending/editing text message: {str(text_e)}")
        return None

async def check_and_end_giveaways(bot: Bot, conn, cursor):
    while True:
        now = datetime.now(pytz.utc)
        try:
            cursor.execute("SELECT * FROM giveaways WHERE is_active = %s", ('true',))
            giveaways = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]
            giveaways = [dict(zip(columns, row)) for row in giveaways]

            if giveaways:
                for giveaway in giveaways:
                    end_time = giveaway['end_time']
                    if isinstance(end_time, str):
                        try:
                            end_time = datetime.fromisoformat(end_time)
                        except ValueError as ve:
                            logger.error(f"Invalid end_time format for giveaway {giveaway['id']}: {str(ve)}")
                            continue
                    if end_time <= now:
                        try:
                            await end_giveaway(bot, conn, cursor, giveaway['id'])
                        except Exception as e:
                            logger.error(f"Error ending giveaway {giveaway['id']}: {str(e)}")
            # Убираем логирование "No active giveaways found"
            # else:
            #     logger.info("No active giveaways found")
        except Exception as e:
            logger.error(f"Error fetching active giveaways: {str(e)}")

        await asyncio.sleep(30)  # Check every 30 seconds

async def end_giveaway(bot: Bot, conn, cursor, giveaway_id: str):
    try:
        # Fetch giveaway details
        cursor.execute("SELECT * FROM giveaways WHERE id = %s", (giveaway_id,))
        giveaway = cursor.fetchone()
        if not giveaway:
            logger.error(f"Error fetching giveaway: Giveaway {giveaway_id} not found")
            return
        columns = [desc[0] for desc in cursor.description]
        giveaway = dict(zip(columns, giveaway))
        logger.debug(f"Ending giveaway {giveaway_id}: {giveaway}")

        # Fetch all participants with pagination
        participants = []
        limit = 1000
        offset = 0
        while True:
            cursor.execute(
                "SELECT user_id FROM participations WHERE giveaway_id = %s LIMIT %s OFFSET %s",
                (giveaway_id, limit, offset)
            )
            batch = cursor.fetchall()
            if not batch:
                break
            participants.extend([{'user_id': row[0]} for row in batch])
            offset += limit
            if len(batch) < limit:
                break

        logger.info(f"Total participants fetched for giveaway {giveaway_id}: {len(participants)}")

        # Select winners with subscription check
        winners = await select_random_winners(bot, participants,
                                              min(len(participants), giveaway['winner_count']),
                                              giveaway_id, conn, cursor)

        # Update giveaway status to mark it as completed
        cursor.execute(
            "UPDATE giveaways SET is_active = %s, is_completed = %s WHERE id = %s",
            ('false', 'true', giveaway_id)
        )
        conn.commit()
        logger.info(f"Giveaway {giveaway_id} marked as completed (is_active = 'false', is_completed = 'true')")

        # Save winners (if any)
        if winners:
            for index, winner in enumerate(winners, start=1):
                cursor.execute(
                    """
                    INSERT INTO giveaway_winners (giveaway_id, user_id, username, name, place)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (giveaway_id, winner['user_id'], winner['username'], winner.get('name', ''), index)
                )
            conn.commit()
            logger.info(f"Saved {len(winners)} winners for giveaway {giveaway_id}")

        # Notify winners and publish results
        await notify_winners_and_publish_results(bot, conn, cursor, giveaway, winners)

        # Create a new giveaway template with the same details (for future use)
        new_giveaway = giveaway.copy()
        new_giveaway.pop('id', None)  # Remove old ID to create a new record
        new_giveaway['is_active'] = 'false'
        new_giveaway['is_completed'] = 'false'  # This is a template, not a completed giveaway
        new_giveaway['created_at'] = None
        new_giveaway['end_time'] = giveaway['end_time']

        # Convert fields that may contain dicts or lists to JSON strings
        for key, value in new_giveaway.items():
            if isinstance(value, (dict, list)):
                logger.debug(f"Converting field {key} to JSON string: {value}")
                new_giveaway[key] = json.dumps(value)

        logger.debug(f"Prepared new_giveaway for insertion: {new_giveaway}")

        columns = list(new_giveaway.keys())
        placeholders = ', '.join(['%s'] * len(columns))
        cursor.execute(
            f"INSERT INTO giveaways ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",
            list(new_giveaway.values())
        )
        new_giveaway_id = cursor.fetchone()[0]
        logger.info(f"Created new giveaway template with id {new_giveaway_id} based on giveaway {giveaway_id}")

        # Copy congratulations to the new giveaway
        cursor.execute("SELECT * FROM congratulations WHERE giveaway_id = %s", (giveaway_id,))
        congratulations = cursor.fetchall()
        if congratulations:
            congrats_columns = [desc[0] for desc in cursor.description]
            for congrat in congratulations:
                congrat_dict = dict(zip(congrats_columns, congrat))
                congrat_dict.pop('id', None)
                congrat_dict['giveaway_id'] = new_giveaway_id
                columns = list(congrat_dict.keys())
                placeholders = ', '.join(['%s'] * len(columns))
                cursor.execute(
                    f"INSERT INTO congratulations ({', '.join(columns)}) VALUES ({placeholders})",
                    list(congrat_dict.values())
                )
            conn.commit()
            logger.info(f"Copied congratulations to new giveaway template {new_giveaway_id}")

        logger.info(f"Giveaway {giveaway_id} ended and duplicated as template with new id {new_giveaway_id}")

    except Exception as e:
        logger.error(f"Error in end_giveaway for giveaway {giveaway_id}: {str(e)}")
        conn.rollback()

async def check_participant(bot: Bot, user_id: int, communities: List[Dict[str, Any]]) -> bool:
    """Проверка подписки участника на все указанные каналы."""
    for community in communities:
        try:
            member = await bot.get_chat_member(chat_id=community['community_id'], user_id=user_id)
            if member.status not in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR]:
                return False
        except Exception as e:
            logger.error(
                f"Error checking membership for user {user_id} in community {community['community_id']}: {str(e)}")
            return False
    return True

async def select_random_winners(bot: Bot, participants: List[Dict[str, Any]], winner_count: int, giveaway_id: str,
                                conn, cursor) -> List[Dict[str, Any]]:
    # Устанавливаем сид для воспроизводимости
    random.seed(giveaway_id)

    # Получаем список каналов для проверки
    giveaway_communities = await get_giveaway_communities(conn, cursor, giveaway_id)
    if not giveaway_communities:
        logger.warning(f"No communities found for giveaway {giveaway_id}, all participants considered valid")
        shuffled_participants = participants.copy()
        random.shuffle(shuffled_participants)
        winners = random.sample(shuffled_participants, min(winner_count, len(shuffled_participants)))
    else:
        # Параллельная проверка всех участников
        tasks = [check_participant(bot, p['user_id'], giveaway_communities) for p in participants]
        results = await asyncio.gather(*tasks)
        valid_participants = [p for p, valid in zip(participants, results) if valid]

        logger.info(
            f"Found {len(valid_participants)} valid participants out of {len(participants)} for giveaway {giveaway_id}")

        # Перемешиваем и выбираем победителей из валидных
        if valid_participants:
            random.shuffle(valid_participants)
            winners = random.sample(valid_participants, min(winner_count, len(valid_participants)))
        else:
            winners = []
            logger.warning(f"No valid participants found for giveaway {giveaway_id}")

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
            logger.error(f"Error fetching user details for {user_id}: {e}")
            winner_details.append({
                'user_id': user_id,
                'username': f"user{user_id}",
                'name': ""
            })

    logger.info(f"Selected winners for giveaway {giveaway_id}: {[w['user_id'] for w in winner_details]}")
    return winner_details

async def update_giveaway_status(conn, cursor, giveaway_id: str, is_active: str):
    try:
        cursor.execute(
            "UPDATE giveaways SET is_active = %s WHERE id = %s",
            (is_active, giveaway_id)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Error updating giveaway status for giveaway {giveaway_id}: {str(e)}")
        conn.rollback()

async def get_giveaway_communities(conn, cursor, giveaway_id: str) -> List[Dict[str, Any]]:
    try:
        cursor.execute(
            "SELECT community_id FROM giveaway_communities WHERE giveaway_id = %s",
            (giveaway_id,)
        )
        rows = cursor.fetchall()
        return [{'community_id': row[0]} for row in rows]
    except Exception as e:
        logger.error(f"Error fetching giveaway communities for giveaway {giveaway_id}: {str(e)}")
        return []

async def notify_winners_and_publish_results(bot: Bot, conn, cursor, giveaway: Dict[str, Any],
                                             winners: List[Dict[str, Any]]):
    participant_counter_tasks = giveaway.get('participant_counter_tasks')
    target_chat_ids = []
    channel_links = []
    if participant_counter_tasks:
        try:
            tasks = participant_counter_tasks if isinstance(participant_counter_tasks, list) else []
            target_chat_ids = [task['chat_id'] for task in tasks if 'chat_id' in task]
            for chat_id in set(target_chat_ids):
                try:
                    chat = await bot.get_chat(chat_id)
                    channel_name = chat.title
                    invite_link = chat.invite_link if chat.invite_link else f"https://t.me/c/{str(chat_id).replace('-100', '')}"
                    channel_links.append(f"<a href=\"{invite_link}\">{channel_name}</a>")
                except Exception as e:
                    logger.error(f"Не удалось получить информацию о канале {chat_id}: {str(e)}")
                    channel_links.append("Неизвестный канал")
        except Exception as e:
            logger.error(f"Error processing participant_counter_tasks for giveaway {giveaway['id']}: {str(e)}")

    if not target_chat_ids:
        logger.warning(f"No target chat_ids found in participant_counter_tasks for giveaway {giveaway['id']}. Results will not be published in channels.")

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

{giveaway['name']}

<b>Победители:</b> 
<blockquote expandable>
{winners_list}
</blockquote>
"""
    else:
        result_message = f"""
<b>Розыгрыш завершен</b>

{giveaway['name']}

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
                            logger.warning(f"Caption too long for media in chat {chat_id}, sending as text instead")
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
            logger.error(f"Error publishing results in chat {chat_id}: {e}")

    # Fetch congratulations messages
    cursor.execute("SELECT place, message FROM congratulations WHERE giveaway_id = %s", (giveaway['id'],))
    congrats_rows = cursor.fetchall()
    congrats_messages = {row[0]: row[1] for row in congrats_rows}

    # Указываем effect_id для сообщений победителям
    WINNER_EFFECT_ID = "5283179205691990448"

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
                parse_mode='HTML',
                message_effect_id=WINNER_EFFECT_ID  # Добавляем эффект
            )
            logger.info(f"Sent winning message with effect_id {WINNER_EFFECT_ID} to user {winner['user_id']}")
        except Exception as e:
            logger.error(f"Error notifying winner {winner['user_id']}: {e}")

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
            logger.error(f"Error notifying creator {creator_id}: {str(e)}")

async def check_usernames(bot: Bot, conn, cursor):
    try:
        # Fetch users
        cursor.execute("SELECT user_id, telegram_username FROM users")
        users = cursor.fetchall()
        users = [{'user_id': row[0], 'telegram_username': row[1]} for row in users]

        for user in users:
            try:
                chat = await bot.get_chat(user['user_id'])
                current_username = chat.username

                if current_username != user.get('telegram_username'):
                    cursor.execute(
                        "UPDATE users SET telegram_username = %s WHERE user_id = %s",
                        (current_username, user['user_id'])
                    )
                    conn.commit()
                    logger.info(
                        f"Updated username for user {user['user_id']}: {user.get('telegram_username')} -> {current_username}")
            except Exception as e:
                logger.error(f"Error checking user {user['user_id']}: {str(e)}")

        # Fetch communities
        cursor.execute("SELECT community_id, community_username, community_name FROM bound_communities")
        communities = cursor.fetchall()
        communities = [
            {'community_id': row[0], 'community_username': row[1], 'community_name': row[2]}
            for row in communities
        ]

        for community in communities:
            try:
                chat = await bot.get_chat(community['community_id'])
                current_username = chat.username or chat.title
                current_name = chat.title

                if (current_username != community.get('community_username') or
                        current_name != community.get('community_name')):
                    cursor.execute(
                        """
                        UPDATE bound_communities 
                        SET community_username = %s, community_name = %s 
                        WHERE community_id = %s
                        """,
                        (current_username, current_name, community['community_id'])
                    )
                    cursor.execute(
                        """
                        UPDATE giveaway_communities 
                        SET community_username = %s, community_name = %s 
                        WHERE community_id = %s
                        """,
                        (current_username, current_name, community['community_id'])
                    )
                    conn.commit()

                    logger.info(
                        f"Обновлены данные для сообщества {community['community_id']}:\n"
                        f"Username: {community.get('community_username')} -> {current_username}\n"
                        f"Name: {community.get('community_name')} -> {current_name}"
                    )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "chat not found" in str(e).lower():
                    community_name = community.get('community_username', 'Неизвестное сообщество')
                    logger.warning(f"Нет доступа к сообществу {community_name} (ID: {community['community_id']}). "
                                    f"Возможно, бот был удален из администраторов или сообщество было удалено.")
                else:
                    logger.error(f"Неожиданная ошибка при проверке сообщества {community['community_id']}: {str(e)}")
            except Exception as e:
                logger.error(f"Ошибка при проверке сообщества {community['community_id']}: {str(e)}")

    except Exception as e:
        logger.error(f"Ошибка в функции check_usernames: {str(e)}")
        conn.rollback()
