"""Приложение «Вакансии» — точка входа и графический интерфейс.

Интерфейс собирается ДИНАМИЧЕСКИ из .ui-файлов Qt Designer (ui/*.ui)
с помощью PyQt6.uic.loadUi — компиляция в .py не используется.

Слои:
* UI (этот модуль) — окна и обработчики;
* ApplicationLogic (app.logic) — бизнес-логика;
* DataAccessLayer (app.data_access) — JSON-хранилище.

Запуск: python main.py
"""

import os
import sys
import traceback

from PyQt6 import uic
from PyQt6.QtCore import Qt, QDate
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QDialog, QMessageBox, QTableWidgetItem,
    QListWidgetItem, QInputDialog, QFileDialog, QHeaderView, QAbstractItemView,
)

from app.data_access import DataAccessLayer, ValidationError
from app.logic import ApplicationLogic, STATUSES

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(BASE_DIR, "ui")       # здесь лежат .ui-файлы от Qt Designer
EXPORT_DIR = os.path.join(BASE_DIR, "export")  # сюда экспортируются CSV/JSON

# ---------------------------------------------------------------------------
# QSS — Qt Style Sheets (аналог CSS для PyQt)
# ---------------------------------------------------------------------------
# QSS позволяет стилизовать все виджеты приложения в одном месте.
# Синтаксис похож на CSS: селекторы, свойства, псевдоклассы (:hover, :pressed).
# Это заменяет сложный Qt StyleSheet и делает код чище.
STYLE = """
* { font-family: 'Segoe UI', 'Ubuntu', 'Arial', sans-serif; }
QMainWindow, QDialog { background: #f4f6fb; }
QLabel { color: #33415e; }
QLabel#lblTitle { font-size: 21px; font-weight: 700; color: #1e2a44; }
QLabel#lblHeader { font-size: 17px; font-weight: 700; color: #1e2a44; }
QGroupBox {
    background: #ffffff;
    border: 1px solid #e3e8f4;
    border-radius: 10px;
    margin-top: 14px;
    padding: 10px;
    font-weight: 600;
    color: #33415e;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: #3b6df0;
}
QTableWidget, QListWidget {
    background: #ffffff;
    border: 1px solid #e6eaf5;
    border-radius: 8px;
    alternate-background-color: #f4f7ff;
    selection-background-color: #3b6df0;
    selection-color: #ffffff;
}
QHeaderView::section {
    background: #eef2fb;
    color: #33415e;
    border: none;
    border-bottom: 1px solid #dde3f0;
    padding: 6px;
    font-weight: 600;
}
QListWidget::item { padding: 5px; }
QPushButton {
    background: #3b6df0;
    color: #ffffff;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 600;
}
QPushButton:hover { background: #2f5bd0; }
QPushButton:pressed { background: #264bb0; }
QPushButton:disabled { background: #cdd4e6; color: #8a93a8; }
QPushButton#btnDelete, QPushButton#btnNoteDel { background: #e5484d; }
QPushButton#btnDelete:hover, QPushButton#btnNoteDel:hover { background: #c93a3f; }
QPushButton#btnCancel, QPushButton#btnShowAll, QPushButton#btnBrowse {
    background: #eef2fb;
    color: #33415e;
    border: 1px solid #d5dcef;
}
QLineEdit, QPlainTextEdit, QComboBox, QDateEdit, QSpinBox {
    background: #ffffff;
    border: 1px solid #d5dcef;
    border-radius: 8px;
    padding: 6px 8px;
    color: #1e2a44;
}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus { border: 1px solid #3b6df0; }
QStatusBar { color: #5a6580; }
"""


