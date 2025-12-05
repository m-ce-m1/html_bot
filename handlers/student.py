from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database.db import Database
from database.db_provider import get_db_instance
from handlers.common import (
    STUDENT_BTN_MATERIALS,
    STUDENT_BTN_STATS,
    STUDENT_BTN_TEST,
    STUDENT_BTN_TOPICS,
    get_main_keyboard,
)
from states.forms import TestState

router = Router(name="student")


def _db(_: Message | CallbackQuery) -> Database:
    return get_db_instance()


def _student_keyboard(user_id: int):
    return get_main_keyboard(user_id)


def _topics_kb(topics: list[dict], prefix: str, icon: str = "📘") -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for topic in topics:
        builder.button(
            text=f"{icon} {topic['title']}",
            callback_data=f"{prefix}:{topic['topic_id']}",
        )
    builder.adjust(1)
    return builder.as_markup()


async def _start_test_for_topic(
    message: Message,
    state: FSMContext,
    user_id: int,
    topic_id: int,
) -> None:
    db = _db(message)
    topics = await db.list_topics(include_hidden=False)
    topic = next((t for t in topics if t["topic_id"] == topic_id), None)
    if not topic:
        await message.answer(
            "Тема недоступна.",
            reply_markup=_student_keyboard(user_id),
        )
        return

    questions = await db.fetch_random_questions(topic_id, limit=10)
    if len(questions) < 10:
        await message.answer(
            "Для этой темы пока недостаточно вопросов. Сообщи преподавателю.",
            reply_markup=_student_keyboard(user_id),
        )
        return

    attempt_limit = topic.get("attempt_limit")
    if attempt_limit is not None:
        attempts_done = await db.get_attempt_count(user_id, topic_id)
        if attempts_done >= attempt_limit:
            await message.answer(
                "Лимит попыток по этой теме исчерпан.",
                reply_markup=_student_keyboard(user_id),
            )
            await state.clear()
            return
        await state.update_data(attempts_done=attempts_done)

    await state.set_state(TestState.answering)
    await state.update_data(
        topic_id=topic_id,
        topic_title=topic["title"],
        questions=questions,
        current=0,
        correct=0,
    )
    await message.answer(f"Начинаем тест по теме «{topic['title']}». Всего 10 вопросов.")
    await _send_question(message, state)


@router.message(Command("topics"))
@router.message(F.text == STUDENT_BTN_TOPICS)
async def cmd_topics(message: Message) -> None:
    db = _db(message)
    topics = await db.list_topics(include_hidden=False)
    if not topics:
        await message.answer(
            "Пока нет доступных тем. Подожди публикации урока.",
            reply_markup=_student_keyboard(message.from_user.id),
        )
        return

    lines = [f"📘 {t['title']}" for t in topics]
    await message.answer(
        "Доступные темы:\n" + "\n".join(lines),
        reply_markup=_student_keyboard(message.from_user.id),
    )


@router.message(Command("test"))
@router.message(F.text == STUDENT_BTN_TEST)
async def cmd_test(message: Message, state: FSMContext) -> None:
    db = _db(message)
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer(
            "Сначала пройди регистрацию командой /start.",
            reply_markup=_student_keyboard(message.from_user.id),
        )
        return

    topics = await db.list_topics(include_hidden=False)
    if not topics:
        await message.answer(
            "Нет тем, открытых для тестирования.",
            reply_markup=_student_keyboard(message.from_user.id),
        )
        return

    await state.set_state(TestState.choosing_topic)
    await message.answer(
        "Выбери тему для теста:",
        reply_markup=_topics_kb(topics, prefix="take_topic", icon="📝"),
    )


