import importlib.util
import pathlib
import sys
import unittest

SOURCE = pathlib.Path('/Users/sufeyanwaedueraman/.hermes/hermes-agent/hermes_cli/approval_transport.py')
spec = importlib.util.spec_from_file_location('discovered_approval_transport', SOURCE)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ApprovalTransportProof(unittest.TestCase):
    def request(self):
        return module.ApprovalRequest.create(
            command='fixture-only: no command is run', description='test fixture',
            pattern_key='fixture', pattern_keys=('fixture',), session_key='fixture-session',
            surface='test', allow_session=False, allow_permanent=False, timeout_seconds=0.2,
        )

    def invoke(self, callback, req=None, timeout=0.2):
        req = req or self.request()
        return module.invoke_approval_transport(callback, req, timeout_seconds=timeout, poll_interval=0.005)

    def test_correlated_once_and_deny(self):
        req = self.request()
        self.assertEqual(self.invoke(lambda r: r.respond('once'), req).choice, 'once')
        self.assertEqual(self.invoke(lambda r: r.respond('deny'), req).choice, 'deny')

    def test_expired(self):
        req = self.request()
        result = self.invoke(lambda r: (module.time.sleep(0.04), r.respond('once'))[1], req, timeout=0.01)
        self.assertEqual(result.choice, 'deny')
        self.assertEqual(result.failure, 'timeout')

    def test_wrong_digest_and_request_id(self):
        req = self.request()
        wrong_digest = lambda r: module.ApprovalDecision(r.request_id, '0' * 64, 'once')
        wrong_id = lambda r: module.ApprovalDecision('wrong-id', r.digest, 'once')
        for callback in (wrong_digest, wrong_id):
            result = self.invoke(callback, req)
            self.assertEqual(result.choice, 'deny')
            self.assertEqual(result.failure, 'stale')

    def test_callback_exception_fails_closed(self):
        def broken(_):
            raise RuntimeError('fixture error')
        result = self.invoke(broken)
        self.assertEqual(result.choice, 'deny')
        self.assertEqual(result.failure, 'error')


if __name__ == '__main__':
    unittest.main()
