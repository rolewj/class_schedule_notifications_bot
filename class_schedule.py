from aiogram import Bot, Dispatcher, executor, types
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
import logging
from datetime import datetime, timedelta
import aiosqlite
import aioschedule
import asyncio
import boto3
import random
import pytz
from response_dictionary import negative_replies, positive_replies, mixed_replies

# Инициализация стороннего API
comprehend_client = boto3.client("comprehend", region_name="eu-central-1")
translate_client = boto3.client("translate", region_name="eu-central-1")

# токен бота
API_TOKEN = 'TOKEN'

storage = MemoryStorage() # инициализация хранилища состояний для FSM
bot = Bot(token=API_TOKEN) # инициализация бота с указанным токеном
dp = Dispatcher(bot, storage=storage) # инициализация диспетчера для бота с использованием хранилища состояний
dp.middleware.setup(LoggingMiddleware()) # настройка логирования для бота
MAX_MESSAGE_LENGTH = 4096  # максимальная длина сообщения для Telegram
ADMIN_ID = 820288017

# определение класса состояний для машины состояний FSM
class Schedule(StatesGroup):
    choosing_action = State()
    choosing_day_for_deletion = State()
    choosing_delete_option = State()
    week_day_to_add = State()
    week_day_to_delete = State()
    date_to_delete = State()
    choosing_day_for_editing = State()
    choosing_lesson_to_edit = State()
    editing_lesson = State()
    editing_lesson_details = State()
    editing_specific_lesson = State()
    week_day_to_show = State()
    waiting_for_lesson_time = State()
    waiting_for_lesson_name = State()
    waiting_for_teacher_name = State()
    waiting_for_classroom = State()
    waiting_for_id = State()
    deleting_specific_lesson = State()
    deleting_day_schedule = State()
    confirming_day_deletion = State()
    cancelling = State()

