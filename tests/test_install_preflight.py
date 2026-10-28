import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/ai-support'))
from install_preflight import require_stopped


class InstallPreflightTest(unittest.TestCase):
    def test_catalog_status_does_not_need_detail(self):
        fetch = Mock()
        require_stopped([{'status': 'stopped'}, {'status': 'uninitialized'}], fetch)
        fetch.assert_not_called()

    def test_old_catalog_uses_detail(self):
        fetch = Mock(return_value={'id': 'app-id', 'status': 'stopped'})
        require_stopped([{'id': 'app-id'}], fetch)
        fetch.assert_called_once_with('app-id')

    def test_running_examples_also_block_shared_service_changes(self):
        with self.assertRaises(ValueError):
            require_stopped([{'example': True, 'status': 'running'}])

    def test_unknown_status_and_wrong_identity_fail_closed(self):
        for detail in ({'id': 'app-id'}, {'id': 'other', 'status': 'stopped'},
                       {'id': 'app-id', 'status': 'starting'}):
            with self.subTest(detail=detail), self.assertRaises(ValueError):
                require_stopped([{'id': 'app-id'}], Mock(return_value=detail))

    def test_unavailable_detail_fails_closed(self):
        with self.assertRaises(OSError):
            require_stopped([{'id': 'app-id'}], Mock(side_effect=OSError('offline')))
