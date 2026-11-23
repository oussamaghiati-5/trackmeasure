"""A downloaded ranking must have a stable permanent list ID."""
import unittest
from unittest.mock import patch

from scripts import check_tranco


class TrancoDownload(unittest.TestCase):
    def test_download_records_matching_id(self):
        with patch.object(check_tranco, "_get", side_effect=[b"ABCDE\n", b"zip bytes", b"ABCDE\n"]):
            self.assertEqual(check_tranco.download_tranco(), ("ABCDE", b"zip bytes"))

    def test_download_rejects_list_change(self):
        with patch.object(check_tranco, "_get", side_effect=[b"ABCDE\n", b"zip bytes", b"FGHIJ\n"]):
            with self.assertRaisesRegex(RuntimeError, "changed during download"):
                check_tranco.download_tranco()


if __name__ == "__main__":
    unittest.main()
