"""Модуль доступа к данным (DataAccessLayer).

Этот модуль отвечает ЗА СЛОЙ ДАННЫХ. Он:
1. Читает и записывает JSON-файл (data/jobs.json);
2. Валидирует данные при загрузке;
3. Делает атомарную запись (через временный файл);
4. Создаёт резервные копии с ротацией.

Почему JSON, а не база данных?
- Для учебного проекта это проще;
- Один файл = легко открыть, посмотреть, скопировать;
- Но архитектура позволяет заменить DAL на SQLite/PostgreSQL,
  не меняя бизнес-логику (logic.py) и UI (main.py).

Атомарная запись:
  1. Записываем данные во временный файл (jobs.json.tmp);
  2. fsync — гарантируем, что данные записаны на диск;
  3. os.replace — мгновенная замена (атомарная операция на уровне ОС).
  Если программа упадёт на шаге 1 — основной файл не повреждён.

Резервные копии:
  - После каждой успешной записи создаётся копия в data/backups/;
  - Хранятся последние 5 копий (старые удаляются);
  - При повреждении jobs.json — автоматически восстанавливаем из бэкапа.
"""

import json
import os
import shutil
from datetime import datetime

# Значения статусов дублируются здесь для валидации на уровне данных.
# Это "последний рубеж" — даже если логика ошибётся, данные не будут
# испорчены.
STATUS_VALUES = ("Отклик", "Тестовое", "Собеседование", "Оффер", "Отказ")
KEEP_BACKUPS = 5  # Максимальное количество резервных копий.


class DataAccessError(Exception):
    """Ошибка доступа к файлу данных (файл не найден, нет прав и т.д.)."""


class ValidationError(Exception):
    """
    Данные не прошли проверку целостности.

    Поднимается в двух случаях:
    1. При загрузке — если JSON-файл повреждён или имеет неверную структуру;
    2. При валидации формы — если пользователь ввёл некорректные данные.

    Наследуется от Exception, но в main.py перехватывается как ValidationError
    для показа предупреждений пользователю.
    """


def _require_str(value, field):
    """
    Проверяет, что значение — строка.

    Вспомогательная функция для validate_data.
    Если значение None или другой тип — бросает ValidationError.

    Аргументы:
        value: проверяемое значение;
        field: имя поля для сообщения об ошибке.
    """
    if not isinstance(value, str):
        raise ValidationError("Поле «%s» должно быть строкой." % field)


def _valid_date(value, field):
    """
    Проверяет, что значение — строка в формате ISO (YYYY-MM-DD).

    Аргументы:
        value: проверяемое значение;
        field: имя поля для сообщения об ошибке.
    """
    _require_str(value, field)
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValidationError(
            "Поле «%s» должно содержать дату в формате ГГГГ-ММ-ДД." % field
        )


