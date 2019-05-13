import pytz
import db_connector
import re
from datetime import datetime, timezone
from telegram import (ParseMode, ReplyKeyboardMarkup, ReplyKeyboardRemove,
                      ForceReply, TelegramError)
from telegram.ext import ConversationHandler
import html
from telegram_calendar_keyboard import calendar_keyboard


DEF_TZ = pytz.timezone('Europe/Moscow')
_ERR_MSG = 'Извините, произошла ошибка'

CHOOSING_COMMAND, CHOOSING_DL_DATE, CHOOSING_REMIND_DATE, \
    TYPING_REMIND_TIME, TYPING_DL_TIME = range(5)


def _rem_command(text):
    """Remove '/command' from text"""
    return re.sub('/[a-zA-Z_]+', '', text, 1)


def _get_task_id(text):
    """Return string representing first decimal number in text"""
    id_str = re.search('(?:[^_ ])[0-9]*', text)
    id_str = id_str.group(0) if id_str else ''
    return id_str


# todo: Adequate start message
def start(update, context):
    """Send a message when the command /start is issued."""
    update.message.reply_text('Greetings from DeltaSquad!',
                              disable_notification=True)


def help_msg(update, context):
    """Send a message when the command /help is issued."""
    update.message.reply_text('HELP IS ON ITS WAY!!!',
                              disable_notification=True)


def add_task(update, context):
    """Adds new task to the list"""
    handler = db_connector.DataBaseConnector()
    chat_id = update.message.chat.id
    creator_id = update.message.from_user.id
    msg_text = _rem_command(update.message.text)
    if not msg_text:
        update.message.reply_text('Вы не можете добавить пустое задание.')
        return
    try:
        task_id = handler.add_task(chat_id, creator_id, msg_text)
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        return
    # todo: use task_id to send launch task menu
    update.message.reply_text('Задание успешно добавлено.')


def close_task(update, context):
    """Mark the task as closed"""
    handler = db_connector.DataBaseConnector()
    user_id = update.message.from_user.id
    # remove leading command
    msg_text = _rem_command(update.message.text)
    user_data = context.user_data
    if 'chat id' in user_data:
        chat_id = user_data['chat id']
    else:
        chat_id = update.message.chat.id
    try:
        if 'task id' in user_data:
            task_id = user_data['task id']
        else:
            task_id = int(_get_task_id(msg_text))

        success = handler.close_task(task_id, chat_id, user_id)
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        return
    if not success:
        update.message.reply_text('Вы не можете закрыть это задание.',
                                  disable_notification=True,
                                  reply_markup=ReplyKeyboardRemove())
    else:
        update.message.reply_text('Задание успешно закрыто.',
                                  disable_notification=True,
                                  reply_markup=ReplyKeyboardRemove())
    user_data = context.user_data
    if 'task id' in user_data:
        del user_data['task id']
    user_data.clear()
    return ConversationHandler.END


def update_deadline(update, context):
    """Updates task deadline"""
    handler = db_connector.DataBaseConnector()
    user_id = update.message.from_user.id
    # remove leading command
    msg_text = _rem_command(update.message.text)
    user_data = context.user_data
    try:
        if 'task id' in user_data:
            task_id = user_data['task id']
        else:
            task_id = int(_get_task_id(msg_text))
            user_data['task id'] = task_id
        task_info = handler.task_info(task_id)

        success = (task_info['creator_id'] == user_id
                   or user_id in task_info['workers']
                   or not task_info['workers'])
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        return ConversationHandler.END

    if not success:
        update.message.reply_text('Вы не можете установить срок этому заданию.',
                                  disable_notification=True,
                                  reply_markup=ReplyKeyboardRemove())
    else:
        update.message.reply_text(f'Пожалуйста, выберите дату',
                                  reply_markup=calendar_keyboard.
                                  create_calendar())
        return CHOOSING_DL_DATE


