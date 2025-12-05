from __future__ import annotations

import tempfile
from pathlib import Path

from html import escape as html_escape

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import settings
from database.db import Database, TopicFilters
from database.db_provider import get_db_instance
from handlers.common import (
    ADMIN_BTN_ANSWERS,
    ADMIN_BTN_PANEL,
    ADMIN_BTN_ADD_TOPIC,
    ADMIN_BTN_UPLOAD_TEST,
)
from states.forms import (
    AddTopicState,
    DeleteMaterialState,
    MaterialState,
    QuestionState,
    UploadTestState,
)
from utils.exporter import export_attempts_to_excel
from utils.parsers import parse_csv_questions, parse_txt_questions

router = Router(name="admin")

ADMIN_PANEL_SECTIONS = [
    ("topics", "📘 Темы"),
    ("tests", "📝 Тесты"),
    ("materials", "📂 Материалы"),
    ("broadcast", "✉️ Рассылка"),
    ("stats", "📊 Статистика"),
    ("questions", "💬 Вопросы"),
]

ADMIN_PANEL_HINTS = {
    "topics": "Управление темами:\n/add_topic <название>\n/toggle_topic\n/set_attempts <topic_id> <число|unlimited>",
    "tests": "Работа с тестами:\n/upload_test\n/set_attempts <topic_id> <число|unlimited>",
    "materials": "Материалы:\n/add_material\n/material_topic...\n/backup_db для резервной копии",
    "broadcast": "Рассылка:\n/broadcast <текст>",
    "stats": "Статистика:\n/all_stats\n/export_stats",
}

ADMIN_PANEL_ACTIONS = {
    "topics": [
        ("list", "📃 Список тем"),
        ("toggle_hint", "🔁 Как открыть/закрыть"),
        ("limit_hint", "🎯 Лимиты попыток"),
    ],
    "tests": [
        ("templates", "📄 Шаблон вопросов"),
    ],
    "materials": [
        ("general", "📂 Общие материалы"),
        ("remove_hint", "🗑 Удаление материалов"),
    ],
    "broadcast": [
        ("hint", "✉️ Инструкция по рассылке"),
    ],
    "stats": [
        ("overview", "📊 Сводка"),
    ],
}

QUESTION_TEMPLATE_TEXT = (
    "TXT/CSV шаблон (разделитель ';'):\n"
    "Вопрос;Вариант1;Вариант2;Вариант3;Вариант4;НомерПравильного\n"
    "Какой тег выделяет абзац?;&lt;p&gt;;&lt;div&gt;;&lt;h1&gt;;&lt;span&gt;;1\n\n"

)


def _is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def _db(_: Message | CallbackQuery) -> Database:
    return get_db_instance()


async def _ensure_admin(message: Message) -> bool:
    if not _is_admin(message.from_user.id):
        await message.answer("Эта команда доступна только преподавателю.")
        return False
    return True


def _topics_keyboard(topics: list[dict], prefix: str, include_general: bool = False):
    builder = InlineKeyboardBuilder()
    if include_general:
        builder.button(text="🌐 Общие материалы", callback_data=f"{prefix}:0")
    for topic in topics:
        status_icon = "🟢" if topic["is_available"] else "⚪️"
        builder.button(
            text=f"{status_icon} {topic['title']} (ID {topic['topic_id']})",
            callback_data=f"{prefix}:{topic['topic_id']}",
        )
    builder.adjust(1)
    return builder.as_markup()


async def _send_test_templates(message: Message) -> None:
    await message.answer(QUESTION_TEMPLATE_TEXT)


def _safe_slug(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() else "_" for ch in value.lower())
    parts = [chunk for chunk in normalized.split("_") if chunk]
    return "_".join(parts) or "topic"


async def _send_materials_for_removal(
    message: Message, state: FSMContext, topic_id: int | None
) -> None:
    db = _db(message)
    materials = await db.get_materials(topic_id, include_general=False)
    if not materials:
        label = "общего раздела" if topic_id is None else "выбранной темы"
        await message.answer(f"Материалы для {label} не найдены.")
        await state.clear()
        return
    builder = InlineKeyboardBuilder()
    for material in materials[:40]:
        title = material["title"]
        short_title = title if len(title) <= 25 else f"{title[:22]}…"
        builder.button(
            text=f"🗑 #{material['material_id']} ({short_title})",
            callback_data=f"remove_material:{material['material_id']}",
        )
    builder.adjust(1)
    await message.answer(
        "Выбери материал для удаления:",
        reply_markup=builder.as_markup(),
    )
    await state.set_state(DeleteMaterialState.choosing_material)


