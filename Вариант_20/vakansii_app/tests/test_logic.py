"""Юнит-тесты логики и хранилища данных.

Эти тесты работают БЕЗ графического интерфейса. Они проверяют:
- бизнес-логику (ApplicationLogic);
- слой данных (DataAccessLayer);
- валидацию и восстановление данных.

Запуск: python -m unittest discover -s tests -v

Почему тесты важны:
1. Они документируют поведение — видно, как должны работать функции;
2. При изменении кода тесты показывают, если что-то сломалось;
3. Можно безопасно рефакторить, зная, что тесты не пропустят ошибки.

tempfile.TemporaryDirectory() — создаёт временную папку для каждого теста.
Это изолирует тесты: данные одного теста не влияют на другой.
"""

import os
import sys
import tempfile
import unittest

# Добавляем корень проекта в sys.path, чтобы импортировать app.
# Без этого Python не найдёт модули app/ при запуске из tests/.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.data_access import DataAccessLayer, ValidationError
from app.logic import ApplicationLogic

# Пример валидной формы вакансии — используется во многих тестах.
# dict() — удобная функция для создания словаря из именованных аргументов.
VALID_FORM = {
    "company": "ООО \u00ab\u0422\u0435\u0441\u0442\u00bb",
    "position": "\u0420\u0430\u0437\u0440\u0430\u0431\u043e\u0442\u0447\u0438\u043a",
    "salary": "100 000",
    "source": "hh.ru",
    "link": "",
    "status": "\u041e\u0442\u043a\u043b\u0438\u043a",
    "response_date": "2026-11-01",
    "event_date": "2026-11-10",
    "event_text": "\u0421\u043e\u0437\u0432\u043e\u043d \u0441 HR",
}


class DataAccessTests(unittest.TestCase):
    """
    Все тесты наследуются от unittest.TestCase.

    setUp() вызывается ПЕРЕД каждым тестом — создаём чистое хранилище.
    tearDown() вызывается ПОСЛЕ каждого теста — удаляем временную папку.
    Это гарантирует изоляцию: каждый тест работает с пустыми данными.
    """

    def setUp(self):
        # tempfile.TemporaryDirectory() создаёт временную папку,
        # которая автоматически удаляется при tearDown().
        self.tmp = tempfile.TemporaryDirectory()
        # Передаём временную папку в DAL — данные не попадут на диск проекта.
        self.dal = DataAccessLayer(data_dir=self.tmp.name)
        self.logic = ApplicationLogic(self.dal)

    def tearDown(self):
        # Удаляем временную папку.
        self.tmp.cleanup()

    def test_add_and_status_counts(self):
        """Проверка: добавление вакансии и подсчёт статистики."""
        self.logic.add_vacancy(dict(VALID_FORM))
        rows, total = self.logic.status_counts()
        self.assertEqual(total, 1)
        counts = {s: c for s, c, _ in rows}
        self.assertEqual(counts["Отклик"], 1)
        self.assertEqual(counts["Оффер"], 0)
        # При добавлении вакансии автоматически создаётся напоминание.
        self.assertEqual(len(self.dal.reminders()), 1)

    def test_empty_position_rejected(self):
        """Проверка: пустая позиция (только пробелы) отклоняется."""
        form = dict(VALID_FORM, position="   ")
        with self.assertRaises(ValidationError):
            self.logic.add_vacancy(form)

    def test_bad_status_rejected(self):
        """Проверка: неизвестный статус отклоняется."""
        form = dict(VALID_FORM, status="HR-скрин")
        with self.assertRaises(ValidationError):
            self.logic.add_vacancy(form)

    def test_bad_date_rejected(self):
        """Проверка: некорректный формат даты отклоняется."""
        form = dict(VALID_FORM, response_date="01.11.2026")
        with self.assertRaises(ValidationError):
            self.logic.add_vacancy(form)

    def test_update_and_delete_cascade(self):
        """Проверка: обновление вакансии и каскадное удаление."""
        vacancy = self.logic.add_vacancy(dict(VALID_FORM))
        self.logic.add_note(vacancy["id"], "2026-11-01", "Проверка")
        self.logic.update_vacancy(vacancy["id"], dict(VALID_FORM, status="Тестовое"))
        self.assertEqual(self.logic.vacancy_by_id(vacancy["id"])["status"], "Тестовое")
        self.logic.delete_vacancy(vacancy["id"])
        self.assertIsNone(self.logic.vacancy_by_id(vacancy["id"]))
        # Заметки и напоминания удалены каскадно.
        self.assertEqual(self.dal.notes(vacancy["id"]), [])
        self.assertEqual(self.dal.reminders(vacancy["id"]), [])

    def test_period_statistics(self):
        """Проверка: статистика за период фильтрует правильно."""
        self.logic.add_vacancy(dict(VALID_FORM))
        self.logic.add_vacancy(dict(VALID_FORM, position="Аналитик",
                                    response_date="2026-10-15", status="Отказ"))
        # Период: ноябрь 2026 — должна быть только одна вакансия.
        rows, total = self.logic.period_statistics("2026-11-01", "2026-11-30")
        counts = {s: c for s, c, _ in rows}
        self.assertEqual(total, 1)
        self.assertEqual(counts["Отклик"], 1)
        self.assertEqual(counts["Отказ"], 0)

    def test_corrupted_file_restored_from_backup(self):
        """Проверка: восстановление из бэкапа при повреждении файла."""
        self.logic.add_vacancy(dict(VALID_FORM))
        # Имитируем повреждение: записываем битый JSON.
        with open(self.dal.file_path, "w", encoding="utf-8") as fh:
            fh.write("{битый json")
        # Новый DAL должен восстановить из бэкапа.
        dal2 = DataAccessLayer(data_dir=self.tmp.name)
        self.assertEqual(len(dal2.data["vacancies"]), 1)
        self.assertEqual(dal2.data["vacancies"][0]["position"], "Разработчик")

    def test_backup_rotation(self):
        """Проверка: ротация бэкапов — не более 5 копий."""
        for i in range(8):
            self.logic.add_note(self.logic.add_vacancy(dict(VALID_FORM))["id"],
                                "2026-11-01", "заметка %d" % i)
        self.assertLessEqual(len(self.dal._backup_paths()), 5)

    def test_reminders_panel_order(self):
        """Проверка: напоминания сортируются по категориям и дате."""
        from datetime import date
        today = date(2026, 11, 1)
        self.logic.add_vacancy(dict(VALID_FORM))  # напоминание 2026-11-10
        self.logic.add_vacancy(dict(VALID_FORM, event_date="2026-10-25",
                                    event_text="Просроченный дедлайн"))
        rows = self.logic.reminders_panel(today=today)
        self.assertEqual(rows[0][0], "Просрочено")
        self.assertEqual(rows[1][0], "Скоро")

    def test_export_csv_and_json(self):
        """Проверка: экспорт в CSV и JSON создаёт корректные файлы."""
        self.logic.add_vacancy(dict(VALID_FORM))
        csv_path = os.path.join(self.tmp.name, "out.csv")
        json_path = os.path.join(self.tmp.name, "out.json")
        self.logic.export(csv_path, "csv")
        self.logic.export(json_path, "json")
        with open(csv_path, encoding="utf-8-sig") as fh:
            self.assertIn("Вакансии", fh.read())
        import json as jsonlib
        with open(json_path, encoding="utf-8") as fh:
            data = jsonlib.load(fh)
        self.assertEqual(len(data["vacancies"]), 1)
        self.assertIn("statistics", data)


if __name__ == "__main__":
    unittest.main()