def load_ui(widget, file_name):
    """
    Утилита для динамической загрузки интерфейса из .ui-файла.

    Как это работает:
    1. Qt Designer сохраняет визуальный макет в XML-файл (.ui).
    2. PyQt6.uic.loadUi() читает этот XML и создаёт виджеты "на лету".
    3. Мы передаём уже созданный экземпляр класса (self) и имя файла.
       loadUi находит в .ui-файле виджеты по их objectName и присваивает
       их атрибутам self (например, self.tableJobs = QTableWidget(...)).

    Почему не pyuic6?
    pyuic6 компилирует .ui в .py-файл. Но при каждом изменении .ui
    нужно заново запускать компиляцию. loadUi avoids этого — файл .ui
    читается каждый раз при запуске, что удобнее для разработки.

    Аргументы:
        widget: экземпляр QMainWindow или QDialog — в него "встроится" UI
        file_name: имя файла в директории ui/ (без полного пути)
    """
    uic.loadUi(os.path.join(UI_DIR, file_name), widget)


def qd_to_iso(qdate):
    """
    Преобразование QDate (Qt-дата) в строку ISO ("yyyy-MM-dd").

    Внутреннее хранилище использует формат ISO (ГГГГ-ММ-ДД), потому что:
    - это стандартный формат, легко сортируется как строка;
    - легко парсится через datetime.strptime.

    Аргументы:
        qdate: экземпляр QDate из виджета dateResponse / dateEvent
    Возвращает:
        строку вида "2026-11-09"
    """
    return qdate.toString("yyyy-MM-dd")


def iso_to_ru(iso_str):
    """
    Преобразование строки ISO ("yyyy-MM-dd") в читаемый формат ("dd.MM.yyyy").

    Используется только для ОТОБРАЖЕНИЯ данных в таблице.
    Внутри приложения даты всегда хранятся в формате ISO.

    Аргументы:
        iso_str: строка вида "2026-11-09"
    Возвращает:
        строку вида "09.11.2026"
    """
    return QDate.fromString(iso_str, "yyyy-MM-dd").toString("dd.MM.yyyy")


# ----------------------------------------------------------------------
# Окно статистики (StatsDialog)
# ----------------------------------------------------------------------
# Этот класс — отдельное диалоговое окно, которое показывает:
# 1. Общую статистику по статусам всех вакансий;
# 2. Статистику за выбранный пользователем период дат.
#
# QDialog — модальное или немодальное окно. Здесь используется модальное
# (exec()), то есть пользователь не может работать с главным окном,
# пока не закроет статистику.
#
# Ключевой приём: self.logic — ссылка на ApplicationLogic.
# UI-слой НЕ работает с данными напрямую. Все вычисления (подсчёт,
# проценты, фильтрация по периоду) делегируются бизнес-логике.
# Это соответствует правилу: "UI знает о логике, логика не знает об UI".