async def _send_topics_overview(message: Message) -> None:
    db = _db(message)
    topics = await db.list_topics(include_hidden=True)
    if not topics:
        await message.answer("Темы ещё не созданы.")
        return
    lines = []
    for topic in topics:
        status = "ON" if topic["is_available"] else "OFF"
        limit = topic.get("attempt_limit")
        limit_text = "∞" if limit is None else str(limit)
        lines.append(
            f"{html_escape(topic['title'])} — {status}, попыток: {limit_text}, id={topic['topic_id']}"
        )
    await message.answer("Темы:\n" + "\n".join(lines))


async def _send_materials_overview(message: Message) -> None:
    db = _db(message)
    materials = await db.get_materials(topic_id=None, include_general=True)
    if not materials:
        await message.answer("Материалы отсутствуют.")
        return
    lines = []
    for material in materials[:20]:
        scope = "общие" if material["topic_id"] is None else f"topic_id={material['topic_id']}"
        lines.append(
            f"#{material['material_id']} [{material['type']}] {html_escape(material['title'])} — {scope}"
        )
    more = ""
    if len(materials) > 20:
        more = f"\n... и ещё {len(materials) - 20} записей."
    await message.answer("Материалы:\n" + "\n".join(lines) + more)


async def _send_materials_remove_hint(message: Message) -> None:
    await message.answer(
        "Используй /remove_material, чтобы выбрать тему и удалить конкретный материал.\n"
        "Файлы типа file будут удалены и из каталога materials."
    )


async def _send_broadcast_hint(message: Message) -> None:
    await message.answer(
        "Команда /broadcast отправляет сообщение всем студентам.\n"
        "Формат: /broadcast Текст уведомления\n"
        "Советы:\n"
        "• заранее протестируй текст на себе\n"
        "• избегай слишком длинных сообщений\n"
        "• бот сообщит число доставок"
    )


async def _send_stats_overview(message: Message) -> None:
    db = _db(message)
    records = await db.get_statistics(TopicFilters())
    if not records:
        await message.answer("Попыток тестов пока нет.")
        return
    total = len(records)
    avg_percent = sum(r["score"] / r["max_score"] for r in records) / total * 100
    unique_users = len({r["user_id"] for r in records})
    await message.answer(
        f"Сводка:\nВсего попыток: {total}\nУникальных пользователей: {unique_users}\n"
        f"Средний результат: {avg_percent:.1f}%"
    )