# функция, вызываемая при запуске бота
async def on_startup(dp):
    async with aiosqlite.connect('schedule.db') as db:
        # создание таблицы расписания, если она не существует
        await db.execute('''CREATE TABLE IF NOT EXISTS schedule (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            user_id INTEGER NOT NULL,
                            week_day TEXT NOT NULL,
                            lesson_time TEXT NOT NULL,
                            lesson_name TEXT NOT NULL,
                            teacher_name TEXT NOT NULL,
                            classroom TEXT NOT NULL,
                            FOREIGN KEY(user_id) REFERENCES subscriptions(user_id))''')
        
        # Создание таблицы подписок, если она не существует
        await db.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
                            user_id INTEGER PRIMARY KEY,
                            active BOOLEAN NOT NULL CHECK (active IN (0, 1)),
                            notification_time TEXT,
                            timezone TEXT
                        )''')
        await db.commit()

    # Запуск планировщика задач
    asyncio.create_task(scheduler())

# клавиатура для главного меню
main_menu_kb = ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
main_menu_kb.add(KeyboardButton('/add'))
main_menu_kb.add(KeyboardButton('/delete'))
main_menu_kb.add(KeyboardButton('/edit'))
main_menu_kb.add(KeyboardButton('/view'))
main_menu_kb.add(KeyboardButton('/notification'))

# клавиатура для возврата в главное меню
back_to_main_menu_kb = ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton('Назад'))

# обработчик команды /start
@dp.message_handler(commands=['start'], state='*')
async def start_command(message: types.Message):
    await message.answer("Привет! Я бот для управления расписанием занятий.\n"
                         "/add — добавить занятие;\n"
                         "/delete — удалить занятие или расписание на день;\n"
                         "/edit — редактировать занятие;\n"
                         "/view — просмотреть расписание;\n"
                         "/notification — подписка на рассылку сообщений с расписанием.\n"
                         "Выберите действие:",
                         reply_markup=main_menu_kb
    )
    
# обработчик кнопки "Назад"
@dp.message_handler(lambda message: message.text == "Назад", state="*")
async def back_to_main_menu(message: types.Message, state: FSMContext):
    await state.finish() # сбрасываем состояние
    await message.answer(
                         "/add — добавить занятие;\n"
                         "/delete — удалить занятие или расписание на день;\n"
                         "/edit — редактировать занятие;\n"
                         "/view — просмотреть расписание;\n"
                         "/notification — подписка на рассылку сообщений с расписанием.\n"
                         "Выберите действие:",
                         reply_markup=main_menu_kb
    )
    
# функция для создания клавиатуры с днями недели и кнопкой "Назад"
def get_week_days_kb():
    week_days_kb = ReplyKeyboardMarkup(resize_keyboard=True)
    week_days_kb.add(KeyboardButton('Назад'))
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    for i in range(6):
        day = monday + timedelta(days=i)
        button_text = day.strftime("%A")
        week_days_kb.add(KeyboardButton(button_text))
    return week_days_kb

# обработчик команды /add для начала процесса добавления расписания
@dp.message_handler(commands=['add'], state='*')
async def add_command(message: types.Message):
    await message.answer("На какой день недели добавляем занятие?", reply_markup=get_week_days_kb())
    await Schedule.week_day_to_add.set()

# обработчик для выбора дня недели при добавлении расписания
@dp.message_handler(state=Schedule.week_day_to_add)
async def week_day_chosen(message: types.Message, state: FSMContext):
    valid_days = get_valid_week_days()
    if message.text.lower() == "назад":
        # логика для кнопки "Назад"
        await state.finish()
        await message.answer(
            "/add — добавить занятие;\n"
            "/delete — удалить занятие или расписание на день;\n"
            "/edit — редактировать занятие;\n"
            "/view — просмотреть расписание;\n"
            "/notification — подписка на рассылку сообщений с расписанием.\n"
            "Выберите действие:",
            reply_markup=main_menu_kb
        )
    elif not is_valid_week_day(message.text, valid_days):
        await handle_invalid_week_day_input(message)
    else:
        # если день недели корректный, сохраняем его и переходим к следующему шагу
        async with state.proxy() as data:
            data['week_day'] = message.text
        await Schedule.waiting_for_lesson_time.set() # Устанавливаем следующее состояние для ввода времени занятия

        # создаем клавиатуру с кнопкой "Назад" для следующего шага
        back_kb = ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton('Назад'))
        await message.answer(
            "Введите занятие следующим образом через запятую: время, название предмета, ФИО преподавателя, аудитория.\n"
            "Пример: 9:50, Защита информации, Меркулов И.А., 420 (К.5)",
            reply_markup=back_kb
        )

# функция для проверки дня недели
def is_valid_week_day(week_day, valid_days):
    return week_day in valid_days

# функция для извлечения допустимых дней недели, нужно для проверки введенного дня недели
def get_valid_week_days():
    today = datetime.now()
    monday = today - timedelta(days=today.weekday())
    valid_days = []
    for i in range(6):
        day = monday + timedelta(days=i)
        valid_days.append(day.strftime("%A"))
    return valid_days


# обработчик для ввода информации о занятии
@dp.message_handler(state=Schedule.waiting_for_lesson_time)
async def lesson_info_chosen(message: types.Message, state: FSMContext):
    if message.text.lower() == 'назад':
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)
    else:
        lesson_info = message.text.split(', ')
        if len(lesson_info) != 4:
            await message.answer(
                "Некорректный ввод. Пожалуйста, следуйте формату и введите данные ещё раз.",
                reply_markup=back_to_main_menu_kb
            )
        else:
            async with state.proxy() as data:
                data['lesson_time'], data['lesson_name'], data['teacher_name'], data['classroom'] = lesson_info
            user_id = message.from_user.id
            await add_lesson_to_db(state, user_id)
            await state.finish()
            await message.answer(
                "Занятие успешно добавлено! Хотите добавить еще занятие?",
                reply_markup=get_week_days_kb()
            )
            await Schedule.week_day_to_add.set()


# функция для добавления занятия в базу данных
async def add_lesson_to_db(state: FSMContext, user_id: int):
    async with state.proxy() as data:
        async with aiosqlite.connect('schedule.db') as db:
            await db.execute(
                'INSERT INTO schedule (user_id, week_day, lesson_time, lesson_name, teacher_name, classroom) VALUES (?, ?, ?, ?, ?, ?)',
                (user_id, data['week_day'], data['lesson_time'], data['lesson_name'], data['teacher_name'], data['classroom'])
            )
            await db.commit()

# обработчик для команды /edit
@dp.message_handler(commands=['edit'], state='*')
async def edit_schedule_command(message: types.Message):
    # предоставляем пользователю выбрать день для редактирования
    week_days_kb = get_week_days_kb()
    await message.answer("Выберите день недели для редактирования занятия:", reply_markup=week_days_kb)
    await Schedule.choosing_day_for_editing.set()

# обработчик для выбора дня недели при редактировании расписания
@dp.message_handler(state=Schedule.choosing_day_for_editing)
async def choose_day_for_editing(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    selected_day = message.text
    if message.text.lower() == 'назад':
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)
    else:
        lessons = await get_lessons_for_user_by_day(user_id, selected_day)
        lessons_kb = ReplyKeyboardMarkup(resize_keyboard=True)
        lessons_kb.add(KeyboardButton('Назад'))
        for lesson in lessons:
            button_text = f"{lesson[2]} - {lesson[3]} ({lesson[1]})"
            lessons_kb.add(KeyboardButton(button_text))
        await message.answer("Выберите занятие для редактирования:", reply_markup=lessons_kb)
        await Schedule.editing_specific_lesson.set()

# обработчик для ввода новых деталей занятия
@dp.message_handler(state=Schedule.editing_lesson_details)
async def update_lesson_details(message: types.Message, state: FSMContext):
    new_lesson_details = message.text.split(', ')
    if len(new_lesson_details) != 4:  # Убедитесь, что введено 4 элемента
        await message.answer(
            "Некорректный ввод. Пожалуйста, следуйте формату и введите данные еще раз. Формат: 'Время, Название, Преподаватель, Аудитория'.",
            reply_markup=back_to_main_menu_kb
        )
    else:
        async with state.proxy() as data:
            lesson_id = data['lesson_id']
            week_day = data['week_day']  # Получение week_day из состояния

            try:
                await update_lesson_in_db(lesson_id, week_day, *new_lesson_details)
                await state.finish()
                await message.answer("Занятие успешно обновлено!", reply_markup=main_menu_kb)
            except Exception as e:
                await message.answer(f"Произошла ошибка при обновлении занятия: {e}")

# функция для обновления занятия в базе данных
async def update_lesson_in_db(lesson_id, week_day, lesson_time, lesson_name, teacher_name, classroom):
    async with aiosqlite.connect('schedule.db') as db:
        await db.execute(
            'UPDATE schedule SET week_day = ?, lesson_time = ?, lesson_name = ?, teacher_name = ?, classroom = ? WHERE id = ?',
            (week_day, lesson_time, lesson_name, teacher_name, classroom, lesson_id)
        )
        await db.commit()

# обработчик для выбора конкретного занятия для редактирования
@dp.message_handler(state=Schedule.editing_specific_lesson)
async def edit_chosen_lesson(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == 'Назад':
        await edit_schedule_command(message)
    else:
        try:
            lesson_details = message.text.split(' - ')
            lesson_time = lesson_details[0].strip()
            lesson_name, week_day = lesson_details[1].split('(')
            lesson_name = lesson_name.strip()
            week_day = week_day.split(')')[0].strip()

            lesson_id = await get_lesson_id_by_details(user_id, lesson_time, lesson_name, week_day)
            if lesson_id is None:
                raise ValueError
            else:
                # Вывод текущих деталей занятия
                current_details = await get_lesson_details_by_id(lesson_id)
                if current_details:
                    current_details_text = f"Текущие детали занятия: {current_details[0]}, {current_details[1]}, {current_details[2]}, {current_details[3]}"
                    await message.answer(current_details_text)

                # Сохранение lesson_id и week_day в состоянии
                async with state.proxy() as data:
                    data['lesson_id'] = lesson_id
                    data['week_day'] = week_day

                await message.answer("Введите новые детали занятия в формате 'Время, Название, Преподаватель, Аудитория'.")
                await Schedule.editing_lesson_details.set()
        except (ValueError, IndexError):
            await message.answer("Некорректный выбор. Пожалуйста, попробуйте еще раз.")


# обработчик для команды /show
@dp.message_handler(commands=['show'], state='*')
async def show_schedule(message: types.Message):
    week_days_kb = get_week_days_kb()
    await message.answer("На какой день показать расписание?", reply_markup=week_days_kb)
    await Schedule.week_day_to_show.set()

# функция для получения расписания на конкретный день
async def get_schedule_for_day(week_day_date: str, user_id: int):
    async with aiosqlite.connect('schedule.db') as db:
        cursor = await db.execute('SELECT * FROM schedule WHERE week_day = ? AND user_id = ?', (week_day_date, user_id))
        return await cursor.fetchall()

# обработчик для отображения расписания на выбранный день
@dp.message_handler(state=Schedule.week_day_to_show)
async def show_day_schedule(message: types.Message, state: FSMContext):
    valid_days = get_valid_week_days()
    user_id = message.from_user.id
    if message.text.lower() == 'назад':
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)
    elif not is_valid_week_day(message.text, valid_days):
        await handle_invalid_week_day_input(message)
    else:
        week_day_date = message.text
        schedule = await get_schedule_for_day(week_day_date, user_id)
        if not schedule:
            await message.answer(f"Расписание на {week_day_date} пусто.")
        else:
            schedule_message = f"Расписание на {week_day_date}:\n" + "\n".join(
                f"{entry[3]} - {entry[4]}, {entry[5]}, ауд. {entry[6]}"
                for entry in schedule
            )
            await message.answer(schedule_message)
        await message.answer("Хотите посмотреть расписание на другой день или вернуться в главное меню?", reply_markup=get_week_days_kb())
        await Schedule.week_day_to_show.set()

# обработчик команды /delete для начала процесса удаления расписания
@dp.message_handler(commands=['delete'], state='*')
async def delete_schedule_command(message: types.Message):
    week_days_kb = get_week_days_kb()
    await message.answer("Выберите день недели:", reply_markup=week_days_kb)
    await Schedule.choosing_day_for_deletion.set()

# обработчик для подтверждения удаления расписания на день недели
@dp.message_handler(lambda message: message.text == "Удалить расписание на день недели", state=Schedule.choosing_delete_option)
async def confirm_day_schedule_deletion(message: types.Message, state: FSMContext):
    confirm_kb = ReplyKeyboardMarkup(resize_keyboard=True)
    confirm_kb.add(KeyboardButton('Да'))
    confirm_kb.add(KeyboardButton('Нет'))
    await message.answer("Вы уверены, что хотите удалить расписание на выбранный день?", reply_markup=confirm_kb)
    await Schedule.confirming_day_deletion.set()

# обработчик для удаления
@dp.message_handler(state=Schedule.choosing_day_for_deletion)
async def choose_day_for_deletion(message: types.Message, state: FSMContext):
    valid_days = get_valid_week_days()
    if message.text.lower() == 'назад':
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)
    elif not is_valid_week_day(message.text, valid_days):
        await handle_invalid_week_day_input(message)
    else:
        async with state.proxy() as data:
            data['selected_day'] = message.text

        delete_options_kb = ReplyKeyboardMarkup(resize_keyboard=True)
        delete_options_kb.add(KeyboardButton('Назад'))
        delete_options_kb.add(KeyboardButton('Удалить занятие'))
        delete_options_kb.add(KeyboardButton('Удалить расписание на день недели'))

        await message.answer("Выберите опцию удаления:", reply_markup=delete_options_kb)
        await Schedule.choosing_delete_option.set()

# обработчик для команды удаления конкретного занятия
@dp.message_handler(lambda message: message.text == "Удалить занятие", state=Schedule.choosing_delete_option)
async def delete_specific_lesson_command(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    async with state.proxy() as data:
        selected_day = data['selected_day']
    # получаем список занятий
    lessons = await get_lessons_for_user_by_day(user_id, selected_day)
    lessons_kb = ReplyKeyboardMarkup(resize_keyboard=True)
    lessons_kb.add(KeyboardButton('Назад'))
    for lesson in lessons:
        button_text = f"{lesson[2]} - {lesson[3]} ({lesson[1]})"
        lessons_kb.add(KeyboardButton(button_text))
    await message.answer("Выберите занятие для удаления:", reply_markup=lessons_kb)
    await Schedule.deleting_specific_lesson.set()

# обработчик подтверждения удаления расписания на выбранный день
@dp.message_handler(state=Schedule.confirming_day_deletion)
async def delete_day_schedule(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == 'Да':
        async with state.proxy() as data:
            selected_day = data['selected_day']
        await delete_schedule_for_day(selected_day, user_id)
        await message.answer(f"Расписание на {selected_day} было удалено.")
    else:
        await message.answer("Удаление отменено.")
    await state.finish()
    await message.answer("Выберите действие:", reply_markup=main_menu_kb)

# функция для получения информации занятия по ID
async def get_lesson_details_by_id(lesson_id: int):
    async with aiosqlite.connect('schedule.db') as db:
        cursor = await db.execute(
            'SELECT lesson_time, lesson_name, teacher_name, classroom FROM schedule WHERE id = ?',
            (lesson_id,)
        )
        return await cursor.fetchone()

# функция для получения ID занятия по информации
async def get_lesson_id_by_details(user_id: int, lesson_time: str, lesson_name: str, week_day: str):
    async with aiosqlite.connect('schedule.db') as db:
        cursor = await db.execute(
            'SELECT id FROM schedule WHERE user_id = ? AND week_day = ? AND lesson_time = ? AND lesson_name = ?',
            (user_id, week_day, lesson_time, lesson_name)
        )
        result = await cursor.fetchone()
        return result[0] if result else None

# обработчик для удаления выбранного занятия
@dp.message_handler(state=Schedule.deleting_specific_lesson)
async def delete_chosen_lesson(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text == 'Назад':
        await delete_schedule_command(message)
    else:
        # разбор сообщения пользователя для получения деталей занятия
        try:
            lesson_details = message.text.split(' - ')
            lesson_time = lesson_details[0].strip()
            lesson_name, week_day = lesson_details[1].split('(')
            lesson_name = lesson_name.strip()
            week_day = week_day.split(')')[0].strip()

            # получение ID занятия
            lesson_id = await get_lesson_id_by_details(user_id, lesson_time, lesson_name, week_day)
            if lesson_id is None:
                raise ValueError
        except (ValueError, IndexError):
            await message.answer("Некорректный выбор. Пожалуйста, попробуйте еще раз.")
            return

        # выполнение запроса на удаление занятия по ID
        async with aiosqlite.connect('schedule.db') as db:
            await db.execute(
                'DELETE FROM schedule WHERE id = ? AND user_id = ?',
                (lesson_id, user_id)
            )
            await db.commit()

        await message.answer(f"Занятие удалено.")
        
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)

# функция для получения списка занятий пользователя по дню
async def get_lessons_for_user_by_day(user_id: int, selected_day: str):
    async with aiosqlite.connect('schedule.db') as db:
        # Обратите внимание на форматирование даты в вашей базе данных, чтобы соответствовать формату selected_day
        cursor = await db.execute(
            'SELECT id, week_day, lesson_time, lesson_name, teacher_name, classroom FROM schedule WHERE user_id = ? AND week_day = ?',
            (user_id, selected_day)
        )
        return await cursor.fetchall()

# функция для удаления расписания на выбранный день
async def delete_schedule_for_day(selected_day: str, user_id: int):
    async with aiosqlite.connect('schedule.db') as db:
        await db.execute('DELETE FROM schedule WHERE week_day = ? AND user_id = ?', (selected_day, user_id))
        await db.commit()

# обработчик для выбора дня недели при удалении расписания
@dp.message_handler(state=Schedule.date_to_delete)
async def delete_schedule_for_day_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if message.text.lower() == 'назад':
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)
    else:
        selected_date_str = message.text.strip()
        try:
            await delete_schedule_for_day(selected_date_str, user_id)
            await message.answer(f"Расписание на {selected_date_str} было удалено.")
        except Exception as e:
            await message.answer(f"Произошла ошибка при удалении расписания: {e}")
        await message.answer("Хотите удалить расписание на другой день или вернуться в главное меню?", reply_markup=get_week_days_kb())
        await Schedule.date_to_delete.set()

# обработчик команды /showdb для отображения всей информации из базы данных
@dp.message_handler(commands=['showdb'], state='*')
async def show_db(message: types.Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        response = "Содержимое базы данных:\n\n"
        async with aiosqlite.connect('schedule.db') as db:
            cursor = await db.execute('SELECT id, user_id, week_day, lesson_time, lesson_name, teacher_name, classroom FROM schedule')
            rows = await cursor.fetchall()

        if not rows:
            await message.answer("База данных расписания пуста.")
        else:
            for row in rows:
                row_text = f"ID: {row[0]}, USER_ID: {row[1]}, День: {row[2]}, Время: {row[3]}, Занятие: {row[4]}, Преподаватель: {row[5]}, Аудитория: {row[6]}\n"
                if len(response) + len(row_text) < MAX_MESSAGE_LENGTH:
                    response += row_text
                else:
                    await message.answer(response)
                    response = row_text

            # отправить оставшиеся данные после цикла
            if response:
                await message.answer(response)
    else:
        await message.answer("У вас нет прав для использования этой команды.")


# обработчик команды /showsubs для отображения всех подписок
@dp.message_handler(commands=['showsubs'], state='*')
async def show_subscriptions(message: types.Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        async with aiosqlite.connect('schedule.db') as db:
            cursor = await db.execute('SELECT * FROM subscriptions')
            rows = await cursor.fetchall()
            if not rows:
                await message.answer("В базе данных нет подписок.")
                return
            # формирование ответного сообщения с данными о подписках
            response = "Список всех подписок:\n\n"
            for row in rows:
                response += f"USER_ID: {row[0]}, Активность: {row[1]}, Время: {row[2]}, Часовой пояс: {row[3]}\n"
            await message.answer(response)
    else:
        await message.answer("У вас нет прав для использования этой команды.")  

class Confirm(StatesGroup):
    confirmation = State()

# обработчик команды /resetdb для сброса базы данных
@dp.message_handler(commands=['resetdb'], state='*')
async def reset_db_command(message: types.Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.answer("Вы уверены, что хотите удалить и заново создать базу данных? Это действие необратимо. Введите 'да' для подтверждения.")
        await Confirm.confirmation.set()
    else:
        await message.answer("У вас нет прав для использования этой команды.")

# обработчик подтверждения сброса базы данных
@dp.message_handler(state=Confirm.confirmation)
async def confirm_reset_db(message: types.Message, state: FSMContext):
    if message.text.lower() == 'да':
        async with aiosqlite.connect('schedule.db') as db:
            await db.execute('DROP TABLE IF EXISTS schedule')
            await db.execute('''CREATE TABLE IF NOT EXISTS schedule (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                user_id INTEGER NOT NULL,
                                week_day TEXT NOT NULL,
                                lesson_time TEXT NOT NULL,
                                lesson_name TEXT NOT NULL,
                                teacher_name TEXT NOT NULL,
                                classroom TEXT NOT NULL,
                                FOREIGN KEY(user_id) REFERENCES subscriptions(user_id))''')
            await db.commit()
        await message.answer("База данных была успешно сброшена и заново создана.")
    else:
        await message.answer("Сброс базы данных отменен.")
    await state.finish()

# обработчик команды /resetsubs для сброса таблицы подписок
@dp.message_handler(commands=['resetsubs'], state='*')
async def reset_subs_command(message: types.Message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.answer("Вы уверены, что хотите удалить и заново создать таблицу подписок? Это действие необратимо. Введите 'да' для подтверждения.")
        await Confirm.confirmation.set()
    else:
        await message.answer("У вас нет прав для использования этой команды.")

# обработчик подтверждения сброса таблицы подписок
@dp.message_handler(state=Confirm.confirmation)
async def confirm_reset_subs(message: types.Message, state: FSMContext):
    if message.text.lower() == 'да':
        async with aiosqlite.connect('schedule.db') as db:
            await db.execute('DROP TABLE IF EXISTS subscriptions')
            await db.execute('''CREATE TABLE IF NOT EXISTS subscriptions (
                                user_id INTEGER PRIMARY KEY,
                                active BOOLEAN NOT NULL CHECK (active IN (0, 1)),
                                notification_time TEXT,
                                timezone TEXT
                            )''')
            await db.commit()
        await message.answer("Таблица подписок была успешно сброшена и заново создана.")
    else:
        await message.answer("Сброс таблицы подписок отменен.")
    await state.finish()

# обработчик команды /view для просмотра БД
@dp.message_handler(commands=['view'])
async def view_schedule(message: types.Message):
    user_id = message.from_user.id
    
    schedule_query = '''
    SELECT week_day, 
           printf("%02d:%s", CAST(substr(lesson_time, 1, instr(lesson_time, ':') - 1) AS INTEGER),
           substr(lesson_time, instr(lesson_time, ':') + 1)) AS formatted_lesson_time,
           lesson_name, 
           teacher_name, 
           classroom
    FROM schedule
    WHERE user_id = ?
    ORDER BY 
        CASE week_day
            WHEN 'Monday' THEN 1
            WHEN 'Tuesday' THEN 2
            WHEN 'Wednesday' THEN 3
            WHEN 'Thursday' THEN 4
            WHEN 'Friday' THEN 5
            WHEN 'Saturday' THEN 6
        END, 
        formatted_lesson_time;
    '''
    
    schedule_by_day = {}  # Словарь для хранения расписания по дням недели
    async with aiosqlite.connect('schedule.db') as db:
        cursor = await db.execute(schedule_query, (user_id,))
        rows = await cursor.fetchall()
    
    # Заполнение словаря
    days_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
    for day in days_of_week:
        schedule_by_day[day] = []

    for week_day, formatted_lesson_time, lesson_name, teacher_name, classroom in rows:
        schedule_by_day[week_day].append(f"{formatted_lesson_time} - {lesson_name}, {teacher_name}, ауд. {classroom}")
    
    response = "Ваше расписание:\n"
    # формирование и отправка сообщений
    for day in days_of_week:
        if schedule_by_day[day]:
            response += f"{day}:\n" + "\n".join(schedule_by_day[day]) + "\n\n"
        else:
            response += f"{day}:\nНет расписания.\n\n"

        if len(response) > 4000:
            await message.answer(response)
            response = ""
    
    # проверяем, остался ли текст для отправки
    if response:
        await message.answer(response)


# класс состояний для уведомлений
class Notification(StatesGroup):
    waiting_for_confirmation = State()
    waiting_for_time = State()
    waiting_for_timezone = State()

# перевод текста на английский язык
def translate_to_english(text):
  message_language = comprehend_client.detect_dominant_language(Text=text)["Languages"][0]["LanguageCode"]
  if message_language == "en":
    return text
  else:
    translated_text = translate_client.translate_text(Text=text, SourceLanguageCode=message_language, TargetLanguageCode="en")["TranslatedText"]
    return translated_text

# определение сентимента
def detect_sentiment(message):
   message = translate_to_english(message)
   sentiment = comprehend_client.detect_sentiment(Text=message, LanguageCode="en")["Sentiment"]
   return sentiment

# функция для проверки ввода дня недели
async def handle_invalid_week_day_input(message: types.Message):
    default_reply = "Пожалуйста, выберите корректный день недели."
    try:
        sentiment = detect_sentiment(message.text)
        if sentiment == "NEGATIVE":
            reply = random.choice(negative_replies) + " " + default_reply
        elif sentiment == "POSITIVE":
            reply = random.choice(positive_replies) + " " + default_reply
        elif sentiment == "MIXED":
            reply = random.choice(mixed_replies) + " " + default_reply
        else:
            reply = default_reply
    except Exception:
        reply = default_reply
    finally:
        await message.answer(reply, reply_markup=get_week_days_kb())


# обработчик команды /notification для настройки уведомлений
@dp.message_handler(commands=['notification'], state='*')
async def notification_command(message: types.Message):
    await message.answer(
        "Это автоматическая рассылка расписания.\n"
        "Вы можете подписаться на ежедневные уведомления с расписанием.\n"
        "Чтобы подписаться, ответьте 'да',\nчтобы отписаться — 'нет'.",
        reply_markup=types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True).add('Да', 'Нет', 'Назад')
    )
    await Notification.waiting_for_confirmation.set()

# обработчик подтверждения настройки уведомлений
@dp.message_handler(state=Notification.waiting_for_confirmation)
async def notification_confirmation(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if message.text.lower() == 'назад':
        await state.finish()
        await message.answer("Выберите действие:", reply_markup=main_menu_kb)
    elif message.text.lower() == 'да':
        await message.answer("Введите время для уведомлений в формате ЧЧ:ММ (например, 18:00):")
        await Notification.waiting_for_time.set()
    elif message.text.lower() == 'нет':
        async with aiosqlite.connect('schedule.db') as db:
            await db.execute('''UPDATE subscriptions SET active = 0 WHERE user_id = ?''', (user_id,))
            await db.commit()
        await message.answer("Вы отменили подписку на уведомления.", reply_markup=main_menu_kb)
        await state.finish()
    else:
        await message.answer("Пожалуйста, ответьте 'да' или 'нет'.")

# обработчик для установки времени уведомлений
@dp.message_handler(state=Notification.waiting_for_time)
async def set_notification_time(message: types.Message, state: FSMContext):
    notification_time = message.text
    try:
        datetime.strptime(notification_time, "%H:%M") # проверка корректности формата времени
        # сохраняем время во временное хранилище состояния
        async with state.proxy() as data:
            data['notification_time'] = notification_time
        # запрос выбора часового пояса
        await message.answer("Введите ваш часовой пояс в формате UTC+-N (Нужно ввести число N, например 7, 0 или -3):")
        await Notification.waiting_for_timezone.set()
    except ValueError:
        await message.answer("Неверный формат времени. Пожалуйста, используйте формат ЧЧ:ММ.")

# функция для конвертации времени пользователя в UTC
def convert_to_utc(user_time, timezone_offset):
    # timezone_offset ожидается в формате +-N
    offset = int(timezone_offset)
    user_datetime = datetime.strptime(user_time, "%H:%M")
    return (user_datetime - timedelta(hours=offset)).strftime("%H:%M")

# обработчик для установки часового пояса
@dp.message_handler(state=Notification.waiting_for_timezone)
async def set_timezone(message: types.Message, state: FSMContext):
    user_timezone = message.text # ожидается в формате +-N
    try:
        async with state.proxy() as data:
            user_id = message.from_user.id
            notification_time = data['notification_time']
            notification_time_utc = convert_to_utc(notification_time, user_timezone)

            async with aiosqlite.connect('schedule.db') as db:
                await db.execute('''INSERT INTO subscriptions (user_id, active, notification_time, timezone)
                                    VALUES (?, ?, ?, ?)
                                    ON CONFLICT(user_id) 
                                    DO UPDATE SET active = excluded.active, 
                                                   notification_time = excluded.notification_time,
                                                   timezone = excluded.timezone''',
                                (user_id, True, notification_time_utc, user_timezone))
                await db.commit()
            await message.answer(f"Уведомления установлены на {notification_time} (Часовой пояc в формате UTC: {user_timezone}).", reply_markup=main_menu_kb)
            await state.finish()
    except ValueError as e:
        await message.answer(str(e))

# функция для проверки и отправки уведомлений
async def check_and_send_notifications():
    utc_now = datetime.utcnow()

    async with aiosqlite.connect('schedule.db') as db:
        cursor = await db.execute('SELECT user_id, notification_time, timezone FROM subscriptions WHERE active = 1')
        subscriptions = await cursor.fetchall()

        for user_id, notification_time_utc, timezone_offset in subscriptions:
            # проверяем, соответствует ли текущее UTC время времени уведомления в UTC
            if utc_now.strftime("%H:%M") == notification_time_utc:
                # определяем локальное время пользователя
                user_local_time = utc_now + timedelta(hours=int(timezone_offset))
                # определяем завтрашний день для пользователя
                user_tomorrow = (user_local_time + timedelta(days=1)).strftime('%A')

                # получаем расписание на завтрашний день в локальном времени пользователя
                cursor = await db.execute('SELECT lesson_time, lesson_name, teacher_name, classroom FROM schedule WHERE user_id = ? AND week_day = ?', (user_id, user_tomorrow))
                schedule_entries = await cursor.fetchall()

                if schedule_entries:
                    message_text = f"Расписание на завтра ({user_tomorrow}):\n" + "\n".join(f"{time} - {name}, {teacher}, ауд. {classroom}" for time, name, teacher, classroom in schedule_entries)
                    logging.info(f"Отправка уведомления пользователю {user_id}.")
                    try:
                        await bot.send_message(user_id, message_text)
                    except Exception as e:
                        logging.error(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
                else:
                    logging.info(f"Нет расписания для отправки пользователю {user_id} на {user_tomorrow}")


# функция для запуска планировщика задач
async def scheduler():
    while True:
        await check_and_send_notifications()
        await asyncio.sleep(60) # ждем одну минуту перед следующей проверкой


# точка входа для запуска бота
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    loop = asyncio.get_event_loop()
    executor.start_polling(dp, on_startup=on_startup)
