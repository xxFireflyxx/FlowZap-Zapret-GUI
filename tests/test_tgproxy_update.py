"""Безопасное обновление Telegram Proxy без сети и рабочего Telegram."""
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.updates import tgproxy as updater
from core.updates import component_transaction as transaction


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
        "proxy/__init__.py": b"__version__ = '2'\n",
        "proxy/tg_ws_proxy.py": b"def main():\n    import argparse\n    parser = argparse.ArgumentParser()\n    parser.add_argument('--port')\n    parser.parse_args()\n    raise RuntimeError('server must not start in probe')\n",
        "utils/__init__.py": b"# utils\n", "LICENSE": b"test license",
        "README.md": b"not installed", "user-config.json": b"not installed",
    }
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as stream:
        for name, content in files.items():
            stream.writestr("release/" + name, content)
    return data.getvalue()


class TgProxyUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flowzap-tg-update-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / "tgproxy"
        write_tree(self.target, {
            "src/proxy/tg_ws_proxy.py": b"old code",
            "src/proxy/removed.py": b"obsolete module",
            "src/options.json": b'{"version":"1","options":["--old"]}',
            "version.txt": b"v1", "local-note.txt": b"keep",
            "nested/src/custom.txt": b"keep nested",
        })
        (self.root / "config.toml").write_bytes(b"settings remain here")
        self.original = snapshot(self.target)
        updater._exit_requested.clear()
        self.addCleanup(updater._exit_requested.clear)
        self.probe_patch = patch.object(updater, "probe", return_value={"version": "2", "options": ["--port"]})
        self.probe = self.probe_patch.start()
        self.addCleanup(self.probe_patch.stop)
        quiet = patch.object(logging.Logger, "handle")
        quiet.start()
        self.addCleanup(quiet.stop)

    def install(self, data=None):
        updater._install_archive(data if data is not None else archive(), self.target, "v2", lambda message: None)

    def test_success_replaces_code_options_and_version_together(self):
        self.install()
        self.assertEqual(updater.get_installed_tg_proxy_version(self.target), "v2")
        self.assertEqual(json.loads((self.target / "src/options.json").read_text())["version"], "2")
        self.assertFalse((self.target / "src/proxy/removed.py").exists())
        self.assertEqual((self.target / "local-note.txt").read_bytes(), b"keep")
        self.assertEqual((self.target / "nested/src/custom.txt").read_bytes(), b"keep nested")
        self.assertEqual((self.root / "config.toml").read_bytes(), b"settings remain here")
        self.assertFalse((self.target / "README.md").exists())
        self.assertFalse((self.target / "user-config.json").exists())
        self.assertFalse((self.target / "src.new").exists())
        self.assertFalse(transaction.transaction_dir(self.target).exists())
        self.probe.assert_called_once_with(self.target / "src.new")

    def test_first_install(self):
        self.target = self.root / "first"
        self.install()
        self.assertEqual(updater.get_installed_tg_proxy_version(self.target), "v2")

    def test_probe_failure_keeps_old_installation(self):
        self.probe.return_value = {"error": "missing dependency"}
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(snapshot(self.target), self.original)

    def test_bad_archive_keeps_old_installation(self):
        for data in (b"not zip", archive({"README.md": b"empty"})):
            with self.assertRaises((ValueError, zipfile.BadZipFile)):
                self.install(data)
            self.assertEqual(snapshot(self.target), self.original)
        self.probe.assert_not_called()

    def test_unsafe_paths_rejected(self):
        for name in ("proxy/../../outside.py", "proxy/..\\..\\outside.py", "C:/outside.py"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.install(archive({name: b"bad"}))
                self.assertEqual(snapshot(self.target), self.original)
        self.assertFalse((self.root / "outside.py").exists())

    def test_copy_failure_keeps_code_and_version(self):
        with patch.object(updater.shutil, "copytree", side_effect=OSError("Нет места")):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)
        self.install()
        self.assertEqual(updater.get_installed_tg_proxy_version(self.target), "v2")

    def test_swap_failure_restores_code_options_and_version(self):
        rename = Path.rename
        def fail(path, dest):
            if path == transaction.transaction_dir(self.target) / "new":
                raise PermissionError("Файлы заняты")
            return rename(path, dest)
        with patch.object(Path, "rename", fail):
            with self.assertRaises(ValueError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)
        self.assertEqual(updater.get_installed_tg_proxy_version(self.target), "v1")

    def test_interrupted_swap_restores_pair(self):
        rename = Path.rename
        def interrupt(path, dest):
            result = rename(path, dest)
            if path == transaction.transaction_dir(self.target) / "new":
                raise SystemExit("Имитация аварийного завершения")
            return result
        with patch.object(Path, "rename", interrupt):
            with self.assertRaises(SystemExit):
                self.install()
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_exit_after_probe_prevents_swap(self):
        def cancel(source):
            updater.request_exit()
            return {"version": "2", "options": ["--port"]}
        self.probe.side_effect = cancel
        with self.assertRaises(ValueError):
            self.install()
        self.assertEqual(snapshot(self.target), self.original)

    def test_exit_waits_for_critical_section(self):
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

    def test_legacy_interrupted_src_swap_recovers(self):
        (self.target / "src").rename(self.target / "src.old")
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)

    def test_quit_confirmation_covers_proxy_update(self):
        from ui.main_window import MainWindow, QMessageBox
        fake = SimpleNamespace(tray=Mock(), dashboard=SimpleNamespace(_installing_core=False, _tg_installing=True))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No), \
                patch("ui.main_window.QApplication.quit") as quit_app:
            MainWindow._quit(fake)
            quit_app.assert_not_called()

    def test_late_callbacks_do_not_restart_proxy(self):
        from ui.tabs.dashboard import DashboardTab
        from ui.tabs.updates import UpdatesTab
        dashboard = SimpleNamespace(_shutting_down=True)
        tab = SimpleNamespace(_dashboard=dashboard)
        DashboardTab._on_tg_install_done(dashboard, True, "done")
        DashboardTab._on_tg_proxy_done(dashboard, True, "")
        UpdatesTab._on_tg_done(tab, True, "done")
        UpdatesTab._on_tg_stopped(tab, False, "")
        UpdatesTab._download_tg(tab)
        UpdatesTab._on_finished(tab, "tg", True, "done")

    def test_metadata_write_failure_keeps_old_installation(self):
        write = Path.write_text
        def fail(path, *args, **kwargs):
            if path == transaction.transaction_dir(self.target) / "new/version.txt":
                raise OSError("Нет места")
            return write(path, *args, **kwargs)
        with patch.object(Path, "write_text", fail):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(snapshot(self.target), self.original)

    def test_real_probe_in_separate_process(self):
        from core.tgproxy import host
        launcher = self.root / "main.py"
        # Отдельный корень приложения для теста. Белый список host.py не меняем:
        # src.new остаётся внутри собственного tgproxy тестового приложения.
        launcher.write_text("import sys\nsys.dont_write_bytecode=True\n"
                            f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1])!r})\n"
                            "from core.tgproxy import host\n"
                            "host.self_command=lambda: [sys.executable, __file__]\n"
                            "sys.exit(host.run(sys.argv[1:]))\n", encoding="utf-8")
        with patch.object(host, "self_command", return_value=[sys.executable, str(launcher)]), \
                patch.object(updater, "probe", side_effect=host.probe):
            self.install()
        info = json.loads((self.target / "src/options.json").read_text())
        self.assertEqual(info["version"], "2")
        self.assertIn("--port", info["options"])

    def test_process_termination_recovers_whole_installation(self):
        work = transaction.transaction_dir(self.target)
        write_tree(work / "new", {"version.txt": b"v2", "src/proxy/tg_ws_proxy.py": b"new"})
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
transaction.commit(target, 'Telegram Proxy')
"""
        result = subprocess.run([sys.executable, "-B", "-c", code, str(self.target)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                timeout=20, creationflags=0x08000000 if os.name == "nt" else 0)
        self.assertEqual(result.returncode, 86)
        updater.recover_interrupted_update(self.target)
        self.assertEqual(snapshot(self.target), self.original)


if __name__ == "__main__":
    unittest.main()