@router.callback_query(TestState.choosing_topic, F.data.startswith("take_topic:"))
async def process_test_topic(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    await _start_test_for_topic(call.message, state, call.from_user.id, topic_id)


# ОБНОВЛЁННАЯ ФУНКЦИЯ: варианты внутри текста, кнопки — A B C D
async def _send_question(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    idx = data["current"]
    question = data["questions"][idx]

    # Собираем варианты
    options = [
        question["option1"],
        question["option2"],
        question["option3"],
        question["option4"],
    ]

    # Формируем текст вопроса с вариантами A), B), C), D)
    question_text = f"Вопрос {idx + 1}/10\n\n{question['text']}\n\n"
    for i, opt in enumerate(options):
        letter = chr(ord('A') + i)
        question_text += f"{letter}) {opt}\n"

    # Кнопки: A, B, C, D
    builder = InlineKeyboardBuilder()
    for i in range(4):
        letter = chr(ord('A') + i)
        # Сохраняем прежнюю логику: option1 = 1, option2 = 2 и т.д.
        builder.button(
            text=letter,
            callback_data=f"answer:{idx}:{i + 1}",
        )
    builder.adjust(4)  # все кнопки в одной строке

    await message.answer(
        question_text.strip(),
        reply_markup=builder.as_markup(),
        parse_mode=None,
    )


@router.callback_query(TestState.answering, F.data.startswith("answer:"))
async def process_answer(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, idx_str, option_str = call.data.split(":")
    selected_idx = int(option_str)
    db = _db(call)
    data = await state.get_data()
    current_idx = int(idx_str)
    if current_idx != data["current"]:
        return  # игнорируем устаревшие ответы

    questions = data["questions"]
    if questions[current_idx]["correct_option"] == selected_idx:
        data["correct"] += 1
        await state.update_data(correct=data["correct"])

    next_idx = current_idx + 1
    if next_idx >= len(questions):
        await _finish_test(call.message, state, db, call.from_user.id)
        return

    await state.update_data(current=next_idx)
    await _send_question(call.message, state)


async def _finish_test(
    message: Message,
    state: FSMContext,
    db: Database,
    user_id: int,
) -> None:
    data = await state.get_data()
    score = data["correct"]
    max_score = len(data["questions"])
    topic_id = data["topic_id"]
    attempt_count = await db.get_attempt_count(user_id, topic_id)
    attempt_number = attempt_count + 1
    await db.save_attempt(
        user_id,
        topic_id,
        score,
        max_score,
        attempt_number=attempt_number,
    )
    await state.clear()
    percent = round(score / max_score * 100)
    await message.answer(
        f"Результат: {score} из {max_score} ({percent}%). "
        f"Попытка #{attempt_number}.",
        reply_markup=_student_keyboard(user_id),
    )
    builder = InlineKeyboardBuilder()
    builder.button(text="📈 Моя статистика", callback_data="student:stats")
    builder.button(text="🔁 Повторить тему", callback_data=f"student:retry:{topic_id}")
    builder.button(text="📂 Материалы темы", callback_data=f"materials:{topic_id}")
    builder.adjust(1)
    await message.answer("Выбери следующее действие:", reply_markup=builder.as_markup())


async def _send_stats(target_message: Message, user_id: int) -> None:
    db = _db(target_message)
    attempts = await db.get_attempts_by_user(user_id)
    if not attempts:
        await target_message.answer(
            "Ты ещё не проходил тесты.",
            reply_markup=_student_keyboard(user_id),
        )
        return

    lines = []
    for attempt in attempts[:10]:
        percent = round(attempt["score"] / attempt["max_score"] * 100)
        lines.append(
            f"{attempt['title']}: {attempt['score']}/{attempt['max_score']} ({percent}%) — "
            f"{attempt['timestamp']} Попытка #{attempt['attempt_number']}"
        )
    await target_message.answer(
        "Последние попытки:\n" + "\n".join(lines),
        reply_markup=_student_keyboard(user_id),
    )


@router.message(Command("stats"))
@router.message(F.text == STUDENT_BTN_STATS)
async def cmd_stats(message: Message) -> None:
    await _send_stats(message, message.from_user.id)


@router.callback_query(F.data == "student:stats")
async def stats_callback(call: CallbackQuery) -> None:
    await call.answer()
    await _send_stats(call.message, call.from_user.id)


@router.callback_query(F.data.startswith("student:retry:"))
async def retry_callback(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    await _start_test_for_topic(call.message, state, call.from_user.id, topic_id)


@router.message(Command("materials"))
@router.message(F.text == STUDENT_BTN_MATERIALS)
async def cmd_materials(message: Message) -> None:
    db = _db(message)
    topics = await db.list_topics(include_hidden=False)
    if not topics:
        await message.answer(
            "Материалы появятся после публикации уроков.",
            reply_markup=_student_keyboard(message.from_user.id),
        )
        return

    await message.answer(
        "Выбери тему для материалов:",
        reply_markup=_topics_kb(topics, prefix="materials", icon="📂"),
    )


@router.callback_query(F.data.startswith("materials:"))
async def process_materials(call: CallbackQuery) -> None:
    await call.answer()
    _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    db = _db(call)

    materials = await db.get_materials(topic_id)
    if not materials:
        await call.message.answer(
            "Материалы пока не добавлены.",
            reply_markup=_student_keyboard(call.from_user.id),
        )
        return

    for material in materials:
        title = material["title"]
        if material["type"] == "link":
            builder = InlineKeyboardBuilder()
            builder.button(text="🔗 Открыть", url=material["content"])
            await call.message.answer(title, reply_markup=builder.as_markup())
        elif material["type"] == "file":
            file_path = settings.materials_dir / material["content"]
            if file_path.exists():
                await call.message.answer_document(
                    document=FSInputFile(file_path),
                    caption=title,
                )
            else:
                await call.message.answer(
                    f"Файл {title} недоступен на сервере.",
                    reply_markup=_student_keyboard(call.from_user.id),
                )
        else:
            await call.message.answer(
                f"{title}\n\n{material['content']}",
                reply_markup=_student_keyboard(call.from_user.id),
            )
    await call.message.answer(
        "Готово! Используй клавиатуру ниже, чтобы продолжить.",
        reply_markup=_student_keyboard(call.from_user.id),
    )