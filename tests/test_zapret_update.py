"""Изолированные проверки установки: без сети, драйвера и реальной службы."""
import io
import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from core.updates import zapret as updater
from core.updates import component_transaction as transaction


REQUIRED = ("bin/winws.exe", "bin/WinDivert.dll", "bin/WinDivert64.sys",
            "bin/cygwin1.dll", "lists/list-general.txt", "lists/ipset-all.txt")
_qt_app = None


def write_tree(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()} if root.exists() else {}


def archive(files=None):
    files = files if files is not None else {
        **{name: b"new" for name in REQUIRED},
        "general-new.bat": b"new preset",
        "lists/list-general-user.txt": b"release defaults",
        "lists/list-exclude.txt": b"release excludes",
        "utils/game_filter.enabled": b"release filter",
    }
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as stream:
        for name, content in files.items():
            stream.writestr("zapret-release/" + name, content)
    return data.getvalue()


class ZapretUpdateTests(unittest.TestCase):
    def setUp(self):
        quiet = patch.object(logging.Logger, "handle")
        quiet.start()
        self.addCleanup(quiet.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="flowzap-update-test-")
        self.addCleanup(self.tmp.cleanup)
        self.target = Path(self.tmp.name) / "zapret"
        write_tree(self.target, {
            **{name: b"old" for name in REQUIRED},
            "general-old.bat": b"obsolete",
            "my-preset.bat": b"custom",
            "version.txt": b"v1",
            updater.PRESETS_MANIFEST: b"general-old.bat\n",
            "lists/list-general-user.txt": b"my domains",
            "lists/list-exclude.txt": b"my exclusions",
            "lists/ipset-exclude-user.txt": b"my IPs",
            "lists/flowzap-game-list-all.txt": b"games",
            "utils/game_filter.enabled": b"mode=all",
        })
        self.original = snapshot(self.target)
        updater._exit_requested.clear()
        self.addCleanup(updater._exit_requested.clear)
        self.service = patch("core.service.client.service_installed", return_value=True)
        self.service.start()
        self.addCleanup(self.service.stop)

    def install(self, data=None, before=None):
        updater._install_archive(data if data is not None else archive(),
                                 self.target, "v2", lambda message: None, before)

    def prepared(self):
        work = transaction.transaction_dir(self.target)
        write_tree(work / "new", {"version.txt": b"v2"})
        return work

    def test_success_preserves_custom_files_and_exclusions(self):
        stopped = []
        self.install(before=lambda: stopped.append(True))
        result = snapshot(self.target)
        self.assertEqual(stopped, [True])
        self.assertEqual(result["version.txt"], b"v2")
        self.assertEqual(result["bin/winws.exe"], b"new")
        self.assertNotIn("general-old.bat", result)
        self.assertIn("general-new.bat", result)
        self.assertEqual(result[updater.PRESETS_MANIFEST].splitlines(), [b"general-new.bat"])
        for name in ("my-preset.bat", "lists/list-general-user.txt", "lists/list-exclude.txt",
                     "lists/ipset-exclude-user.txt", "lists/flowzap-game-list-all.txt",
                     "utils/game_filter.enabled"):
            self.assertEqual(result[name], self.original[name], name)
        self.assertFalse(transaction.transaction_dir(self.target).exists())

    def test_first_install(self):
        self.target = self.target.with_name("first")
        self.install()
        self.assertEqual((self.target / "version.txt").read_bytes(), b"v2")

    def test_bad_payload_does_not_stop_or_modify_current(self):
        for payload in (archive({"README.md": b"nothing"}), b"not zip",
                        archive({**{n: b"" for n in REQUIRED}, "test.bat": b"x"})):
            with self.subTest(payload=payload[:8]):
                stop = []
                with self.assertRaises((ValueError, zipfile.BadZipFile)):
                    self.install(payload, before=lambda: stop.append(True))
                self.assertFalse(stop)
                self.assertEqual(snapshot(self.target), self.original)

    def test_copy_failure_keeps_original(self):
        with patch.object(updater.shutil, "copy2", side_effect=OSError("Нет места")):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)
        # Повтор после отказа подготовки не требует ручной уборки.
        self.install()
        self.assertEqual((self.target / "version.txt").read_bytes(), b"v2")

    def test_swap_failure_restores_original(self):
        rename = Path.rename
        def fail_new(path, dest):
            if path == transaction.transaction_dir(self.target) / "new":
                raise PermissionError("Занято антивирусом")
            return rename(path, dest)
        with patch.object(Path, "rename", fail_new):
            with self.assertRaises(ValueError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)

    def test_failure_to_move_old_keeps_original(self):
        rename = Path.rename
        def fail_old(path, dest):
            if path == self.target:
                raise PermissionError("Файл занят")
            return rename(path, dest)
        with patch.object(Path, "rename", fail_old):
            with self.assertRaises(ValueError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)

    def test_interruption_after_old_rename_recovers(self):
        work = self.prepared()
        rename = Path.rename
        def interrupt(path, dest):
            result = rename(path, dest)
            if path == self.target:
                raise SystemExit("Имитация завершения процесса")
            return result
        with patch.object(Path, "rename", interrupt):
            with self.assertRaises(SystemExit):
                transaction.commit(self.target)
        self.assertFalse(self.target.exists())
        self.assertTrue((work / "old").exists())
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_interruption_after_new_rename_rolls_back(self):
        work = self.prepared()
        rename = Path.rename
        def interrupt(path, dest):
            result = rename(path, dest)
            if path == work / "new":
                raise SystemExit("Имитация завершения процесса")
            return result
        with patch.object(Path, "rename", interrupt):
            with self.assertRaises(SystemExit):
                transaction.commit(self.target)
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_committed_update_survives_cleanup_interruption(self):
        self.prepared()
        with patch.object(transaction, "_retire", side_effect=OSError("Уборка недоступна")):
            transaction.commit(self.target)
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), {"version.txt": b"v2"})

    def test_failed_rollback_preserves_backup_for_retry(self):
        work = self.prepared()
        rename = Path.rename
        def fail(path, dest):
            if path in (work / "new", work / "old"):
                raise PermissionError("Занято")
            return rename(path, dest)
        with patch.object(Path, "rename", fail):
            with self.assertRaises(ValueError):
                transaction.commit(self.target)
        self.assertEqual(snapshot(work / "old"), self.original)
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_exit_during_preparation_does_not_replace_files(self):
        with self.assertRaises(ValueError):
            self.install(before=updater._exit_requested.set)
        self.assertEqual(snapshot(self.target), self.original)

    def test_exit_waits_for_inflight_commit(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        def commit():
            with updater._commit_lock:
                entered.set()
                release.wait(5)
        worker = threading.Thread(target=commit)
        worker.start()
        self.assertTrue(entered.wait(2))
        quitter = threading.Thread(target=lambda: (updater.finish_before_exit(), done.set()))
        quitter.start()
        try:
            self.assertTrue(updater._exit_requested.wait(2))
            self.assertFalse(done.is_set())
        finally:
            release.set()
            worker.join(5)
            quitter.join(5)
        self.assertTrue(done.is_set())

    def test_unsafe_archive_is_rejected(self):
        with self.assertRaises(ValueError):
            self.install(archive({"../../outside.txt": b"bad"}))
        self.assertEqual(snapshot(self.target), self.original)
        self.assertFalse((Path(self.tmp.name) / "outside.txt").exists())

    def test_journal_write_failure_keeps_original_and_can_recover(self):
        self.prepared()
        with patch.object(transaction.os, "fsync", side_effect=OSError("Нет места")):
            with self.assertRaises(OSError):
                transaction.commit(self.target)
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_commit_marker_failure_restores_original(self):
        self.prepared()
        fsync = transaction.os.fsync
        calls = []
        def fail_marker(fd):
            calls.append(fd)
            if len(calls) == 2:
                raise OSError("Ошибка записи отметки")
            return fsync(fd)
        with patch.object(transaction.os, "fsync", fail_marker):
            with self.assertRaises(ValueError):
                transaction.commit(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_interrupted_first_install_is_not_reported_as_installed(self):
        self.target = self.target.with_name("first")
        work = self.prepared()
        rename = Path.rename
        def interrupt(path, dest):
            result = rename(path, dest)
            if path == work / "new":
                raise SystemExit("Имитация завершения процесса")
            return result
        with patch.object(Path, "rename", interrupt):
            with self.assertRaises(SystemExit):
                transaction.commit(self.target)
        updater.recover_interrupted_update(self.target)
        self.assertFalse(self.target.exists())

    def test_quit_confirmation_and_close_to_tray(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ui.main_window import MainWindow, QMessageBox
        fake = SimpleNamespace(tray=Mock(), dashboard=SimpleNamespace(_installing_core=True))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
                patch("ui.main_window.QApplication.quit") as quit_app:
            MainWindow._quit(fake)
            quit_app.assert_not_called()
            fake.tray.hide.assert_not_called()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                patch("ui.main_window.QApplication.quit") as quit_app:
            MainWindow._quit(fake)
            quit_app.assert_called_once()
        fake = SimpleNamespace(minimize_to_tray=True, hide=Mock(), _quit=Mock())
        event = Mock()
        MainWindow.closeEvent(fake, event)
        event.ignore.assert_called_once()
        fake.hide.assert_called_once()
        fake._quit.assert_not_called()

    def test_widgets_block_edits_during_update(self):
        global _qt_app
        from PySide6.QtWidgets import QApplication
        from ui.main_window import MainWindow
        with patch.dict(os.environ, {"QT_QPA_PLATFORM": "offscreen"}):
            _qt_app = QApplication.instance() or QApplication([])
        config = {"_app_dir": self.tmp.name,
                  "ui": {"animations": False, "aurora": False, "restore_state": False},
                  "updater": {"check_on_start": False}, "dns": {"pairs": []}}
        # Все фоновые действия выключены: тестируются настоящие виджеты и
        # соединение сигнала, но не служба, сеть, WinDivert или автозапуск.
        with patch("threading.Thread.start"), patch.object(MainWindow, "_build_tray"), \
                patch.object(MainWindow, "_start_update_autocheck"):
            window = MainWindow(Path(self.tmp.name), config=config)
        try:
            window.dashboard.set_component_updating("core", True)
            self.assertFalse(window.dashboard._presets_panel.isEnabled())
            self.assertFalse(window.parameters._sections["lists"].isEnabled())
            self.assertTrue(window.parameters._sections["dns"].isEnabled())
            self.assertFalse(window.parameters._write_list("list-general-user.txt", ["changed"]))
            self.assertEqual((self.target / "lists/list-general-user.txt").read_bytes(), b"my domains")
            window.dashboard.set_component_updating("core", False)
            self.assertTrue(window.dashboard._presets_panel.isEnabled())
            self.assertTrue(window.parameters._sections["lists"].isEnabled())
        finally:
            _qt_app.aboutToQuit.disconnect(window.dashboard.shutdown)
            window.deleteLater()

    @unittest.skipUnless(os.name == "nt", "Проверка блокировки файлов Windows")
    def test_windows_locked_file_keeps_original(self):
        with (self.target / "bin" / "winws.exe").open("rb"):
            with self.assertRaises(ValueError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)

    def test_process_termination_recovers_on_next_run(self):
        work = self.prepared()
        code = """
import os, sys
from pathlib import Path
from core.updates import component_transaction as transaction
target = Path(sys.argv[1])
rename = Path.rename
def interrupt(path, dest):
    result = rename(path, dest)
    if path == target:
        os._exit(86)
    return result
Path.rename = interrupt
transaction.commit(target)
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.target)],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, timeout=20,
                                creationflags=0x08000000 if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 86, result.stderr.decode("utf-8", errors="replace"))
        self.assertEqual(snapshot(work / "old"), self.original)
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_empty_domain_lists_are_valid(self):
        payload = {name: b"new" for name in REQUIRED}
        payload.update({"general.bat": b"preset", "lists/list-general.txt": b"", "lists/ipset-all.txt": b""})
        self.install(archive(payload))
        self.assertEqual((self.target / "version.txt").read_bytes(), b"v2")

    def test_custom_path_and_cleanup_do_not_touch_unrelated_folder(self):
        self.target = self.target.with_name("zapret [test]")
        unrelated = self.target.with_name(".zapret [test].flowzap-update.cleanup-mine")
        write_tree(unrelated, {"keep.txt": b"keep"})
        self.install()
        self.assertEqual((self.target / "version.txt").read_bytes(), b"v2")
        self.assertEqual((unrelated / "keep.txt").read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
