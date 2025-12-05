from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from config import settings
from database.db import Database
from database.db_provider import get_db_instance
from states.forms import QuestionState, RegistrationState

STUDENT_BTN_MENU = "🏠 Меню"
STUDENT_BTN_TOPICS = "📚 Темы"
STUDENT_BTN_TEST = "📝 Тест"
STUDENT_BTN_MATERIALS = "📂 Материалы"
STUDENT_BTN_STATS = "📈 Статистика"
STUDENT_BTN_ASK = "❓ Вопрос"
STUDENT_BTN_HELP = "ℹ️ Помощь"

ADMIN_BTN_PANEL = "🛠 Админ-панель"
ADMIN_BTN_ANSWERS = "💬 Вопросы"
ADMIN_BTN_ADD_TOPIC = "➕ Тема"
ADMIN_BTN_UPLOAD_TEST = "📤 Тесты"

router = Router(name="common")


def _get_db(_: Message) -> Database:
    return get_db_instance()


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    buttons = [
        [KeyboardButton(text=STUDENT_BTN_MENU), KeyboardButton(text=STUDENT_BTN_TOPICS)],
        [KeyboardButton(text=STUDENT_BTN_TEST), KeyboardButton(text=STUDENT_BTN_MATERIALS)],
        [KeyboardButton(text=STUDENT_BTN_STATS), KeyboardButton(text=STUDENT_BTN_ASK)],
        [KeyboardButton(text=STUDENT_BTN_HELP)],
    ]
    if is_admin(user_id):
        buttons.append([KeyboardButton(text=ADMIN_BTN_PANEL), KeyboardButton(text=ADMIN_BTN_ANSWERS)])
        buttons.append([KeyboardButton(text=ADMIN_BTN_ADD_TOPIC), KeyboardButton(text=ADMIN_BTN_UPLOAD_TEST)])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    db = _get_db(message)
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer(
            "Привет! Я бот-помощник по изучению HTML.\n"
            "Перед началом напиши своё ФИО полностью.",
            reply_markup=remove_keyboard(),
        )
        await state.set_state(RegistrationState.waiting_full_name)
        return

    greeting = (
        "С возвращением, преподаватель!"
        if is_admin(message.from_user.id)
        else "Рады видеть снова! Продолжим учиться?"
    )
    await message.answer(
        f"{greeting}\n\nДоступные команды:\n"
        "/topics — посмотреть темы\n"
        "/test — пройти тест\n"
        "/materials — материалы\n"
        "/stats — моя статистика\n"
        "/ask — задать вопрос преподавателю\n"
        + (
            "\n\nКоманды преподавателя:\n"
            "/add_topic, /upload_test, /toggle_topic, /set_attempts, /all_stats, /export_stats, "
            "/answer_questions, /add_material, /broadcast, /backup_db"
            if is_admin(message.from_user.id)
            else ""
        )
        + "\n\nИспользуй клавиатуру ниже, чтобы быстро открывать разделы.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@router.message(RegistrationState.waiting_full_name)
async def process_full_name(message: Message, state: FSMContext) -> None:
    full_name = (message.text or "").strip()
    if len(full_name.split()) < 2:
        await message.answer("Пожалуйста, укажи ФИО полностью.")
        return

    db = _get_db(message)
    role = "admin" if is_admin(message.from_user.id) else "student"
    await db.upsert_user(message.from_user.id, full_name, role=role)
    await state.clear()
    await message.answer(
        "Спасибо! Регистрация завершена. Используй кнопки ниже, чтобы открыть темы или тест.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@router.message(Command("menu"))
@router.message(F.text == STUDENT_BTN_MENU)
async def cmd_menu(message: Message) -> None:
    await message.answer(
        "Главное меню. Выбирай, что хочешь сделать дальше 👇",
        reply_markup=get_main_keyboard(message.from_user.id),
    )


@router.message(Command("help"))
@router.message(F.text == STUDENT_BTN_HELP)
async def cmd_help(message: Message) -> None:
    help_text = (
        "Команды ученика:\n"
        "/topics — список доступных тем\n"
        "/test — пройти тест\n"
        "/stats — личная статистика\n"
        "/materials — материалы\n"
        "/ask — задать вопрос преподавателю"
    )
    if is_admin(message.from_user.id):
        help_text += (
            "\n\nКоманды преподавателя:\n"
            "/add_topic — добавить тему\n"
            "/upload_test — загрузить вопросы\n"
            "/toggle_topic — открыть/закрыть доступ к теме\n"
            "/set_attempts — изменить лимит попыток\n"
            "/all_stats — общая статистика\n"
            "/export_stats — экспорт в Excel\n"
            "/answer_questions — ответить на вопросы\n"
            "/add_material — добавить материалы\n"
            "/broadcast — уведомление ученикам\n"
            "/backup_db — получить резервную копию БД"
        )
    await message.answer(help_text, reply_markup=get_main_keyboard(message.from_user.id))


@router.message(Command("ask"))
@router.message(F.text == STUDENT_BTN_ASK)
async def cmd_ask(message: Message, state: FSMContext) -> None:
    db = _get_db(message)
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Сначала зарегистрируйся командой /start.")
        return

    await state.set_state(QuestionState.awaiting_question)
    await message.answer(
        "Напиши свой вопрос преподавателю. Не оставляй поле пустым.",
        reply_markup=remove_keyboard(),
    )


@router.message(QuestionState.awaiting_question)
async def process_question(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сообщение не может быть пустым. Попробуй ещё раз.")
        return

    db = _get_db(message)
    admin_id = settings.admin_ids[0]
    admin_record = await db.get_user(admin_id)
    if not admin_record:
        await db.upsert_user(admin_id, "Администратор", role="admin")
    message_id = await db.record_message(
        from_user_id=message.from_user.id,
        to_user_id=admin_id,
        text=text,
    )
    await state.clear()
    await message.answer(
        "Вопрос отправлен преподавателю. Ожидай ответ в этом чате.",
        reply_markup=get_main_keyboard(message.from_user.id),
    )

    try:
        await message.bot.send_message(
            admin_id,
            f"Вопрос #{message_id} от {message.from_user.full_name}:\n\n{text}\n\n"
            f"Ответь командой /answer_questions.",
        )
    except Exception:
        # Игнорируем ошибки отправки уведомления администратору.
        return