def deadline_cal_handler(update, context):
    selected, full_date, update.message = \
        calendar_keyboard.process_calendar_selection(update, context)

    if selected:
        handler = db_connector.DataBaseConnector()
        user_data = context.user_data
        if 'chat id' in user_data:
            chat_id = user_data['chat id']
        else:
            chat_id = update.message.chat.id

        user_id = update.callback_query.from_user.id

        full_date = full_date.replace(hour=23, minute=59, second=59)
        full_date = DEF_TZ.localize(full_date)
        try:
            task_id = user_data['task id']
            success = handler.set_deadline(task_id, chat_id, user_id, full_date)
        except (ValueError, ConnectionError, KeyError):
            update.message.reply_text('Извините, не получилось.',
                                      reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        if not success:
            update.message.reply_text('Вы не можете установить ' +
                                      'срок этому заданию.',
                                      disable_notification=True,
                                      reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END
        else:
            user_data['deadline'] = full_date
            update.message.bot.delete_message(update.message.chat.id,
                                              update.message.message_id)
            user_name = update.callback_query.from_user.username
            update.message.bot.sendMessage(
                update.message.chat.id,
                (f'@{user_name}, Вы выбрали дату '
                 f'{full_date.strftime("%d/%m/%Y")}\n'
                 'Для уточнения времени отправьте его в формате \"hh:mm\"'
                 'в ответном сообщении.'),
                reply_markup=ForceReply(selective=True))
            return TYPING_DL_TIME


def get_dl_time(update, context):
    user_data = context.user_data
    try:
        handler = db_connector.DataBaseConnector()
        chat_id = user_data['chat id']
        user_id = update.message.from_user.id

        time = re.sub(' *', '', update.message.text, 1)

        task_id = user_data['task id']
        due_date = user_data['deadline']

        hours = int(time[:time.find(':')].strip())
        minutes = int(time[time.find(':') + 1:].strip())
        due_date = due_date.replace(hour=hours, minute=minutes, second=0,
                                    tzinfo=None)
        due_date = DEF_TZ.localize(due_date)

        success = handler.set_deadline(task_id, chat_id, user_id,
                                       due_date)

        if not success:
            update.message.reply_text('Вы не можете установить время ' +
                                      'этому заданию.')
        else:
            update.message.reply_text('Время выполнения установлено.')

    except (ValueError, ConnectionError, AttributeError):
        update.message.reply_text(_ERR_MSG)
    return ConversationHandler.END


def get_list(update, context, for_user=False, free_only=False):
    """Sends the task list"""
    chat = update.message.chat
    user_id = update.message.from_user.id

    if for_user and user_id != chat.id:
        msg = 'Список Ваших задач доступен в личном диалоге'
        update.message.reply_text(msg)
        return

    try:
        handler = db_connector.DataBaseConnector()
        if for_user:
            rows = handler.get_user_tasks(user_id)
        else:
            rows = handler.get_tasks(chat.id, free_only=free_only)
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        return

    if not rows:
        reps_text = 'Ваш список задач пуст!'
        update.message.bot.send_message(chat_id=chat.id, text=reps_text,
                                        disable_notification=True)
        return

    reps_text = ''
    for row in (sorted(rows, key=_row_sort_key)):
        task_mark = u'<b>[ ! ]</b> ' if row['marked'] else u'\u25b8 '
        reps_text += f'{task_mark} {html.escape(row["task_text"])}\n\n'

        # Parse workers list
        if row['workers']:
            workers = ''
            for w_id in row['workers']:
                w_info = chat.get_member(w_id)
                f_name = w_info['user']['first_name']
                l_name = w_info['user']['last_name']
                username = w_info['user']['username']
                tg_link = f'https://t.me/{username}'
                workers += f'<a href="{tg_link}">{l_name} {f_name}</a>\n'

            reps_text += f'<b>Исполнитель:</b> {workers}'

        # Localize UTC time
        if row['deadline']:
            dl_format = ' %a %d.%m'
            if row['deadline'].second == 0:  # if time is not default
                dl_format += ' %H:%M'
            dl = row['deadline'].astimezone(DEF_TZ).strftime(dl_format)
            reps_text += f'<b>Срок:</b> <code>{dl}</code>\n'

        reps_text += f'<b>Действия:</b>  /act_{row["id"]}\n'
        reps_text += u'-' * 16 + '\n\n'

    update.message.bot.send_message(chat_id=chat.id, text=reps_text,
                                    parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True,
                                    disable_notification=True)


def _row_sort_key(row):
    """Sort tasks by marked flag and deadline"""
    # Sort works in ascending order
    # To show marked tasks first m_key must be False
    m_key = False if row['marked'] else True
    dl_key = row['deadline']
    if not dl_key:  # If deadline is None
        dl_key = datetime.max
        tz = pytz.timezone('UTC')
        dl_key = tz.localize(dl_key)
    return m_key, dl_key


def take_task(update, context):
    """Assign task to the current user"""
    handler = db_connector.DataBaseConnector()
    user_id = update.message.from_user.id
    msg_text = _rem_command(update.message.text)
    user_data = context.user_data
    if 'chat id' in user_data:
        chat_id = user_data['chat id']
    else:
        chat_id = update.message.chat.id
    try:
        if 'task id' in user_data:
            task_id = user_data['task id']
        else:
            task_id = int(_get_task_id(msg_text))
        success = handler.assign_task(task_id, chat_id, user_id, [user_id])
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        user_data.clear()
        return ConversationHandler.END

    if not success:
        update.message.reply_text('Вы не можете взять это задание.',
                                  reply_markup=ReplyKeyboardRemove())
    else:
        update.message.reply_text('Задание захвачено.',
                                  reply_markup=ReplyKeyboardRemove())
    user_data.clear()
    return ConversationHandler.END


def ret_task(update, context):
    """Return task to the vacant pool"""
    handler = db_connector.DataBaseConnector()
    user_data = context.user_data
    if 'chat id' in user_data:
        chat_id = user_data['chat id']
    else:
        chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    msg_text = _rem_command(update.message.text)
    try:
        if 'task id' in user_data:
            task_id = user_data['task id']
        else:
            task_id = int(_get_task_id(msg_text))
        success = handler.rem_worker(task_id, chat_id, user_id)
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        user_data.clear()
        return ConversationHandler.END

    if not success:
        update.message.reply_text('Вы не можете вернуть это задание.',
                                  reply_markup=ReplyKeyboardRemove())
    else:
        update.message.reply_text('Вы отказались от задания.',
                                  reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


def rem_deadline(update, context):
    """Removes task deadline"""
    handler = db_connector.DataBaseConnector()
    user_data = context.user_data
    if 'chat id' in user_data:
        chat_id = user_data['chat id']
    else:
        chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    # remove leading command
    msg_text = _rem_command(update.message.text)
    try:
        if 'task id' in user_data:
            task_id = user_data['task id']
        else:
            task_id = int(_get_task_id(msg_text))
        success = handler.set_deadline(task_id, chat_id, user_id)
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        user_data.clear()
        return ConversationHandler.END
    if not success:
        update.message.reply_text('Вы не можете отменить срок выполнения '
                                  'этого задания.',
                                  reply_markup=ReplyKeyboardRemove())
    else:
        update.message.reply_text('Срок выполнения отменен.',
                                  reply_markup=ReplyKeyboardRemove())
    user_data.clear()
    return ConversationHandler.END


def done(update, context):
    """Finish act conversation"""
    msg = update.message.reply_text('Принято', disable_notification=True,
                                    reply_markup=ReplyKeyboardRemove())
    update.message.bot.delete_message(update.message.chat.id, msg.message_id)
    context.user_data.clear()
    return ConversationHandler.END


def set_marked_status(update, context):
    handler = db_connector.DataBaseConnector()
    user_data = context.user_data
    if 'chat id' in user_data:
        chat_id = user_data['chat id']
    else:
        chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    # remove leading command
    msg_text = _rem_command(update.message.text)
    try:
        if 'task id' in user_data:
            task_id = user_data['task id']
        else:
            task_id = int(_get_task_id(msg_text))
        marked = not handler.task_info(task_id)['marked']
        success = handler.set_marked_status(task_id, chat_id, user_id, marked)
    except (ValueError, ConnectionError):
        update.message.reply_text(_ERR_MSG)
        user_data.clear()
        return ConversationHandler.END
    if not success:
        if marked:
            update.message.reply_text('Вы не можете отметить это задание.',
                                      disable_notification=True,
                                      reply_markup=ReplyKeyboardRemove())
        else:
            update.message.reply_text('Вы не можете снять отметку '
                                      'этого задания.',
                                      disable_notification=True,
                                      reply_markup=ReplyKeyboardRemove())
    else:
        if marked:
            update.message.reply_text('Отметка успешно добавлена.',
                                      disable_notification=True,
                                      reply_markup=ReplyKeyboardRemove())
        else:
            update.message.reply_text('Отметка успешно удалена.',
                                      disable_notification=True,
                                      reply_markup=ReplyKeyboardRemove())
    user_data.clear()
    return ConversationHandler.END


def act_task(update, context):
    handler = db_connector.DataBaseConnector()
    msg_text = _rem_command(update.message.text)
    chat_id = update.message.chat.id
    user_id = update.message.from_user.id
    try:
        task_id = int(_get_task_id(msg_text))
        context.user_data['task id'] = task_id
        task_info = handler.task_info(task_id)
        context.user_data['chat id'] = task_info['chat_id']
        task_chat = update.message.bot.get_chat(task_info['chat_id'])

        if task_chat.id != chat_id and user_id not in task_info['workers']:
            update.message.reply_text('Вы не можете управлять этим заданием.',
                                      disable_notification=True,
                                      reply_markup=ReplyKeyboardRemove())
            return ConversationHandler.END

        if task_chat.type == 'private':
            is_admin = False
        else:
            is_admin = user_id in [admin.user.id for admin in
                                   update.message.bot.get_chat_administrators(
                                       task_info['chat_id'])]
        buttons = [[]]
        cols = 0
        if (user_id in task_info['workers'] or is_admin
                or task_info['chat_id'] == chat_id and not task_info['workers']
                or task_info['creator_id'] == user_id):
            buttons[-1] += ['Закрыть']
            cols += 1

        if user_id in task_info['workers']:
            cols += 1
            buttons[-1] += ['Отказаться']

        elif task_info['chat_id'] == chat_id and not task_info['workers']:
            if not cols % 2:
                buttons.append([])
            cols += 1
            buttons[-1] += ["Взять"]

        if task_info['creator_id'] == user_id or is_admin:
            if task_info['deadline']:
                buttons.append([])
                buttons[-1] += ['Изменить срок']
                buttons[-1] += ['Удалить срок']
                cols = 0
            else:
                if not cols % 2:
                    buttons.append([])
                buttons[-1] += ['Установить срок']
                cols += 1

            if not cols % 2:
                buttons.append([])
            if task_info['marked']:
                buttons[-1] += ['Снять отметку']
            else:
                buttons[-1] += ['Отметить']
            cols += 1

        if not cols % 2:
            buttons.append([])
        buttons[-1] += ['Создать напоминание']

        buttons.append([])
        buttons[-1] += ['Покинуть меню']

        markup = ReplyKeyboardMarkup(buttons,
                                     selective=True,
                                     one_time_keyboard=True,
                                     resize_keyboard=False)
        update.message.reply_text("Выберите действие с задачей",
                                  reply_markup=markup)
    except (ValueError, ConnectionError, TelegramError):
        update.message.reply_text(_ERR_MSG)
        return ConversationHandler.END
    return CHOOSING_COMMAND


def add_reminder(update, context):
    update.message.bot.send_message(
        update.message.chat.id, 'Пожалуйста, выберите дату',
        disable_notification=True,
        reply_markup=calendar_keyboard.create_calendar())
    return CHOOSING_REMIND_DATE


def reminder_cal_handler(update, context):
    selected, full_date, update.message = \
        calendar_keyboard.process_calendar_selection(update, context)
    if selected:
        today = datetime.now(timezone.utc).astimezone(DEF_TZ)
        if today.date() > full_date.date():
            msg = 'Дата неверна'
            update.message.bot.sendMessage(
                update.message.chat.id, msg,
                reply_markup=ReplyKeyboardRemove(selective=True))
            return ConversationHandler.END

        context.user_data['datetime'] = full_date
        update.message.bot.delete_message(update.message.chat.id,
                                          update.message.message_id)
        msg = (f'Вы выбрали дату {full_date.strftime("%d/%m/%Y")}\n'
               'Введите время в формате \"hh:mm\"\n')
        update.message.bot.sendMessage(
            update.message.chat.id, msg,
            reply_markup=ReplyKeyboardRemove(selective=True))
        return TYPING_REMIND_TIME


def get_rem_time(update, context):
    user_data = context.user_data
    user_id = update.message.from_user.id
    handler = db_connector.DataBaseConnector()
    try:
        time = re.search(r'\d{1,2}:\d{2}', update.message.text).group()
        task_id = user_data['task id']
        date_time = user_data['datetime']

        hours = int(time[:time.find(':')].strip())
        minutes = int(time[time.find(':') + 1:].strip())

        date_time = date_time.replace(hour=hours, minute=minutes, second=0,
                                      tzinfo=None)
        date_time = DEF_TZ.localize(date_time)
        now = datetime.now(timezone.utc)
        if now > date_time:
            msg = 'Введенное время уже прошло'
            update.message.bot.sendMessage(
                update.message.chat.id, msg,
                reply_markup=ReplyKeyboardRemove(selective=True))
            return ConversationHandler.END

        success = handler.create_reminder(task_id, user_id, date_time)

    except (ValueError, AttributeError, ConnectionError):
        update.message.reply_text(_ERR_MSG, disable_notification=True)
        return ConversationHandler.END

    if success:
        msg = 'Напоминание успешно установлено'
        update.message.reply_text(msg, disable_notification=True)
    else:
        update.message.reply_text(_ERR_MSG, disable_notification=True)

    return ConversationHandler.END