class StatsDialog(QDialog):
    def __init__(self, logic, parent=None):
        super().__init__(parent)
        # Загружаем UI из .ui-файла. После этой строки все виджеты
        # из stats_window.ui доступны как атрибуты self:
        # self.tableStatus, self.dateFrom, self.btnRefresh и т.д.
        load_ui(self, "stats_window.ui")
        self.logic = logic

        # Настройка таблиц: одинаковая для обеих таблиц в окне.
        # Цикл for экономит дублирование кода.
        for table in (self.tableStatus, self.tablePeriod):
            table.setColumnCount(3)
            table.setHorizontalHeaderLabels(["Статус", "Количество", "Доля, %"])
            # Запрещаем редактирование ячеек двойным кликом.
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            # Выделение целой строки при клике на ячейку.
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            # Скрываем номера строк слева (не нужны для статистики).
            table.verticalHeader().setVisible(False)
            # Растягиваем колонки по ширине.
            table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        # Формат отображения дат в виджетах QDateEdit.
        self.dateFrom.setDisplayFormat("dd.MM.yyyy")
        self.dateTo.setDisplayFormat("dd.MM.yyyy")

        # Связываем сигналы кнопок со слотами (обработчиками).
        # clicked — сигнал, который посылается при нажатии кнопки.
        self.btnRefresh.clicked.connect(self.refresh)
        self.btnShowAll.clicked.connect(self.show_all_period)
        self.btnClose.clicked.connect(self.close)

        # При открытии окна сразу показываем статистику за весь период.
        self.show_all_period()

    def show_all_period(self):
        # Устанавливаем диапазон дат от самой ранней до самой поздней вакансии.
        jobs = self.logic.vacancies()
        dates = sorted(j["response_date"] for j in jobs)
        if dates:
            self.dateFrom.setDate(QDate.fromString(dates[0], "yyyy-MM-dd"))
            self.dateTo.setDate(QDate.fromString(dates[-1], "yyyy-MM-dd"))
        else:
            self.dateFrom.setDate(QDate.currentDate().addMonths(-1))
            self.dateTo.setDate(QDate.currentDate())
        self.refresh()

    def refresh(self):
        """
        Пересчитывает и отображает статистику для текущего диапазона дат.

        Этот метод вызывается:
        - при открытии окна (через show_all_period);
        - при нажатии кнопки "Обновить";
        - при изменении диапазона дат.
        """
        # Преобразуем QDate в ISO-строку для передачи в бизнес-логику.
        date_from = qd_to_iso(self.dateFrom.date())
        date_to = qd_to_iso(self.dateTo.date())
        try:
            # Делегируем вычисления бизнес-логике.
            rows, total = self.logic.status_counts()
            period_rows, period_total = self.logic.period_statistics(date_from, date_to)
        except ValidationError as exc:
            QMessageBox.warning(self, "Статистика", str(exc))
            return
        # Заполняем обе таблицы результатами.
        self._fill_table(self.tableStatus, rows, total)
        self._fill_table(self.tablePeriod, period_rows, period_total)
        self.labelTotal.setText(
            "Всего вакансий: %d; в периоде %s — %s: %d"
            % (total, iso_to_ru(date_from), iso_to_ru(date_to), period_total)
        )

    @staticmethod
    def _fill_table(table, rows, total):
        """
        Заполняет одну QTableWidget данными из списка кортежей.

        Каждый кортеж: (статус, количество, доля_в_процентах).
        Метод статический, потому что не использует self — его можно
        переиспользовать в других окнах с таблицами.

        Аргументы:
            table: QTableWidget для заполнения
            rows: список кортежей (status, count, share)
            total: общее количество вакансий (для строки "Итого")
        """
        table.setRowCount(0)  # Очищаем таблицу перед заполнением.
        for row_index, (status, count, share) in enumerate(rows):
            table.insertRow(row_index)
            # QTableWidgetItem — обёртка над значением для отображения в таблице.
            table.setItem(row_index, 0, QTableWidgetItem(status))
            table.setItem(row_index, 1, QTableWidgetItem(str(count)))
            table.setItem(row_index, 2, QTableWidgetItem("%.1f" % share))
        # Добавляем итоговую строку.
        table.insertRow(table.rowCount())
        last = table.rowCount() - 1
        table.setItem(last, 0, QTableWidgetItem("Итого"))
        table.setItem(last, 1, QTableWidgetItem(str(total)))
        table.setItem(last, 2, QTableWidgetItem("100,0"))


# ----------------------------------------------------------------------
# Окно экспорта (ExportDialog)
# ----------------------------------------------------------------------
# Позволяет выгрузить данные в двух форматах:
# - CSV (точка с запятой, кодировка utf-8-sig — открывается в Excel);
# - JSON (человекочитаемый, с отступами).
#
# Пользователь выбирает формат радиокнопкой, указывает путь к файлу
# и отмечает, что экспортировать: вакансии, статистику или оба блока.