def validate_data(data):
    """
    Полная проверка структуры и типов данных JSON-файла.

    Вызывается при загрузке файла, чтобы убедиться, что данные
    не повреждены. Если проверка не проходит — данные восстанавливаются
    из резервной копии.

    Проверяет:
    1. Корневой элемент — словарь;
    2. Разделы vacancies, notes, reminders — списки;
    3. next_id — словарь с целочисленными счётчиками;
    4. Каждая вакансия: id, обязательные поля, статус, дата;
    5. Каждая заметка: id, vacancy_id, дата, текст;
    6. Каждое напоминание: id, vacancy_id, дата, событие.

    Исключения:
        ValidationError — при любом нарушении структуры.
    """
    # 1. Корневой элемент — словарь.
    if not isinstance(data, dict):
        raise ValidationError("Корневой элемент файла данных должен быть объектом.")
    # 2. Все разделы — списки.
    for section in ("vacancies", "notes", "reminders"):
        if not isinstance(data.get(section), list):
            raise ValidationError("Раздел «%s» должен быть списком." % section)
    # 3. next_id — словарь с положительными целыми числами.
    next_id = data.get("next_id")
    if not isinstance(next_id, dict):
        raise ValidationError("Раздел «next_id» должен быть объектом.")
    for key in ("vacancies", "notes", "reminders"):
        if not isinstance(next_id.get(key), int) or next_id[key] < 1:
            raise ValidationError(
                "Счётчик идентификаторов «%s» некорректен." % key
            )

    # 4. Валидация каждой вакансии.
    for v in data["vacancies"]:
        if not isinstance(v, dict):
            raise ValidationError("Вакансия должна быть объектом.")
        if not isinstance(v.get("id"), int) or v["id"] < 1:
            raise ValidationError("У вакансии должен быть целочисленный идентификатор.")
        for field in ("company", "position", "salary", "source", "link",
                      "status", "response_date"):
            _require_str(v.get(field, ""), field)
        if not v["position"].strip():
            raise ValidationError("Поле «position» обязательно.")
        if v["status"] not in STATUS_VALUES:
            raise ValidationError("Неизвестный статус вакансии: %r." % v["status"])
        _valid_date(v["response_date"], "response_date")

    # 5. Валидация каждой заметки.
    for n in data["notes"]:
        if not isinstance(n, dict):
            raise ValidationError("Заметка должна быть объектом.")
        if not isinstance(n.get("id"), int) or not isinstance(n.get("vacancy_id"), int):
            raise ValidationError("У заметки должны быть целочисленные id и vacancy_id.")
        _valid_date(n.get("date", ""), "date")
        _require_str(n.get("text", ""), "text")
        if not n["text"].strip():
            raise ValidationError("Текст заметки не может быть пустым.")

    # 6. Валидация каждого напоминания.
    for r in data["reminders"]:
        if not isinstance(r, dict):
            raise ValidationError("Напоминание должно быть объектом.")
        if not isinstance(r.get("id"), int) or not isinstance(r.get("vacancy_id"), int):
            raise ValidationError("У напоминания должны быть целочисленные id и vacancy_id.")
        _valid_date(r.get("date", ""), "date")
        _require_str(r.get("event", ""), "event")
        if not r["event"].strip():
            raise ValidationError("Описание события не может быть пустым.")


