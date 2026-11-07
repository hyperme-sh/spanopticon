import hashlib
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from verification.backup_replica import transfer


class BackupReplica(unittest.TestCase):
    def test_incomplete_upload_duplicate_and_corruption(self):
        data = b"snapshot"
        digest = hashlib.sha256(data).hexdigest()
        name = "verification-20261107T120000Z-" + "a" * 32 + ".tar"
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                transfer(root, name, digest, upload=True, stream=io.BytesIO(data[:-1]))
            self.assertEqual(list(root.iterdir()), [])
            transfer(root, name, digest, upload=True, stream=io.BytesIO(data))
            with self.assertRaises(FileExistsError):
                transfer(root, name, digest, upload=True, stream=io.BytesIO(data))
            (root / name).write_bytes(b"corrupt")
            output = io.BytesIO()
            with self.assertRaises(ValueError):
                transfer(root, name, digest, upload=False, stream=output)
            self.assertEqual(output.getvalue(), b"")