@router.message(Command("admin_panel"))
@router.message(F.text == ADMIN_BTN_PANEL)
async def cmd_admin_panel(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    builder = InlineKeyboardBuilder()
    for code, label in ADMIN_PANEL_SECTIONS:
        builder.button(text=label, callback_data=f"panel:{code}")
    builder.adjust(2)
    await message.answer(
        "Админ-панель: выбери раздел для подсказок или работы с вопросами.",
        reply_markup=builder.as_markup(),
    )


@router.callback_query(F.data.startswith("panel:"))
async def panel_callbacks(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    _, action = call.data.split(":")
    if action == "questions":
        await call.answer()
        await _show_open_questions(call.message)
        return
    hint = ADMIN_PANEL_HINTS.get(action)
    buttons = ADMIN_PANEL_ACTIONS.get(action, [])
    markup = None
    if buttons:
        builder = InlineKeyboardBuilder()
        for code, label in buttons:
            builder.button(text=label, callback_data=f"panel_action:{action}:{code}")
        builder.adjust(1 if len(buttons) <= 2 else 2)
        markup = builder.as_markup()
    if hint:
        await call.message.answer(html_escape(hint), reply_markup=markup)
    elif markup:
        await call.message.answer("Выбери действие:", reply_markup=markup)
    else:
        await call.answer("Раздел в разработке.", show_alert=True)
        return
    await call.answer()


@router.callback_query(F.data.startswith("panel_action:"))
async def panel_action_handler(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    _, section, action = call.data.split(":")
    handlers_map = {
        ("topics", "list"): _send_topics_overview,
        ("tests", "templates"): _send_test_templates,
        ("materials", "general"): _send_materials_overview,
        ("materials", "remove_hint"): _send_materials_remove_hint,
        ("broadcast", "hint"): _send_broadcast_hint,
        ("stats", "overview"): _send_stats_overview,
    }
    key = (section, action)
    if key in handlers_map:
        await call.answer()
        await handlers_map[key](call.message)
        return
    if section == "topics" and action == "toggle_hint":
        await call.answer()
        await call.message.answer("Введи /toggle_topic и следуй кнопкам выбора темы.")
        return
    if section == "topics" and action == "limit_hint":
        await call.answer()
        await call.message.answer(
            "Формат: /set_attempts <topic_id> <число|unlimited>\n"
            "Например: /set_attempts 5 3"
        )
        return
    await call.answer("Действие скоро появится.", show_alert=True)


@router.message(Command("add_topic"))
async def cmd_add_topic(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /add_topic Название темы")
        return

    title = parts[1].strip()
    topic_id = await db.add_topic(title, attempt_limit=settings.attempt_limit_per_topic)
    await message.answer(f"Тема «{title}» добавлена с ID {topic_id}. Используй /toggle_topic для открытия доступа.")


@router.message(F.text == ADMIN_BTN_ADD_TOPIC)
async def cmd_add_topic_button(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    await state.set_state(AddTopicState.waiting_title)
    await message.answer("Отправь название новой темы одним сообщением.")


@router.message(AddTopicState.waiting_title)
async def process_new_topic_title(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым. Попробуй ещё раз.")
        return
    db = _db(message)
    try:
        topic_id = await db.add_topic(title, attempt_limit=settings.attempt_limit_per_topic)
    except Exception:
        await message.answer("Не удалось добавить тему. Проверь, что название уникально.")
        return
    await state.clear()
    await message.answer(f"Тема «{title}» добавлена с ID {topic_id}. Используй /toggle_topic для открытия доступа.")


@router.message(Command("toggle_topic"))
async def cmd_toggle_topic(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    topics = await db.list_topics(include_hidden=True)
    if not topics:
        await message.answer("Темы ещё не созданы.")
        return
    await message.answer(
        "Выбери тему для переключения статуса:",
        reply_markup=_topics_keyboard(topics, prefix="toggle_topic"),
    )


@router.callback_query(F.data.startswith("toggle_topic:"))
async def toggle_topic_callback(call: CallbackQuery) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    db = _db(call)
    topic = await db.get_topic(topic_id)
    if not topic:
        await call.answer("Тема не найдена", show_alert=True)
        return
    new_state = not bool(topic["is_available"])
    await db.set_topic_availability(topic_id, new_state)
    await call.message.answer(
        f"Тема «{topic['title']}» теперь {'доступна' if new_state else 'закрыта'} для учеников."
    )
    await call.answer()


@router.message(Command("set_attempts"))
async def cmd_set_attempts(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование: /set_attempts &lt;topic_id&gt; &lt;число|unlimited&gt;")
        return
    try:
        topic_id = int(parts[1])
    except ValueError:
        await message.answer("topic_id должен быть числом.")
        return
    limit_value = parts[2].lower()
    try:
        attempt_limit = None if limit_value in {"inf", "unlimited", "none"} else int(limit_value)
    except ValueError:
        await message.answer("Лимит должен быть числом или unlimited.")
        return
    if not await db.get_topic(topic_id):
        await message.answer("Тема не найдена.")
        return
    await db.set_topic_attempt_limit(topic_id, attempt_limit)
    if attempt_limit is None:
        await message.answer("Лимит попыток снят.")
    else:
        await message.answer(f"Лимит попыток установлен: {attempt_limit}.")


@router.message(Command("upload_test"))
@router.message(F.text == ADMIN_BTN_UPLOAD_TEST)
async def cmd_upload_test(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    topics = await db.list_topics(include_hidden=True)
    if not topics:
        await message.answer("Сначала добавь темы через /add_topic.")
        return
    await state.set_state(UploadTestState.choosing_topic)
    await message.answer(
        "Выбери тему для загрузки вопросов:",
        reply_markup=_topics_keyboard(topics, prefix="upload_topic"),
    )


@router.callback_query(UploadTestState.choosing_topic, F.data.startswith("upload_topic:"))
async def choose_topic_for_upload(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    db = _db(call)
    topic = await db.get_topic(topic_id)
    if not topic:
        await call.message.answer("Тема не найдена.")
        return
    await state.update_data(topic_id=topic_id, topic_title=topic["title"])
    await state.set_state(UploadTestState.awaiting_file)
    await call.message.answer(
        f"Загрузи CSV или TXT файл с вопросами для темы «{topic['title']}». Максимум 4 варианта в вопросе."
    )
    await _send_test_templates(call.message)


@router.message(UploadTestState.awaiting_file, F.document)
async def process_test_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    document = message.document
    if document is None:
        await message.answer("Нужно отправить файл.")
        return
    file_suffix = Path(document.file_name or "").suffix.lower()
    data = await state.get_data()
    topic_id = data.get("topic_id")
    if not topic_id:
        await message.answer("Тема не выбрана. Используй /upload_test заново.")
        await state.clear()
        return

    bot = message.bot
    file = await bot.get_file(document.file_id)
    with tempfile.NamedTemporaryFile(delete=False, suffix=file_suffix) as tmp:
        await bot.download(file, destination=tmp.name)
        temp_path = Path(tmp.name)

    try:
        if file_suffix == ".csv":
            questions = parse_csv_questions(temp_path)
        elif file_suffix == ".txt":
            questions = parse_txt_questions(temp_path)
        else:
            await message.answer("Поддерживаются только CSV или TXT файлы.")
            return
    except ValueError as exc:
        await message.answer(f"Ошибка разбора: {exc}")
        return
    finally:
        temp_path.unlink(missing_ok=True)

    db = _db(message)
    added = await db.add_questions(topic_id, questions)
    await state.clear()
    await message.answer(f"Загружено {added} вопросов.")


@router.message(Command("all_stats"))
async def cmd_all_stats(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    records = await db.get_statistics(TopicFilters())
    if not records:
        await message.answer("Попыток тестов пока нет.")
        return
    total = len(records)
    avg_percent = sum(r["score"] / r["max_score"] for r in records) / total * 100
    await message.answer(f"Всего попыток: {total}\nСредний результат: {avg_percent:.1f}%")


@router.message(Command("export_stats"))
async def cmd_export_stats(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    topics = await db.list_topics(include_hidden=True)
    if not topics:
        await message.answer("Темы ещё не созданы.")
        return

    exported = 0
    for topic in topics:
        filters = TopicFilters(topic_id=topic["topic_id"])
        data = await db.get_statistics(filters)
        slug = _safe_slug(topic["title"])
        export_path = settings.stats_export_dir / f"stats_topic_{topic['topic_id']}_{slug}.xlsx"
        export_attempts_to_excel(data, export_path)
        await message.answer_document(
            document=FSInputFile(export_path),
            caption=f"Статистика по теме «{topic['title']}».",
        )
        exported += 1

    await message.answer(f"Выгружено файлов: {exported}.")


@router.message(Command("answer_questions"))
@router.message(F.text == ADMIN_BTN_ANSWERS)
async def cmd_answer_questions(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    await _show_open_questions(message)


async def _show_open_questions(message: Message) -> None:
    db = _db(message)
    open_questions = await db.get_open_questions()
    if not open_questions:
        await message.answer("Нет необработанных вопросов.")
        return
    lines = []
    for item in open_questions:
        full_name = item.get("full_name") or "Пользователь"
        text = item.get("text") or ""
        lines.append(
            f"#{item['message_id']} от {html_escape(full_name)}:\n"
            f"{html_escape(text)}\n{item['timestamp']}"
        )
    builder = InlineKeyboardBuilder()
    for item in open_questions:
        builder.button(
            text=f"Ответить #{item['message_id']}",
            callback_data=f"answer_select:{item['message_id']}",
        )
    builder.adjust(1)
    await message.answer(
        "Открытые вопросы:\n\n"
        + "\n\n".join(lines)
        + "\n\nВыбери вопрос для ответа или используй /reply &lt;ID&gt; &lt;ответ&gt;.",
        reply_markup=builder.as_markup(),
    )


@router.message(Command("reply"))
async def cmd_reply(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("Использование: /reply &lt;message_id&gt; &lt;ответ&gt;")
        return
    try:
        message_id = int(parts[1])
    except ValueError:
        await message.answer("ID должен быть числом.")
        return
    answer_text = parts[2]
    db = _db(message)
    question = await db.get_message(message_id)
    if not question:
        await message.answer("Сообщение не найдено.")
        return
    await message.bot.send_message(
        question["from_user_id"],
        f"Ответ от преподавателя:\n\n{html_escape(answer_text)}",
    )
    await db.record_message(
        from_user_id=message.from_user.id,
        to_user_id=question["from_user_id"],
        text=answer_text,
        is_answered=True,
    )
    await db.mark_message_answered(message_id)
    await message.answer("Ответ отправлен.")


@router.callback_query(F.data.startswith("answer_select:"))
async def answer_select(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    _, message_id_str = call.data.split(":")
    message_id = int(message_id_str)
    db = _db(call)
    question = await db.get_message(message_id)
    if not question or question["is_answered"]:
        await call.answer("Сообщение недоступно", show_alert=True)
        return
    student = await db.get_user(question["from_user_id"])
    student_name = student["full_name"] if student else "Пользователь"
    await state.set_state(QuestionState.awaiting_answer)
    await state.update_data(
        answer_message_id=message_id,
        answer_student_id=question["from_user_id"],
    )
    question_text = question.get("text") or ""
    await call.message.answer(
        f"Ответ на вопрос #{message_id} от {html_escape(student_name)}:\n"
        f"{html_escape(question_text)}\n\n"
        "Отправь текст ответа одним сообщением.",
    )
    await call.answer("Введите ответ", show_alert=False)


@router.message(QuestionState.awaiting_answer)
async def process_answer_input(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    text = (message.text or "").strip()
    if not text:
        await message.answer("Ответ не может быть пустым.")
        return
    data = await state.get_data()
    message_id = data.get("answer_message_id")
    student_id = data.get("answer_student_id")
    if not message_id or not student_id:
        await message.answer("Не выбран вопрос. Используй /answer_questions.")
        await state.clear()
        return
    db = _db(message)
    question = await db.get_message(message_id)
    if not question:
        await message.answer("Сообщение не найдено.")
        await state.clear()
        return
    await message.bot.send_message(
        student_id,
        f"Ответ от преподавателя:\n\n{html_escape(text)}",
    )
    await db.record_message(
        from_user_id=message.from_user.id,
        to_user_id=student_id,
        text=text,
        is_answered=True,
    )
    await db.mark_message_answered(message_id)
    await state.clear()
    await message.answer("Ответ отправлен.")


@router.message(Command("add_material"))
async def cmd_add_material(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    topics = await db.list_topics(include_hidden=True)
    await state.set_state(MaterialState.choosing_topic)
    await message.answer(
        "Выбери тему для материала (или общий раздел):",
        reply_markup=_topics_keyboard(topics, prefix="material_topic", include_general=True),
    )


@router.callback_query(MaterialState.choosing_topic, F.data.startswith("material_topic:"))
async def process_material_topic(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    await state.update_data(topic_id=None if topic_id == 0 else topic_id)
    await state.set_state(MaterialState.choosing_type)
    builder = InlineKeyboardBuilder()
    for material_type, label in [("link", "Ссылка"), ("file", "Файл"), ("text", "Текст")]:
        builder.button(text=label, callback_data=f"material_type:{material_type}")
    builder.adjust(3)
    await call.message.answer("Выбери тип материала:", reply_markup=builder.as_markup())


@router.callback_query(MaterialState.choosing_type, F.data.startswith("material_type:"))
async def process_material_type(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, material_type = call.data.split(":")
    await state.update_data(material_type=material_type)
    await state.set_state(MaterialState.awaiting_payload)
    if material_type == "file":
        await call.message.answer("Пришли файл (PDF/DOCX и т.д.). Название возьмём из имени файла.")
    elif material_type == "link":
        await call.message.answer("Отправь сообщение в формате: Название:::https://ссылка")
    else:
        await call.message.answer("Отправь сообщение в формате: Название:::текст материала")


@router.message(Command("remove_material"))
async def cmd_remove_material(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    topics = await db.list_topics(include_hidden=True)
    await state.set_state(DeleteMaterialState.choosing_topic)
    await message.answer(
        "Выбери тему для удаления материала (или общий раздел):",
        reply_markup=_topics_keyboard(topics, prefix="remove_topic", include_general=True),
    )


@router.callback_query(DeleteMaterialState.choosing_topic, F.data.startswith("remove_topic:"))
async def process_remove_material_topic(call: CallbackQuery, state: FSMContext) -> None:
    await call.answer()
    _, topic_id_str = call.data.split(":")
    topic_id = int(topic_id_str)
    selected_topic = None if topic_id == 0 else topic_id
    await state.update_data(remove_topic_id=selected_topic)
    await _send_materials_for_removal(call.message, state, selected_topic)


@router.callback_query(DeleteMaterialState.choosing_material, F.data.startswith("remove_material:"))
async def process_remove_material(call: CallbackQuery, state: FSMContext) -> None:
    if not _is_admin(call.from_user.id):
        await call.answer("Нет прав", show_alert=True)
        return
    _, material_id_str = call.data.split(":")
    material_id = int(material_id_str)
    db = _db(call)
    material = await db.get_material(material_id)
    if not material:
        await call.answer("Материал не найден", show_alert=True)
        return
    if material["type"] == "file":
        file_path = settings.materials_dir / material["content"]
        file_path.unlink(missing_ok=True)
    await db.delete_material(material_id)
    await call.answer("Материал удалён")
    data = await state.get_data()
    topic_id = data.get("remove_topic_id")
    await _send_materials_for_removal(call.message, state, topic_id)


@router.message(MaterialState.awaiting_payload, F.document)
async def process_material_file(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    data = await state.get_data()
    if data.get("material_type") != "file":
        await message.answer("Сейчас ожидается текстовый материал. Отмени командой /cancel.")
        return
    document = message.document
    if not document:
        await message.answer("Нужно отправить файл.")
        return
    filename = document.file_name or f"material_{document.file_id}"
    safe_name = filename.replace(" ", "_")
    file_path = settings.materials_dir / safe_name
    await message.bot.download(document, destination=str(file_path))
    db = _db(message)
    await db.add_material(
        title=filename,
        content=safe_name,
        material_type="file",
        topic_id=data.get("topic_id"),
    )
    await state.clear()
    await message.answer("Материал сохранён.")


@router.message(MaterialState.awaiting_payload)
async def process_material_text(message: Message, state: FSMContext) -> None:
    if not await _ensure_admin(message):
        return
    data = await state.get_data()
    material_type = data.get("material_type")
    if material_type not in {"link", "text"}:
        await message.answer("Ожидается файл. Используй /add_material заново.")
        return
    payload_text = (message.text or "").strip()
    if ":::" not in payload_text:
        await message.answer("Используй формат: Название:::контент")
        return
    title, payload = [part.strip() for part in payload_text.split(":::", maxsplit=1)]
    db = _db(message)
    await db.add_material(
        title=title,
        content=payload,
        material_type=material_type,
        topic_id=data.get("topic_id"),
    )
    await state.clear()
    await message.answer("Материал добавлен.")


@router.message(Command("broadcast"))
async def cmd_broadcast(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: /broadcast Текст сообщения")
        return
    text = parts[1]
    db = _db(message)
    students = await db.list_users(role="student")
    sent = 0
    for student in students:
        try:
            await message.bot.send_message(student["user_id"], text)
            sent += 1
        except Exception:
            continue
    await message.answer(f"Рассылка завершена. Успешно: {sent}/{len(students)}.")


@router.message(Command("backup_db"))
async def cmd_backup_db(message: Message) -> None:
    if not await _ensure_admin(message):
        return
    db = _db(message)
    backup_path = await db.backup_file()
    await message.answer_document(FSInputFile(backup_path), caption="Резервная копия базы данных.")

