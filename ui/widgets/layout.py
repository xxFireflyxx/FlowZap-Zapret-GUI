"""
ui/widgets/layout.py
--------------------
Раскладка страниц: страница с заголовком и прокруткой, строка настройки.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QVBoxLayout, QWidget

from ui.theme import theme
from ui.widgets.base import label


class SettingRow(QWidget):
    """Строка настройки: название и пояснение слева, контрол справа."""

    def __init__(self, title: str, description: str = "", control: QWidget | None = None, parent=None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 4, 0, 4)
        row.setSpacing(16)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(title, role="strong"))
        self.description = label(description, role="muted", wrap=True)
        # Только hide(), не setVisible(True): у виджета, ещё не вставленного в
        # строку, show показывает его отдельным окном — при запуске мелькали окошки
        if not description:
            self.description.hide()
        col.addWidget(self.description)
        row.addLayout(col, stretch=1)
        if control is not None:
            row.addWidget(control, alignment=Qt.AlignVCenter)


class Page(QWidget):
    """Страница вкладки: заголовок + подзаголовок + действия справа,
    под ними прокручиваемое содержимое (self.body) ограниченной ширины."""

    def __init__(self, title: str, subtitle: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        m = theme.metrics

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("pageContent")
        scroll.setWidget(content)
        center = QHBoxLayout(content)
        center.setContentsMargins(m.padding_lg, m.padding_lg - 4, m.padding_lg, m.padding_lg)

        column = QWidget()
        column.setMaximumWidth(m.content_max_width)
        center.addStretch(0)
        center.addWidget(column, stretch=1)
        center.addStretch(0)

        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(m.padding_md)

        # Без заголовка (главная) — содержимое начинается сразу под шапкой окна.
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            titles = QVBoxLayout()
            titles.setSpacing(2)
            titles.addWidget(label(title, role="title"))
            if subtitle:
                titles.addWidget(label(subtitle, role="subtitle"))
            head.addLayout(titles, stretch=1)
            head.addLayout(self.header_actions)
            layout.addLayout(head)
            layout.addSpacing(4)

        self.body = QVBoxLayout()
        self.body.setSpacing(m.padding_md)
        layout.addLayout(self.body)
        layout.addStretch(1)
