import logging
from aiogram import Bot, types
from aiogram.types import FSInputFile, Message
from supabase import Client
import random
from datetime import datetime
import pytz
import aiogram.exceptions

async def send_message_with_image(bot: Bot, chat_id: int, text: str, reply_markup=None, message_id: int = None, parse_mode: str = None) -> Message | None:
    image_path = 'image/pepes.png'  # Replace with your image path
    image = FSInputFile(image_path)

    try:
        if message_id:
            try:
                # Try to edit the existing message
                return await bot.edit_message_media(
                    chat_id=chat_id,
                    message_id=message_id,
                    media=types.InputMediaPhoto(media=image, caption=text, parse_mode=parse_mode),
                    reply_markup=reply_markup
                )
            except aiogram.exceptions.TelegramBadRequest as e:
                if "message to edit not found" in str(e).lower():
                    logging.warning(f"Message to edit not found: {e}. Sending a new message.")
                    # If the message to edit is not found, send a new message
                    return await bot.send_photo(chat_id=chat_id, photo=image, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
                else:
                    raise
        else:
            # Send a new message if no message_id is provided
            return await bot.send_photo(chat_id=chat_id, photo=image, caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logging.error(f"Error in send_message_with_image: {str(e)}")
        # If sending with image fails, try sending just the text
        try:
            return await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as text_e:
            logging.error(f"Error sending text message: {str(text_e)}")
        return None


async def check_and_end_giveaways(bot: Bot, supabase: Client):
    try:
        now = datetime.now(pytz.UTC)
        response = supabase.table('giveaways').select('*').eq('is_active', True).lte('end_time',
                                                                                     now.isoformat()).execute()

        for giveaway in response.data:
            await end_giveaway(bot, supabase, giveaway['id'])
    except Exception as e:
        logging.error(f"Error in check_and_end_giveaways: {str(e)}")


async def end_giveaway(bot: Bot, supabase: Client, giveaway_id: str):
    try:
        # Получаем информацию о розыгрыше
        giveaway_response = supabase.table('giveaways').select('*').eq('id', giveaway_id).single().execute()
        giveaway = giveaway_response.data

        if not giveaway or not giveaway['is_active']:
            logging.warning(f"Giveaway {giveaway_id} not found or already ended")
            return

        # Получаем участников
        participants_response = supabase.table('participations').select('user_id').eq('giveaway_id',
                                                                                      giveaway_id).execute()
        participants = [p['user_id'] for p in participants_response.data]

        if not participants:
            logging.warning(f"No participants found for giveaway {giveaway_id}")
            await update_giveaway_status(supabase, giveaway_id, False)
            return

        # Выбираем победителей
        winners = random.sample(participants, min(giveaway['winner_count'], len(participants)))

        # Обновляем статус розыгрыша
        await update_giveaway_status(supabase, giveaway_id, False)

        # Отправляем сообщения победителям
        for i, winner in enumerate(winners, 1):
            congrats_response = supabase.table('congratulations').select('message').eq('giveaway_id', giveaway_id).eq(
                'place', i).single().execute()
            congrats_message = congrats_response.data[
                'message'] if congrats_response.data else f"Поздравляем! Вы выиграли в розыгрыше '{giveaway['name']}'!"

            try:
                await bot.send_message(chat_id=winner, text=congrats_message)
            except Exception as e:
                logging.error(f"Failed to send congratulation message to winner {winner}: {str(e)}")

        # Публикуем результаты в связанных сообществах
        communities_response = supabase.table('giveaway_communities').select('community_id').eq('giveaway_id',
                                                                                                giveaway_id).execute()
        for community in communities_response.data:
            try:
                winners_text = "\n".join(
                    [f"{i}. @{await get_username(bot, winner_id)}" for i, winner_id in enumerate(winners, 1)])
                result_message = f"Розыгрыш '{giveaway['name']}' завершен!\n\nПобедители:\n{winners_text}"
                await bot.send_message(chat_id=community['community_id'], text=result_message)
            except Exception as e:
                logging.error(f"Failed to publish results in community {community['community_id']}: {str(e)}")

    except Exception as e:
        logging.error(f"Error in end_giveaway: {str(e)}")


async def update_giveaway_status(supabase: Client, giveaway_id: str, is_active: bool):
    try:
        supabase.table('giveaways').update({'is_active': is_active}).eq('id', giveaway_id).execute()
    except Exception as e:
        logging.error(f"Error updating giveaway status: {str(e)}")


async def get_username(bot: Bot, user_id: int) -> str:
    try:
        user = await bot.get_chat_member(user_id, user_id)
        return user.user.username or f"User{user_id}"
    except Exception as e:
        logging.error(f"Error getting username for user {user_id}: {str(e)}")
        return f"User{user_id}"


async def check_usernames(bot: Bot, supabase: Client):
    try:
        # Проверка пользователей (оставляем как есть)
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
                    logging.info(f"Updated username for user {user['user_id']}: {user.get('telegram_username')} -> {current_username}")
            except Exception as e:
                logging.error(f"Error checking user {user['user_id']}: {str(e)}")

        # Проверка сообществ
        communities_response = supabase.table('bound_communities').select('community_id, community_username').execute()
        communities = communities_response.data

        for community in communities:
            try:
                chat = await bot.get_chat(community['community_id'])
                current_username = chat.username

                if current_username != community.get('community_username'):
                    # Обновляем username в таблице bound_communities
                    supabase.table('bound_communities').update({
                        'community_username': current_username
                    }).eq('community_id', community['community_id']).execute()

                    # Обновляем username в таблице giveaway_communities
                    supabase.table('giveaway_communities').update({
                        'community_username': current_username
                    }).eq('community_id', community['community_id']).execute()

                    logging.info(f"Обновлено имя пользователя для сообщества {community['community_id']}: {community.get('community_username')} -> {current_username}")
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
