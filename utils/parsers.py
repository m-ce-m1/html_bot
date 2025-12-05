from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable


def parse_csv_questions(file_path: Path) -> list[dict]:
    questions: list[dict] = []
    with file_path.open("r", encoding="utf-8", newline="") as fp:
        reader = csv.reader(fp, delimiter=";")
        for line_no, row in enumerate(reader, start=1):
            if len(row) < 6:
                raise ValueError(f"Строка {line_no}: ожидалось 6 столбцов, получено {len(row)}.")
            question, *options, correct = row[:6]
            correct_idx = int(correct)
            if not 1 <= correct_idx <= 4:
                raise ValueError(f"Строка {line_no}: номер ответа должен быть от 1 до 4.")
            questions.append(
                {
                    "text": question.strip(),
                    "option1": options[0].strip(),
                    "option2": options[1].strip(),
                    "option3": options[2].strip(),
                    "option4": options[3].strip(),
                    "correct_option": correct_idx,
                }
            )
    return questions


def parse_txt_questions(file_path: Path) -> list[dict]:
    """
    Парсит TXT файл с форматом подобным CSV (каждая строка - разделитель ';').
    Формат: Вопрос;Вариант1;Вариант2;Вариант3;Вариант4;НомерПравильного
    """
    content = file_path.read_text(encoding="utf-8")
    questions: list[dict] = []
    
    for line_no, line in enumerate(content.splitlines(), start=1):
        line = line.strip()
        if not line:  # Пропускаем пустые строки
            continue
            
        parts = line.split(";")
        if len(parts) < 6:
            raise ValueError(f"Строка {line_no}: ожидалось 6 частей (разделитель ';'), получено {len(parts)}.")
        
        question, *options, correct = parts[:6]
        correct_idx = int(correct.strip())
        
        if not 1 <= correct_idx <= 4:
            raise ValueError(f"Строка {line_no}: номер ответа должен быть от 1 до 4.")
        
        questions.append(
            {
                "text": question.strip(),
                "option1": options[0].strip(),
                "option2": options[1].strip(),
                "option3": options[2].strip(),
                "option4": options[3].strip(),
                "correct_option": correct_idx,
            }
        )
    
    if not questions:
        raise ValueError("Не удалось найти вопросы в TXT/CSV файле. Проверь формат.")
    
    return questions