class ExportDialog(QDialog):
    def __init__(self, logic, parent=None):
        super().__init__(parent)
        load_ui(self, "export_window.ui")
        self.logic = logic

        self.dateFrom.setDisplayFormat("dd.MM.yyyy")
        self.dateTo.setDisplayFormat("dd.MM.yyyy")

        # Связываем сигналы с обработчиками.
        self.btnBrowse.clicked.connect(self.browse)
        self.btnExport.clicked.connect(self.do_export)
        self.btnCancel.clicked.connect(self.reject)

        # При переключении формата (CSV/JSON) меняем путь по умолчанию.
        # toggled — сигнал, посылаемый при изменении состояния радиокнопки.
        self.rbtCSV.toggled.connect(self._default_path)
        self.rbtJSON.toggled.connect(self._default_path)
        self._default_path()

    def _default_path(self):
        """
        Формирует путь к файлу по умолчанию: export/vakansii_YYYYMMDD_HHMM.ext

        Используется при открытии окна и при переключении формата (CSV/JSON).
        isChecked() проверяет, выбрана ли конкретная радиокнопка.
        """
        from datetime import datetime
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        ext = "csv" if self.rbtCSV.isChecked() else "json"
        self.edtPath.setText(
            os.path.join(EXPORT_DIR, "vakansii_%s.%s" % (stamp, ext))
        )

    def browse(self):
        """
        Открывает диалог выбора файла для сохранения.

        QFileDialog.getSaveFileName — стандартное системное окно.
        flt — фильтр форматов файлов (разделитель ";;").
        Возвращает кортеж (путь, выбранный_фильтр).
        """
        if self.rbtCSV.isChecked():
            flt = "CSV (*.csv);;Все файлы (*)"
        else:
            flt = "JSON (*.json);;Все файлы (*)"
        path, _ = QFileDialog.getSaveFileName(
            self, "Файл экспорта", self.edtPath.text(), flt
        )
        if path:
            self.edtPath.setText(path)

    def do_export(self):
        """
        Вызывает экспорт в бизнес-логике и показывает результат.

        Все параметры (путь, формат, флаги, даты) собираются из UI-виджетов
        и передаются в self.logic.export().
        """
        try:
            path = self.logic.export(
                self.edtPath.text().strip(),
                "csv" if self.rbtCSV.isChecked() else "json",
                include_vacancies=self.chkVacancies.isChecked(),
                include_stats=self.chkStatistics.isChecked(),
                date_from=qd_to_iso(self.dateFrom.date()),
                date_to=qd_to_iso(self.dateTo.date()),
            )
        except ValidationError as exc:
            QMessageBox.warning(self, "Экспорт", str(exc))
            return
        except OSError as exc:
            QMessageBox.critical(self, "Экспорт", "Не удалось записать файл: %s" % exc)
            return
        QMessageBox.information(self, "Готово", "Данные выгружены в файл:\n%s" % path)
        self.accept()