class DataAccessLayer:
    """
    Слой доступа к данным: чтение, атомарная запись, резервные копии.

    Это класс-обёртка вокруг JSON-файла. Он предоставляет простой
    интерфейс (vacancies(), add_vacancy(), update_vacancy()...) без
    знания о том, что данные хранятся в JSON.

    Если бы мы захотели перейти на SQLite, нужно было бы изменить
    ТОЛЬКО этот класс. logic.py и main.py остались бы без изменений.

    Конструктор:
        data_dir: путь к директории с данными (по умолчанию data/ рядом с main.py).
    """

    def __init__(self, data_dir=None):
        if data_dir is None:
            # Определяем корень проекта относительно этого файла.
            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            data_dir = os.path.join(project_root, "data")
        self.data_dir = data_dir
        self.backups_dir = os.path.join(data_dir, "backups")
        self.file_path = os.path.join(data_dir, "jobs.json")
        # Создаём директорию для бэкапов, если нет.
        os.makedirs(self.backups_dir, exist_ok=True)
        # Загружаем данные при создании слоя.
        self.data = self.load()

    # ------------------------------------------------------------------
    # Чтение и восстановление
    # ------------------------------------------------------------------

    def _backup_paths(self):
        """
        Возвращает пути ко всем резервным копиям, от свежих к старым.

        Сортировка по дате модификации (os.path.getmtime) — файлы
        бэкапов именуются jobs_YYYYMMDD_HHMMSS.json, так что сортировка
        по имени дала бы тот же результат, но mtime надёжнее.
        """
        if not os.path.isdir(self.backups_dir):
            return []
        names = [n for n in os.listdir(self.backups_dir)
                 if n.startswith("jobs_") and n.endswith(".json")]
        paths = [os.path.join(self.backups_dir, n) for n in names]
        paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return paths

    def _empty_data(self):
        """
        Возвращает структуру данных для нового (пустого) файла.

        next_id — счётчики для автоматического присвоения ID.
        Начинаются с 1, увеличиваются при каждом добавлении.
        """
        return {
            "vacancies": [],
            "notes": [],
            "reminders": [],
            "next_id": {"vacancies": 1, "notes": 1, "reminders": 1},
        }

    def load(self):
        """
        Загружает данные с автоматическим восстановлением из бэкапа.

        Алгоритм:
        1. Пробуем прочитать jobs.json;
        2. Если файл повреждён — пробуем бэкапы (от свежих к старым);
        3. Если восстановили из бэкапа — копируем его в jobs.json;
        4. Если ни один файл не найден — создаём пустую базу.

        Это гарантирует, что приложение всегда работает с корректными данными.
        """
        candidates = []
        if os.path.exists(self.file_path):
            candidates.append(self.file_path)
        candidates.extend(self._backup_paths())

        for path in candidates:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                # Проверяем структуру данных.
                validate_data(data)
                if path != self.file_path:
                    # Восстановили из бэкапа — копируем в основной файл.
                    shutil.copyfile(path, self.file_path)
                return data
            except (OSError, ValueError, ValidationError):
                # Пробуем следующий кандидат.
                continue

        # Нет ни одного файла — начинаем с пустой базы.
        self.data = self._empty_data()
        self.save()
        return self.data

    # ------------------------------------------------------------------
    # Атомарная запись и резервные копии
    # ------------------------------------------------------------------

    def save(self):
        """
        Сохраняет данные: атомарная запись + бэкап + ротация бэкапов.

        Порядок критичен:
        1. Сначала атомарно записываем данные;
        2. Затем создаём бэкап (чтобы бэкап был той же версии);
        3. Затем удаляем старые бэкапы.
        """
        self._atomic_write()
        self._make_backup()
        self._prune_backups()

    def _atomic_write(self):
        """
        Атомарная запись JSON-файла.

        Почему не просто open(path, 'w').write(...)?
        Если программа упадёт во время записи (например, отключение питания),
        основной файл будет повреждён (половина данных записано).

        Решение:
        1. Записываем во временный файл (.tmp);
        2. fsync — ОС гарантирует, что данные записаны на диск;
        3. os.replace — мгновенная замена (атомарная операция ядра).

        Если программа упадёт на шаге 1 — основной файл не тронут.
        Если на шаге 3 — либо старый файл, либо новый (нет половины).
        """
        tmp_path = self.file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())  # Данные физически записаны на диск.
        os.replace(tmp_path, self.file_path)  # Атомарная замена.

    def _make_backup(self):
        """
        Создаёт резервную копию текущего файла данных.

        Имя файла: jobs_YYYYMMDD_HHMMSS_ffffff.json
        (включая микросекунды для уникальности даже при частых записях).
        """
        if not os.path.exists(self.file_path):
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        dst = os.path.join(self.backups_dir, "jobs_%s.json" % stamp)
        try:
            shutil.copyfile(self.file_path, dst)
        except OSError:
            pass  # отсутствие бэкапа не должно блокировать запись

    def _prune_backups(self, keep=KEEP_BACKUPS):
        """
        Удаляет старые бэкапы, оставляя только keep последних.

        Это предотвращает разрастание директории backups/.
        """
        for path in self._backup_paths()[keep:]:
            try:
                os.remove(path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # Генератор идентификаторов
    # ------------------------------------------------------------------

    def next_id(self, section):
        """
        Возвращает следующий свободный ID и увеличивает счётчик.

        section: "vacancies", "notes" или "reminders".

        Почему не UUID?
        Для учебного проекта целые числа проще для понимания.
        В реальном проекте лучше использовать UUID или SERIAL (PostgreSQL).
        """
        value = self.data["next_id"].get(section, 1)
        self.data["next_id"][section] = value + 1
        return value

    # ------------------------------------------------------------------
    # CRUD: Вакансии
    # ------------------------------------------------------------------

    def vacancies(self):
        """
        Возвращает копию списка всех вакансий.

        dict(v) — создаём поверхностную копию каждой вакансии,
        чтобы внешний код не мог изменить данные напрямую,
        минуя save().
        """
        return [dict(v) for v in self.data["vacancies"]]

    def vacancy_by_id(self, vacancy_id):
        """Находит вакансию по ID и возвращает копию или None."""
        for v in self.data["vacancies"]:
            if v["id"] == vacancy_id:
                return dict(v)
        return None

    def add_vacancy(self, fields):
        """
        Добавляет вакансию и сохраняет данные.

        fields: словарь с ключами из VACANCY_FIELDS.

        Возвращает:
            созданную вакансию с присвоенным ID.
        """
        vacancy = dict(fields)
        vacancy["id"] = self.next_id("vacancies")  # Присваиваем уникальный ID.
        self.data["vacancies"].append(vacancy)
        self.save()  # Атомарная запись + бэкап.
        return dict(vacancy)

    def update_vacancy(self, vacancy_id, fields):
        """
        Обновляет поля существующей вакансии.

        fields — только изменённые поля (не вся вакансия).
        dict.update() применяет только переданные ключи.
        """
        for v in self.data["vacancies"]:
            if v["id"] == vacancy_id:
                v.update(fields)
                self.save()
                return dict(v)
        raise DataAccessError("Вакансия %s не найдена." % vacancy_id)

    def delete_vacancy(self, vacancy_id):
        """
        Удаляет вакансию КАСКАДНО: вакансия + заметки + напоминания.

        Это обеспечивает целостность данных:
        нельзя оставить заметку без вакансии или напоминание без вакансии.
        """
        # Удаляем вакансию.
        self.data["vacancies"] = [v for v in self.data["vacancies"]
                                  if v["id"] != vacancy_id]
        # Удаляем связанные заметки (cascade delete).
        self.data["notes"] = [n for n in self.data["notes"]
                              if n["vacancy_id"] != vacancy_id]
        # Удаляем связанные напоминания.
        self.data["reminders"] = [r for r in self.data["reminders"]
                                  if r["vacancy_id"] != vacancy_id]
        self.save()

    # ------------------------------------------------------------------
    # CRUD: Заметки
    # ------------------------------------------------------------------

    def notes(self, vacancy_id=None):
        """
        Возвращает заметки. Если vacancy_id указан — только для этой вакансии.

        vacancy_id=None — все заметки (для тестов и отладки).
        vacancy_id=42 — заметки только вакансии с ID 42.
        """
        return [dict(n) for n in self.data["notes"]
                if vacancy_id is None or n["vacancy_id"] == vacancy_id]

    def add_note(self, vacancy_id, date_str, text):
        """
        Добавляет заметку к вакансии.

        Аргументы:
            vacancy_id: ID вакансии, к которой относится заметка;
            date_str: дата в формате ISO;
            text: текст заметки.
        """
        note = {"id": self.next_id("notes"), "vacancy_id": vacancy_id,
                "date": date_str, "text": text}
        self.data["notes"].append(note)
        self.save()
        return dict(note)

    def delete_note(self, note_id):
        """Удаляет заметку по ID."""
        self.data["notes"] = [n for n in self.data["notes"] if n["id"] != note_id]
        self.save()

    # ------------------------------------------------------------------
    # CRUD: Напоминания
    # ------------------------------------------------------------------

    def reminders(self, vacancy_id=None):
        """
        Возвращает напоминания. Если vacancy_id указан — только для этой вакансии.
        """
        return [dict(r) for r in self.data["reminders"]
                if vacancy_id is None or r["vacancy_id"] == vacancy_id]

    def set_reminders(self, vacancy_id, items):
        """
        Полностью заменяет напоминания вакансии.

        items: список кортежей [(дата, событие), ...].

        Сначала удаляем все старые напоминания вакансии,
        затем создаём новые из переданного списка.
        Это проще, чем искать изменения по одному.
        """
        self.data["reminders"] = [r for r in self.data["reminders"]
                                  if r["vacancy_id"] != vacancy_id]
        for date_str, event in items:
            self.data["reminders"].append({
                "id": self.next_id("reminders"),
                "vacancy_id": vacancy_id,
                "date": date_str,
                "event": event,
            })
        self.save()
