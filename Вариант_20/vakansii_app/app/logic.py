"""Модуль бизнес-логики приложения (ApplicationLogic).

Этот модуль НЕ зависит от PyQt и графического интерфейса.
Он работает ТОЛЬКО со слоем доступа к данным (DataAccessLayer).

Почему это важно:
- Логику можно тестировать без запуска GUI (см. tests/test_logic.py);
- Логику можно переиспользовать в другом проекте (веб-интерфейс, API);
- Изменение UI не ломает бизнес-правила и наоборот.

Архитектура:
  UI (main.py) --> ApplicationLogic (logic.py) --> DataAccessLayer (data_access.py)
  <-- тесты (test_logic.py) работают ТОЛЬКО с logic + DAL
"""

import csv
import json
import os
from datetime import datetime, date

from .data_access import DataAccessLayer, ValidationError

# Константы допустимых статусов — единый источник истины.
# И UI (ComboBox), и логика (валидация), и DAL (проверка) используют
# одни и те же значения. Если нужно добавить статус — меняем здесь.
STATUSES = ("Отклик", "Тестовое", "Собеседование", "Оффер", "Отказ")

# Обязательные поля вакансии. Используется для сбора данных из формы.
VACANCY_FIELDS = ("company", "position", "salary", "source", "link",
                  "status", "response_date")


def parse_iso_date(value, field):
    """
    Парсит строку даты в формате ISO ("YYYY-MM-DD") в объект date.

    Используется для валидации дат из форм и данных.

    Аргументы:
        value: строка с датой (например, "2026-11-09")
        field: имя поля для сообщения об ошибке

    Возвращает:
        объект datetime.date

    Исключения:
        ValidationError — если значение не строка или некорректный формат.
    """
    if not isinstance(value, str):
        raise ValidationError("Поле «%s» должно быть датой." % field)
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError("Поле «%s» содержит некорректную дату: %r." % (field, value))


