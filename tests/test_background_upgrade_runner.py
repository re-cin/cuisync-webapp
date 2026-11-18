"""Offline checks for Unicode logs and resumable batch progress."""
import io
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from admin_data.long_background_upgrade import configure_output
from upgrade_background_sources import run


class BackgroundUpgradeRunnerTests(unittest.TestCase):
    def test_redirected_legacy_console_handles_unicode_titles(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding='cp1252')
        with patch('sys.stdout', stream), patch('sys.stderr', stream):
            configure_output()
            print('Şiş kebabı — 한국')
            self.assertEqual(buffer.getvalue().decode('utf-8').splitlines(), ['Şiş kebabı — 한국'])
        stream.detach()

    def test_completed_write_is_checkpointed_before_diagnostic_logging(self):
        recipe = {'title': 'Şiş kebabı', 'country': 'TURKIYE', 'recipe_id': 1}
        store = Mock()
        store.get.return_value = {'verification_status': 'pending', 'updated_at': 'old'}
        progress = Mock()
        with patch('dish_backgrounds.enrich_background', return_value={'updated_at': 'new'}), \
                patch('builtins.print', side_effect=UnicodeEncodeError('ascii', 'Ş', 0, 1, 'test')):
            with self.assertRaises(UnicodeEncodeError):
                run(store, [recipe], apply=True, delay=0, diagnose=True, progress=progress)
        progress.assert_called_once_with(1, 1, {'updated': 1})


if __name__ == '__main__':
    unittest.main()
