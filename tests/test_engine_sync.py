"""Синхронизация версий движка: служба и сеть заменены заглушками."""
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.service.client import ServiceError
from core.zapret import runner


class EngineSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="flowzap-sync-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "version.txt").write_text("test-new", encoding="utf-8")
        self.session = Mock()
        self.session.status.return_value = {"engine_version": "test-old"}
        self.previous_cache = runner._synced_version
        runner._synced_version = None
        self.addCleanup(setattr, runner, "_synced_version", self.previous_cache)
        for stub in (
            patch.object(runner.service_client, "session", self.session),
            patch("core.dns.manager.retry_without_dns", side_effect=lambda action, *args: action()),
            patch.object(logging.Logger, "handle"),
        ):
            stub.start()
            self.addCleanup(stub.stop)

    def test_failed_sync_retries_and_success_is_cached(self):
        self.session.update_engine.side_effect = [ServiceError("Проверочный отказ"), "test-new"]
        runner.sync_engine(self.root / "bin")
        self.assertIsNone(runner._synced_version)
        runner.sync_engine(self.root / "bin")
        self.assertEqual(runner._synced_version, "test-new")
        runner.sync_engine(self.root / "bin")
        self.assertEqual(self.session.status.call_count, 2)
        self.assertEqual(self.session.update_engine.call_count, 2)

    def test_repeated_failures_are_not_cached(self):
        self.session.update_engine.side_effect = ServiceError("Проверочный отказ")
        for _ in range(2):
            runner.sync_engine(self.root / "bin")
            self.assertIsNone(runner._synced_version)
        self.assertEqual(self.session.update_engine.call_count, 2)

    def test_no_installed_engine_still_raises_and_retries(self):
        self.session.status.return_value = {"engine_version": ""}
        self.session.update_engine.side_effect = ServiceError("Проверочный отказ")
        for _ in range(2):
            with self.assertRaises(ServiceError):
                runner.sync_engine(self.root / "bin")
            self.assertIsNone(runner._synced_version)
        self.assertEqual(self.session.update_engine.call_count, 2)

    def test_matching_engine_does_not_download(self):
        self.session.status.return_value = {"engine_version": "test-new"}
        runner.sync_engine(self.root / "bin")
        runner.sync_engine(self.root / "bin")
        self.session.status.assert_called_once()
        self.session.update_engine.assert_not_called()


if __name__ == "__main__":
    unittest.main()
