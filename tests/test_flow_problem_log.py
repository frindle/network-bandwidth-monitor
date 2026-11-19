import contextlib
import io
import unittest
from unittest import mock

from app import firewalla


def flows_with(data):
    out = io.StringIO()
    with mock.patch.object(firewalla, '_ssh_curl', return_value=data), contextlib.redirect_stdout(out):
        flows = firewalla.get_flows(0, 1)
    return flows, out.getvalue()


class FlowProblemLog(unittest.TestCase):
    def setUp(self):
        firewalla._flow_problem_logged_at = 0.0

    def test_error_text_is_logged_once_an_hour(self):
        err = {'message': 'bad request', 'error': 'x' * 500}
        flows, out = flows_with(err)
        self.assertEqual(flows, [])
        self.assertIn('message=bad request', out)
        self.assertIn('error=' + 'x' * 200, out)
        self.assertNotIn('x' * 201, out)
        _, again = flows_with(err)
        self.assertEqual(again, '')

    def test_logs_again_after_an_hour(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(firewalla._log_flow_problem({'error': 'e'}, now=10_000.0))
            self.assertFalse(firewalla._log_flow_problem({'error': 'e'}, now=10_000.0 + 3599))
            self.assertTrue(firewalla._log_flow_problem({'error': 'e'}, now=10_000.0 + 3600))

    def test_empty_flows_list_is_not_a_problem(self):
        flows, out = flows_with({'flows': []})
        self.assertEqual((flows, out), ([], ''))

    def test_flows_pass_through(self):
        flows, out = flows_with({'flows': [{'ts': 1}]})
        self.assertEqual((flows, out), ([{'ts': 1}], ''))


if __name__ == '__main__':
    unittest.main()
