"""Command-line binding and validation checks for the local service."""

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch
import unittest

from overwatch import cli


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        output = StringIO()
        with patch.object(cli, "ThreadingHTTPServer") as server, patch.object(cli.threading, "Thread") as threads:
            server.return_value.serve_forever.side_effect = KeyboardInterrupt
            with patch("sys.argv", ["overwatch", "--demo", *arguments]), redirect_stdout(output):
                cli.main()
        return server, threads, output.getvalue()

    def test_default_host_binds_loopback_and_keeps_local_startup_url(self):
        server, threads, output = self.run_cli()
        self.assertEqual(server.call_args.args[0], ("127.0.0.1", 8765))
        self.assertIn("Poto.si | Overwatch: http://127.0.0.1:8765", output)
        self.assertEqual(threads.return_value.start.call_count, 2)
        server.return_value.server_close.assert_called_once()

    def test_explicit_all_interfaces_host_changes_bind_address(self):
        server, _, output = self.run_cli("--host", "0.0.0.0", "--port", "8123")
        self.assertEqual(server.call_args.args[0], ("0.0.0.0", 8123))
        self.assertIn("http://127.0.0.1:8123", output)

    def test_invalid_host_and_port_are_rejected_before_server_creation(self):
        for arguments in (("--host", "192.0.2.1"), ("--port", "65536")):
            with self.subTest(arguments=arguments):
                with patch.object(cli, "ThreadingHTTPServer") as server, patch("sys.argv", ["overwatch", *arguments]), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    with self.assertRaises(SystemExit) as raised:
                        cli.main()
                    self.assertEqual(raised.exception.code, 2)
                    server.assert_not_called()


if __name__ == "__main__":
    unittest.main()
