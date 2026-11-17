import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest

import test_shopee as runner


@contextlib.contextmanager
def http_service(body, status=200):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name)
        self.report = runner.RunReport(self.output, 'test')
        self.args = SimpleNamespace(server_url=None, port=0, startup_timeout=0.3)

    def test_wrong_service_is_rejected(self):
        with http_service(b'{"ok":true,"service":"another-app"}') as service:
            self.args.server_url = f'http://127.0.0.1:{service.server_port}'
            with self.assertRaises(runner.DiagnosticError) as failure:
                with runner.Server(self.args, self.output, self.report):
                    self.fail('Wrong service was accepted')
        self.assertEqual(failure.exception.code, 'wrong_service')

    def test_busy_port_does_not_stop_other_service(self):
        with http_service(b'other application') as service:
            self.args.port = service.server_port
            with self.assertRaises(runner.DiagnosticError) as failure:
                with runner.Server(self.args, self.output, self.report):
                    self.fail('Busy port was accepted')
            self.assertEqual(failure.exception.code, 'port_in_use')
            import requests
            self.assertEqual(requests.get(f'http://127.0.0.1:{service.server_port}', timeout=2).text, 'other application')

    def test_invalid_json_has_specific_error(self):
        with http_service(b'<html>not JSON</html>') as service:
            server = runner.Server(self.args, self.output, self.report)
            with self.assertRaises(runner.DiagnosticError) as failure:
                server.probe(f'http://127.0.0.1:{service.server_port}')
        self.assertEqual(failure.exception.code, 'health_invalid_json')

    def test_existing_healthy_server_is_reused_and_kept_running(self):
        body = b'{"ok":true,"service":"captcha-so-pe-server1","diagnostics_version":2,"model_loaded":true}'
        with http_service(body) as service:
            self.args.port = service.server_port
            with runner.Server(self.args, self.output, self.report) as server:
                self.assertIsNone(server.process)
                self.assertEqual(server.url, f'http://127.0.0.1:{service.server_port}')
            import requests
            self.assertEqual(requests.get(server.url + '/health', timeout=2).status_code, 200)

    def test_missing_model_fails_before_loading_or_downloading(self):
        args = SimpleNamespace(model=str(self.output / 'missing.pt'))
        with self.assertRaises(runner.DiagnosticError) as failure:
            runner.local_yolo(args, self.output, self.report)
        self.assertEqual(failure.exception.code, 'model_not_found')

    def test_report_is_readable_before_test_finishes(self):
        self.report.stage('server_waiting', 'Waiting for startup')
        saved = json.loads((self.output / 'report.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['stage'], 'server_waiting')
        self.assertTrue(saved['updated_at'].endswith('+07:00'))

    def test_explicit_edge_path_is_resolved(self):
        executable = self.output / 'msedge.exe'
        executable.write_bytes(b'fixture')
        self.assertEqual(runner.resolve_edge_path(str(executable)), executable.resolve())

    def test_missing_edge_path_has_specific_error(self):
        with self.assertRaises(runner.DiagnosticError) as failure:
            runner.resolve_edge_path(str(self.output / 'missing-msedge.exe'))
        self.assertEqual(failure.exception.code, 'edge_not_found')


if __name__ == '__main__':
    unittest.main()
