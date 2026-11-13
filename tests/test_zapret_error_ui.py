"""Ошибка запуска обхода отображается без вторичного исключения интерфейса."""
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from core.service.client import ServiceError
from core.zapret.manager import ZapretManager, ServiceState
from ui.main_window import MainWindow


class ZapretErrorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="flowzap-error-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manager = ZapretManager(self.root / "winws.exe")
        self.addCleanup(self.manager.cancel_pending)
        self.exceptions = []
        for stub in (
            patch("threading.Thread.start"),
            patch.object(MainWindow, "_build_tray"),
            patch.object(MainWindow, "_start_update_autocheck"),
            patch.object(MainWindow, "_refresh_mode"),
            patch.object(logging.Logger, "handle"),
            patch.object(sys, "excepthook", lambda kind, value, tb: self.exceptions.append(value)),
        ):
            stub.start()
            self.addCleanup(stub.stop)
        self.window = MainWindow(self.root, manager=self.manager, config={
            "_app_dir": str(self.root),
            "ui": {"animations": False, "aurora": False, "restore_state": False},
            "updater": {"check_on_start": False}, "dns": {"pairs": []},
        })
        self.addCleanup(self.dispose_window)

    def dispose_window(self):
        self.app.aboutToQuit.disconnect(self.window.dashboard.shutdown)
        self.window.deleteLater()

    def test_missing_preset_shows_fallback_without_exception(self):
        self.assertFalse(self.manager.start(self.root / "missing.bat"))
        self.assertEqual(self.manager.state, ServiceState.ERROR)
        self.assertEqual(self.exceptions, [])
        hint = self.window.dashboard._tile_zapret.hint.text()
        self.assertIn("выберите другой пресет", hint)
        self.assertNotIn(str(self.root), hint)

    def test_service_failure_shows_user_message_and_next_attempt_clears_it(self):
        preset = self.root / "general.bat"
        preset.touch()
        self.manager.zapret.executable.touch()
        with patch("core.zapret.manager.build_winws_cmd", return_value=(["unused"], self.root)), \
                patch("core.zapret.manager.runner.launch", side_effect=ServiceError("Фоновая служба недоступна")):
            self.assertFalse(self.manager.start(preset))
        self.assertEqual(self.exceptions, [])
        self.assertEqual(self.window.dashboard._tile_zapret.hint.text(), "Фоновая служба недоступна")
        self.assertFalse(self.manager.start(self.root / "missing.bat"))
        self.assertEqual(self.exceptions, [])
        self.assertIn("выберите другой пресет", self.window.dashboard._tile_zapret.hint.text())


if __name__ == "__main__":
    unittest.main()
