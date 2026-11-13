"""Регрессия: двухстрочный статус TG не растягивает соседние карточки."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

from ui.theme import theme
from ui.widgets.base import WrapLabel
from ui.tabs.dashboard import ServiceTile


class WrapLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.apply(cls.app)

    def settle(self):
        for _ in range(3):
            QCoreApplication.sendPostedEvents(None, QEvent.LayoutRequest)
        self.app.processEvents()

    def test_explicit_minimum_survives_text_and_resize(self):
        widget = WrapLabel("Коротко")
        widget.resize(300, 100)
        widget.setMinimumHeight(50)
        widget.show()
        try:
            self.settle()
            self.assertEqual(widget.minimumHeight(), 50)
            widget.setText("Строка\n" * 10)
            self.assertGreater(widget.minimumHeight(), 50)
            widget.setText("")
            widget.resize(250, 100)
            self.settle()
            self.assertEqual(widget.minimumHeight(), 50)
        finally:
            widget.close()

    def test_two_line_tg_hint_preserves_service_row_height(self):
        root = QWidget()
        row = QHBoxLayout(root)
        tiles = [ServiceTile("", title, "Выбор", "")
                 for title in ("Обход", "DNS", "Telegram")]
        for tile in tiles:
            tile.setFixedWidth(344)
            row.addWidget(tile)
        tiles[0].set_hint("Лучший по последней проверке")
        tiles[1].set_hint("При выходе вернётся DNS провайдера")
        root.show()
        try:
            self.settle()
            before = [(tile.height(), tile.hint.y()) for tile in tiles]
            for text in ("Telegram\nСтарый секрет", "", "Скачиваю…"):
                tiles[2].set_hint(text)
                self.settle()
                self.assertEqual([(tile.height(), tile.hint.y()) for tile in tiles], before)
                self.assertTrue(all(tile.hint.minimumHeight() >= 34 for tile in tiles))
        finally:
            root.close()


if __name__ == "__main__":
    unittest.main()