# ----------------------------------------------------------------------
# Главное окно (MainWindow)
# ----------------------------------------------------------------------
# QMainWindow — основное окно приложения с панелью меню, тулбарами и
# статус-баром. В нашем случае интерфейс нарисован в main_window.ui,
# и load_ui автоматически создаёт все виджеты с нужными objectName.
#
# Структура главного окна:
# - QTableWidget (tableJobs) — список вакансий;
# - QListWidget (listReminders) — панель напоминаний;
# - QListWidget (listNotes) — панель заметок выбранной вакансии;
# - QDateEdit (dateNote) — дата для новой заметки;
# - Кнопки: добавить/редактировать/удалить вакансию, заметку, статистику, экспорт.

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Загружаем UI из main_window.ui.
        # После load_ui все виджеты из .ui-файла доступны как self.tableJobs,
        # self.btnAdd, self.cmbFilter и т.д.
        load_ui(self, "main_window.ui")

        # Создаём слой данных и логики.
        # DataAccessLayer автоматически загружает data/jobs.json.
        # ApplicationLogic работает с DAL, не зная о файлах или GUI.
        self.logic = ApplicationLogic(DataAccessLayer())

        # ---------- Настройка фильтра по статусу ----------
        # Заполняем ComboBox: "Все" + все допустимые статусы.
        self.cmbFilter.addItem("Все")
        for status in STATUSES:
            self.cmbFilter.addItem(status)
        # currentIndexChanged — сигнал, посылаемый при смене выбранного элемента.
        self.cmbFilter.currentIndexChanged.connect(self.refresh_jobs)

        # ---------- Настройка таблицы вакансий ----------
        self.tableJobs.setColumnCount(8)
        self.tableJobs.setHorizontalHeaderLabels(
            ["№", "Компания", "Позиция", "Вилка ₽", "Статус",
             "Источник", "Дата отклика", "Ссылка"]
        )
        header = self.tableJobs.horizontalHeader()
        # ResizeToContents — автоматически подогнать ширину под контент.
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        # Колонка "Позиция" (индекс 2) растягивается на всё доступное место.
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setStretchLastSection(False)
        # Выделение целых строк, а не отдельных ячеек.
        self.tableJobs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Только одинарное выделение (не несколько строк).
        self.tableJobs.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Запрещаем редактирование ячеек напрямую.
        self.tableJobs.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Отключаем перенос текста в ячейках.
        self.tableJobs.setWordWrap(False)
        # itemSelectionChanged — сигнал при выборе строки в таблице.
        self.tableJobs.itemSelectionChanged.connect(self.refresh_notes_panel)
        # doubleClicked — сигнал при двойном клике на строку (редактирование).
        self.tableJobs.doubleClicked.connect(self.on_edit)

        # ---------- Связывание кнопок с обработчиками ----------
        # clicked.connect() — стандартный паттерн сигналов и слотов PyQt.
        # Когда пользователь нажимает кнопку, посылается сигнал clicked,
        # который вызывает указанный метод (слот).
        self.btnAdd.clicked.connect(self.on_add)
        self.btnEdit.clicked.connect(self.on_edit)
        self.btnDelete.clicked.connect(self.on_delete)
        self.btnStats.clicked.connect(self.on_stats)
        self.btnExport.clicked.connect(self.on_export)
        self.btnNoteAdd.clicked.connect(self.on_note_add)
        self.btnNoteDel.clicked.connect(self.on_note_del)

        # При запуске загружаем данные из JSON и отображаем их.
        self.refresh_jobs()

    # ---------- вспомогательные методы ----------

    def selected_job_id(self):
        """Возвращает ID выбранной вакансии или None."""
        row = self.tableJobs.currentRow()
        if row < 0:
            return None
        item = self.tableJobs.item(row, 0)
        if item is None:
            return None
        # ID сохранён через setData(Qt.UserRole) при заполнении таблицы.
        return item.data(Qt.ItemDataRole.UserRole)

    # ---------- операции с вакансиями ----------

    def refresh_jobs(self):
        """
        Полностью перерисовывает таблицу вакансий.

        Вызывается:
        - при запуске приложения;
        - после добавления/редактирования/удаления вакансии;
        - при смене фильтра в ComboBox.

        Алгоритм:
        1. Очищаем таблицу (setRowCount(0)).
        2. Получаем все вакансии через бизнес-логику.
        3. Фильтруем по статусу (если выбран не "Все").
        4. Заполняем таблицу, сохраняя ID в UserRole для каждой строки.
        """
        self.tableJobs.setRowCount(0)  # Очищаем таблицу.
        flt = self.cmbFilter.currentText()  # Текущий фильтр.
        for row_index, vacancy in enumerate(self.logic.vacancies()):
            # Пропускаем вакансии, не соответствующие фильтру.
            if flt != "Все" and vacancy["status"] != flt:
                continue
            self.tableJobs.insertRow(row_index)
            values = [
                str(vacancy["id"]), vacancy["company"], vacancy["position"],
                vacancy["salary"], vacancy["status"], vacancy["source"],
                iso_to_ru(vacancy["response_date"]), vacancy["link"],
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                # Сохраняем ID вакансии в скрытых данных элемента.
                # Это позволяет потом узнать, какую вакансию редактировать.
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, vacancy["id"])
                # Выделяем статус синим цветом для наглядности.
                if col == 4:
                    item.setForeground(Qt.GlobalColor.darkBlue)
                self.tableJobs.setItem(row_index, col, item)
        # Обновляем панели напоминаний и заметок.
        self.refresh_reminders()
        self.refresh_notes_panel()

    def on_add(self):
        """
        Открывает диалог добавления новой вакансии.

        JobDialog создаётся без vacancy — значит, это форма добавления.
        exec() блокирует выполнение до закрытия диалога.
        DialogCode.Accepted означает, что пользователь нажал OK.
        """
        dialog = JobDialog(self, logic=self.logic)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                # Собираем данные из формы и передаём в бизнес-логику.
                self.logic.add_vacancy(dialog.form_data())
            except ValidationError as exc:
                QMessageBox.warning(self, "Проверка данных", str(exc))
            self.refresh_jobs()
            # statusBar().showMessage() — временное сообщение в статус-баре.
            # 4000 = время отображения в миллисекундах.
            self.statusBar().showMessage("Вакансия добавлена", 4000)

    def on_edit(self):
        """
        Открывает диалог редактирования выбранной вакансии.

        Сначала получаем ID выбранной строки, затем загружаем вакансию
        из DAL и передаём её в JobDialog для предзаполнения формы.
        """
        job_id = self.selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "Редактирование", "Выберите вакансию в списке.")
            return
        # Загружаем полные данные вакансии.
        vacancy = self.logic.vacancy_by_id(job_id)
        # Передаём vacancy — диалог предзаполнит форму.
        dialog = JobDialog(self, logic=self.logic, vacancy=vacancy)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self.logic.update_vacancy(job_id, dialog.form_data())
            except ValidationError as exc:
                QMessageBox.warning(self, "Проверка данных", str(exc))
            self.refresh_jobs()
            self.statusBar().showMessage("Вакансия обновлена", 4000)

    def on_delete(self):
        """
        Удаляет выбранную вакансию с подтверждением.

        QMessageBox.question показывает модальное диалоговое окно
        с кнопками Да/Нет. Возвращает нажатую кнопку.
        """
        job_id = self.selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "Удаление", "Выберите вакансию в списке.")
            return
        vacancy = self.logic.vacancy_by_id(job_id)
        # Спрашиваем подтверждение перед удалением.
        answer = QMessageBox.question(
            self, "Удаление",
            "Удалить вакансию «%s — %s» вместе с заметками и напоминаниями?"
            % (vacancy["company"], vacancy["position"]),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,  # По умолчанию — Нет (безопаснее)
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.logic.delete_vacancy(job_id)
            self.refresh_jobs()
            self.statusBar().showMessage("Вакансия удалена", 4000)

    def on_stats(self):
        """Открывает модальное окно статистики."""
        StatsDialog(self.logic, self).exec()

    def on_export(self):
        """Открывает модальное окно экспорта."""
        ExportDialog(self.logic, self).exec()

    # ---------- напоминания ----------

    def refresh_reminders(self):
        """
        Перерисовывает панель напоминаний.

        Напоминания берутся из бизнес-логики и делятся на три категории:
        "Просрочено" (дата < сегодня), "Скоро" (в ближайшие 14 дней),
        "Планируется" (дальше).
        """
        self.listReminders.clear()  # Очищаем список.
        rows = self.logic.reminders_panel()
        if not rows:
            self.listReminders.addItem("Ближайших событий нет")
            return
        # Префиксы для визуального разделения категорий.
        prefix = {"Просрочено": "\u26a0\ufe0f Просрочено: ", "Скоро": "\u25cf Скоро: ", "Планируется": ""}
        for kind, event_date, text in rows:
            self.listReminders.addItem(
                "%s%s — %s" % (prefix[kind], event_date.strftime("%d.%m.%Y"), text)
            )

    # ---------- заметки ----------

    def refresh_notes_panel(self):
        """
        Перерисовывает панель заметок для выбранной вакансии.

        Вызывается:
        - при выборе строки в таблице (itemSelectionChanged);
        - после добавления/удаления заметки;
        - при обновлении списка вакансий.

        Если вакансия не выбрана — показываем подсказку.
        """
        self.listNotes.clear()
        job_id = self.selected_job_id()
        if job_id is None:
            self.listNotes.addItem("Выберите вакансию в списке, чтобы увидеть заметки")
            return
        notes = self.logic.notes_for(job_id)
        if not notes:
            self.listNotes.addItem("Заметок пока нет — добавьте первую кнопкой «+ Заметка»")
            return
        for note in notes:
            # QListWidgetItem — элемент для QListWidget.
            item = QListWidgetItem("%s — %s" % (iso_to_ru(note["date"]), note["text"]))
            # Сохраняем ID заметки для последующего удаления.
            item.setData(Qt.ItemDataRole.UserRole, note["id"])
            self.listNotes.addItem(item)

    def on_note_add(self):
        """
        Открывает диалог ввода многострочного текста для новой заметки.

        QInputDialog.getMultiLineText — стандартное диалоговое окно
        с полем ввода текста и кнопками OK/Cancel.
        Возвращает кортеж (текст, нажат_ли_OK).
        """
        job_id = self.selected_job_id()
        if job_id is None:
            QMessageBox.information(self, "Заметки", "Выберите вакансию в списке.")
            return
        text, ok = QInputDialog.getMultiLineText(
            self, "Новая заметка", "Текст заметки по этапу:", ""
        )
        if not ok:
            return  # Пользователь нажал Cancel.
        try:
            # Дата заметки берётся из dateNote — виджета в UI.
            self.logic.add_note(job_id, qd_to_iso(self.dateNote.date()), text)
        except ValidationError as exc:
            QMessageBox.warning(self, "Проверка данных", str(exc))
            return
        self.refresh_notes_panel()
        self.statusBar().showMessage("Заметка добавлена", 4000)

    def on_note_del(self):
        """
        Удаляет выбранную заметку.

        currentItem() возвращает выбранный QListWidgetItem.
        ID заметки извлекаем из пользовательских данных (UserRole).
        """
        item = self.listNotes.currentItem()
        note_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        if note_id is None:
            QMessageBox.information(self, "Заметки", "Выберите заметку для удаления.")
            return
        self.logic.delete_note(note_id)
        self.refresh_notes_panel()
        self.statusBar().showMessage("Заметка удалена", 4000)


# ----------------------------------------------------------------------
# Диалог вакансии (JobDialog)
# ----------------------------------------------------------------------
# Это переиспользуемый диалог для добавления и редактирования вакансий.
# Один и тот же класс используется в двух режимах:
# - vacancy=None  -> новая вакансия (форма пустая);
# - vacancy=dict  -> редактирование (форма заполнена данными).
#
# Ключевой приём: form_data() собирает все поля формы в словарь,
# который передаётся в бизнес-логику. Это разделяет сбор данных
# (UI) и их валидацию/сохранение (логика).

class JobDialog(QDialog):
    def __init__(self, parent=None, logic=None, vacancy=None):
        super().__init__(parent)
        load_ui(self, "job_dialog.ui")
        self.logic = logic
        self.vacancy_id = None

        # Заполняем ComboBox статуса из константы STATUSES.
        self.cmbStatus.addItems(STATUSES)
        # Связываем кнопки OK/Cancel с обработчиками.
        self.btnOk.clicked.connect(self.accept_check)
        self.btnCancel.clicked.connect(self.reject)

        if vacancy is not None:
            # ---------- РЕЖИМ РЕДАКТИРОВАНИЯ ----------
            self.vacancy_id = vacancy["id"]
            self.setWindowTitle("Редактирование вакансии")
            self.lblHeader.setText("Редактирование вакансии")
            # Предзаполняем все поля формы данными вакансии.
            self.edtCompany.setText(vacancy["company"])
            self.edtPosition.setText(vacancy["position"])
            self.edtSalary.setText(vacancy["salary"])
            self.edtSource.setText(vacancy["source"])
            self.edtLink.setText(vacancy["link"])
            self.cmbStatus.setCurrentText(vacancy["status"])
            self.dateResponse.setDate(
                QDate.fromString(vacancy["response_date"], "yyyy-MM-dd")
            )
            # Загружаем напоминание вакансии для предзаполнения.
            reminders = self.logic.reminders_for(vacancy["id"])
            if reminders:
                self.dateEvent.setDate(
                    QDate.fromString(reminders[0]["date"], "yyyy-MM-dd")
                )
                self.edtEvent.setText(reminders[0]["event"])
        else:
            # ---------- РЕЖИМ ДОБАВЛЕНИЯ ----------
            # Ставим текущую дату для отклика и +7 дней для события.
            self.dateResponse.setDate(QDate.currentDate())
            self.dateEvent.setDate(QDate.currentDate().addDays(7))
        # Фокус на поле "Позиция" — пользователь может сразу печатать.
        self.edtPosition.setFocus()

    def accept_check(self):
        """
        Проверка данных перед закрытием диалога.

        Вместо того чтобы позволять UI передавать данные напрямую,
        мы сначала валидируем их через бизнес-логику.
        Если валидация не прошла — показываем предупреждение и НЕ закрываем диалог.
        Если прошла — вызываем accept(), который завершает exec() с кодом Accepted.
        """
        try:
            self.logic.validate_vacancy_form(self.form_data())
        except ValidationError as exc:
            QMessageBox.warning(self, "Проверка данных", str(exc))
            return
        self.accept()  # Закрывает диалог с кодом Accepted.

    def form_data(self):
        """
        Собирает все поля формы в словарь.

        Этот метод вызывается из main.py (on_add / on_edit) для передачи
        данных в бизнес-логику. strip() убирает пробелы по краям.

        Возвращает:
            dict с ключами: company, position, salary, source, link,
            status, response_date, event_date, event_text
        """
        return {
            "company": self.edtCompany.text().strip(),
            "position": self.edtPosition.text().strip(),
            "salary": self.edtSalary.text().strip(),
            "source": self.edtSource.text().strip(),
            "link": self.edtLink.text().strip(),
            "status": self.cmbStatus.currentText(),
            "response_date": qd_to_iso(self.dateResponse.date()),
            "event_date": qd_to_iso(self.dateEvent.date()),
            "event_text": self.edtEvent.text().strip(),
        }


# ----------------------------------------------------------------------
# Точка входа
# ----------------------------------------------------------------------

def excepthook(exc_type, exc_value, exc_tb):
    """
    Перехват НЕОБРАБОТАННЫХ исключений.

    По умолчанию Python «падает» с traceback в консоль. Мы заменяем
    это поведение: показываем QMessageBox с деталями ошибки и
    продолжаем работу (если GUI ещё жив).

    sys.excepthook — это глобальный обработчик, который Python вызывает
    для всех необработанных исключений.

    Аргументы (стандартная сигнатура traceback):
        exc_type: тип исключения (ValueError, KeyError, ...)
        exc_value: само исключение (текст ошибки)
        exc_tb: стек вызовов (traceback)
    """
    # Если QApplication ещё не создан — выводим в консоль как обычно.
    if QApplication.instance() is None:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    detail = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    QMessageBox.critical(None, "Непредвиденная ошибка",
                         "Приложение продолжит работу, но произошла ошибка:\n%s\n\nПодробности:\n%s"
                         % (exc_value, detail[-1500:]))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main():
    """
    Точка входа приложения.

    Порядок:
    1. Создаём QApplication — ОДИН экземпляр на всё приложение.
       Он управляет основным циклом событий (обработка кликов, клавиш и т.д.).
    2. setApplicationName — имя приложения в системных вызовах.
    3. setStyle("Fusion") — кроссплатформенный стиль (выглядит одинаково
       на Windows, macOS, Linux). Без Fusion стиль зависит от ОС.
    4. setStyleSheet(STYLE) — применяем наши QSS-стили.
    5. Создаём и показываем главное окно.
    6. app.exec() — ЗАПУСК основного цикла событий.
       Эта строка не возвращается, пока окно не закрыто.
    """
    app = QApplication(sys.argv)
    app.setApplicationName("Вакансии")
    app.setStyle("Fusion")  # Единый стиль на всех платформах.
    app.setStyleSheet(STYLE)  # Применяем QSS-стили.
    window = MainWindow()
    window.show()  # Показываем окно (не вызывает обработчики событий).
    return app.exec()  # Запускаем цикл обработки событий (блокирует возврат).


if __name__ == "__main__":
    # Регистрируем перехватчик необработанных исключений.
    sys.excepthook = excepthook
    sys.exit(main())