class ApplicationLogic:
    """
    Бизнес-логика трекера вакансий.

    Этот класс — "мозг" приложения. Он знает правила работы с данными,
    но НЕ знает о виджетах PyQt.

    Конструктор принимает DataAccessLayer (DAL) — объект, который умеет
    читать/записывать JSON-файл. Это позволяет:
    - В продакшене: DAL пишет в SQLite/PostgreSQL;
    - В тестах: DAL работает с tempfile (временной директорией);
    - В текущем проекте: DAL работает с JSON-файлом.

    Принцип: "Инверсия управления через зависимости" (Dependency Inversion).
    """

    def __init__(self, dal):
        self.dal = dal  # Слой доступа к данным — вводится через конструктор.

    # ------------------------------------------------------------------
    # Операции с вакансиями
    # ------------------------------------------------------------------

    def vacancies(self):
        """
        Возвращает ВСЕ вакансии, отсортированные по убыванию даты отклика.

        reverse=True = самые свежие даты первыми.
        При одинаковой дате — сортировка по ID (самые новые первыми).
        """
        return sorted(self.dal.vacancies(),
                      key=lambda v: (v["response_date"], v["id"]), reverse=True)

    def vacancy_by_id(self, vacancy_id):
        """Находит одну вакансию по ID или возвращает None."""
        return self.dal.vacancy_by_id(vacancy_id)

    def validate_vacancy_form(self, form):
        """
        Проверяет данные формы перед сохранением.

        Валидация здесь, а не в UI, потому что:
        1. Данные могут прийти откуда угодно (форма, API, импорт);
        2. UI должен показывать ошибки, но правила — в логике.

        Правила:
        - position не может быть пустым;
        - status должен быть из STATUSES;
        - response_date — корректный ISO-формат;
        - если есть event_text, то event_date тоже обязателен;
        - все поля — строки.
        """
        if not (form.get("position") or "").strip():
            raise ValidationError("Укажите позицию (должность).")
        if form.get("status") not in STATUSES:
            raise ValidationError("Выберите корректный статус.")
        parse_iso_date(form.get("response_date"), "Дата отклика")
        # Напоминание: если есть текст события — должна быть дата.
        if (form.get("event_text") or "").strip():
            parse_iso_date(form.get("event_date"), "Дата напоминания")
        for field in VACANCY_FIELDS:
            value = form.get(field)
            if value is not None and not isinstance(value, str):
                raise ValidationError("Поле «%s» должно быть строкой." % field)

    def add_vacancy(self, form):
        """
        Добавляет новую вакансию.

        Порядок:
        1. Валидация данных;
        2. Формируем словарь полей из константы VACANCY_FIELDS;
        3. DAL создаёт запись и присваивает ID;
        4. Синхронизируем напоминание (если пользователь указал событие);
        5. Сохраняем и возвращаем созданную вакансию.
        """
        self.validate_vacancy_form(form)
        fields = {key: form.get(key, "") for key in VACANCY_FIELDS}
        vacancy = self.dal.add_vacancy(fields)
        self._sync_reminder(vacancy["id"], form)
        return vacancy

    def update_vacancy(self, vacancy_id, form):
        """
        Обновляет существующую вакансию.

        Аналогично add_vacancy, но с обновлением записи по ID.
        """
        self.validate_vacancy_form(form)
        fields = {key: form.get(key, "") for key in VACANCY_FIELDS}
        vacancy = self.dal.update_vacancy(vacancy_id, fields)
        self._sync_reminder(vacancy_id, form)
        return vacancy

    def delete_vacancy(self, vacancy_id):
        """
        Удаляет вакансию.

        Каскадное удаление: DAL автоматически удаляет заметки и напоминания
        этой вакансии (цепочечное удаление через foreign key).
        """
        self.dal.delete_vacancy(vacancy_id)

    def _sync_reminder(self, vacancy_id, form):
        """
        Синхронизирует напоминание вакансии с данными формы.

        Если пользователь указал event_text — обновляем напоминание.
        Если event_text пустой — удаляем напоминание (его не было).

        Это приватный метод (префикс _), он не вызывается из UI.
        """
        text = (form.get("event_text") or "").strip()
        if text:
            self.dal.set_reminders(vacancy_id, [(form["event_date"], text)])
        else:
            self.dal.set_reminders(vacancy_id, [])

    # ------------------------------------------------------------------
    # Заметки
    # ------------------------------------------------------------------

    def notes_for(self, vacancy_id):
        """
        Возвращает заметки вакансии, отсортированные по дате.

        Заметки привязаны к вакансии (vacancy_id) и хранятся как
        независимые записи с датой. Это позволяет вести журнал
        изменений по этапам.
        """
        return sorted(self.dal.notes(vacancy_id),
                      key=lambda n: (n["date"], n["id"]))

    def add_note(self, vacancy_id, date_str, text):
        """
        Добавляет заметку к вакансии.

        Правила:
        - текст не может быть пустым;
        - дата должна быть в формате ISO.
        """
        text = (text or "").strip()
        if not text:
            raise ValidationError("Текст заметки не может быть пустым.")
        parse_iso_date(date_str, "Дата заметки")
        return self.dal.add_note(vacancy_id, date_str, text)

    def delete_note(self, note_id):
        """Удаляет заметку по ID."""
        self.dal.delete_note(note_id)

    # ------------------------------------------------------------------
    # Напоминания
    # ------------------------------------------------------------------

    def reminders_for(self, vacancy_id):
        """
        Напоминания конкретной вакансии (для предзаполнения формы).

        Вызывается из JobDialog при редактировании вакансии,
        чтобы показать текущее напоминание.
        """
        return sorted(self.dal.reminders(vacancy_id), key=lambda r: r["date"])

    def reminders_panel(self, days=14, today=None):
        """
        Формирует список напоминаний для панели главного окна.

        Каждое напоминание категоризируется:
        - "Просрочено" — дата события < сегодня;
        - "Скоро" — дата в пределах next days дней;
        - "Планируется" — дата дальше.

        Аргументы:
            days: порог "скоро" (по умолчанию 14 дней);
            today: тестовый аргумент для юнит-тестов (если None — date.today()).

        Возвращает:
            список кортежей: (категория, datetime.date, текст)
        """
        today = today or date.today()
        rows = []
        for r in self.dal.reminders():
            event_date = datetime.strptime(r["date"], "%Y-%m-%d").date()
            # Добавляем название компании и позиции к тексту события.
            vacancy = self.dal.vacancy_by_id(r["vacancy_id"])
            who = "%s — %s: " % (vacancy["company"], vacancy["position"]) if vacancy else ""
            text = who + r["event"]
            if event_date < today:
                rows.append(("Просрочено", event_date, text))
            elif (event_date - today).days <= days:
                rows.append(("Скоро", event_date, text))
            else:
                rows.append(("Планируется", event_date, text))
        # Сортировка: сначала просроченные, потом скоро, потом планы.
        # Внутри каждой категории — по дате.
        order = {"Просрочено": 0, "Скоро": 1, "Планируется": 2}
        rows.sort(key=lambda x: (order[x[0]], x[1]))
        return rows

    # ------------------------------------------------------------------
    # Статистика
    # ------------------------------------------------------------------

    def status_counts(self):
        """
        Подсчитывает количество вакансий по статусам.

        Возвращает:
            (rows, total) — rows: список (статус, количество, доля%),
            total: общее число вакансий.

        Пример результата:
            [("Отклик", 5, 50.0), ("Собеседование", 3, 30.0),
             ("Оффер", 1, 10.0), ("Отказ", 1, 10.0), ("Тестовое", 0, 0.0)], 10
        """
        # Инициализируем счётчики для ВСЕХ статусов (включая нулевые).
        counts = {s: 0 for s in STATUSES}
        for v in self.dal.vacancies():
            counts[v["status"]] = counts.get(v["status"], 0) + 1
        total = sum(counts.values())
        # Вычисляем долю в процентах.
        rows = [(s, c, (c / total * 100.0 if total else 0.0))
                for s, c in counts.items()]
        return rows, total

    def period_statistics(self, date_from, date_to):
        """
        Подсчитывает статистику вакансий за выбранный период.

        Фильтрация идёт по дате отклика (response_date).

        Аргументы:
            date_from: "YYYY-MM-DD" — начало периода;
            date_to: "YYYY-MM-DD" — конец периода.

        Исключения:
            ValidationError — если начало позже конца.
        """
        start = parse_iso_date(date_from, "Начало периода")
        end = parse_iso_date(date_to, "Конец периода")
        if start > end:
            raise ValidationError("Начало периода не может быть позже его конца.")
        counts = {s: 0 for s in STATUSES}
        for v in self.dal.vacancies():
            d = datetime.strptime(v["response_date"], "%Y-%m-%d").date()
            if start <= d <= end:
                counts[v["status"]] = counts.get(v["status"], 0) + 1
        total = sum(counts.values())
        rows = [(s, c, (c / total * 100.0 if total else 0.0))
                for s, c in counts.items()]
        return rows, total

    # ------------------------------------------------------------------
    # Экспорт
    # ------------------------------------------------------------------

    def export(self, path, fmt, include_vacancies=True, include_stats=True,
               date_from=None, date_to=None):
        """
        Выгружает данные в CSV или JSON.

        Форматы:
        - CSV: разделитель — точка с запятой (для Excel в русской локали),
          кодировка utf-8-sig (BOM, чтобы Excel корректно показывал кириллицу);
        - JSON: человекочитаемый, с отступами, без BOM.

        Аргументы:
            path: полный путь к файлу;
            fmt: "csv" или "json";
            include_vacancies: включать ли список вакансий;
            include_stats: включать ли статистику;
            date_from, date_to: диапазон для периодической статистики.

        Возвращает:
            path — подтверждение пути к созданному файлу.

        Исключения:
            ValidationError — при некорректных аргументах.
        """
        if fmt not in ("csv", "json"):
            raise ValidationError("Неизвестный формат экспорта: %r." % fmt)
        if not include_vacancies and not include_stats:
            raise ValidationError("Выберите хотя бы один блок для выгрузки.")

        stats_rows, stats_total = self.status_counts()
        period_rows = None
        if date_from and date_to:
            period_rows, _ = self.period_statistics(date_from, date_to)

        # Создаём директорию, если её нет.
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)

        if fmt == "json":
            # ---------- JSON-экспорт ----------
            payload = {}
            if include_vacancies:
                payload["vacancies"] = self.vacancies()
            if include_stats:
                payload["statistics"] = {
                    "total": stats_total,
                    "by_status": [
                        {"status": s, "count": c, "share_percent": round(p, 1)}
                        for s, c, p in stats_rows
                    ],
                }
                if period_rows is not None:
                    payload["statistics"]["period"] = {
                        "from": date_from,
                        "to": date_to,
                        "by_status": [
                            {"status": s, "count": c, "share_percent": round(p, 1)}
                            for s, c, p in period_rows
                        ],
                    }
            # ensure_ascii=False — сохраняет кириллицу как есть, не \u0432.
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
        else:
            # ---------- CSV-экспорт ----------
            # utf-8-sig = UTF-8 + BOM. Excel на Windows читает BOM
            # и понимает, что файл в UTF-8 (иначе будет кракозябра).
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.writer(fh, delimiter=";")
                if include_vacancies:
                    writer.writerow(["Вакансии"])
                    writer.writerow(["ID", "Компания", "Позиция", "Вилка зарплаты",
                                     "Статус", "Источник", "Ссылка", "Дата отклика"])
                    for v in self.vacancies():
                        writer.writerow([v["id"], v["company"], v["position"],
                                         v["salary"], v["status"], v["source"],
                                         v["link"], v["response_date"]])
                    writer.writerow([])
                    writer.writerow([])
                if include_stats:
                    writer.writerow(["Статистика по статусам"])
                    writer.writerow(["Статус", "Количество", "Доля, %"])
                    for s, c, p in stats_rows:
                        # Заменяем точку на запятую для русской локали.
                        writer.writerow([s, c, ("%.1f" % p).replace(".", ",")])
                    writer.writerow(["Итого", stats_total, "100,0"])
                    if period_rows is not None:
                        writer.writerow([])
                        writer.writerow(["Статистика за период %s — %s" % (date_from, date_to)])
                        writer.writerow(["Статус", "Количество", "Доля, %"])
                        for s, c, p in period_rows:
                            writer.writerow([s, c, ("%.1f" % p).replace(".", ",")])
        return path
