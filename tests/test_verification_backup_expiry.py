from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from verification.backup_expiry import expire


class BackupExpiry(unittest.TestCase):
    def test_cutoff_and_archive_selection(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            now = datetime(2026, 1, 2, tzinfo=timezone.utc)
            old = "verification-20260101T000000Z-"
            suffix = "a" * 32 + ".tar"
            boundary = root / (old + suffix)
            boundary.touch()
            recent = root / ("verification-20260101T000001Z-" + suffix)
            recent.touch()
            (root / (old + "b" * 32 + ".tar")).symlink_to(recent)
            (root / (old + "c" * 32 + ".tar")).mkdir()
            (root / (boundary.name + ".partial")).touch()
            (root / "secret").touch()
            with self.assertRaises(ValueError):
                expire(root, 0, apply=True, now=now)
            self.assertEqual(expire(root, 1, now=now)["eligible_archives"], 1)
            self.assertTrue(boundary.exists())
            self.assertEqual(expire(root, 1, apply=True, now=now)["eligible_archives"], 1)
            self.assertFalse(boundary.exists())
            self.assertEqual(len(list(root.iterdir())), 5)
