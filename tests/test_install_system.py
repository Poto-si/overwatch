"""Installer handoff tests; all system paths and commands are isolated."""

import importlib.util
from contextlib import redirect_stdout
import grp
from io import StringIO
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "install-system.py"
SPEC = importlib.util.spec_from_file_location("overwatch_install_system", SCRIPT_PATH)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class SystemInstallHandoffTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.home = self.root / "home" / "alice"
        self.home.mkdir(parents=True)
        self.install_root = self.root / "opt" / "overwatch"
        self.unit_path = self.root / "systemd" / "overwatch.service"
        self.bin_path = self.root / "bin" / "overwatch"
        self.unit_path.parent.mkdir(parents=True)
        self.bin_path.parent.mkdir(parents=True)
        self.old_unit = self.home / ".config/systemd/user/overwatch.service"
        self.old_unit.parent.mkdir(parents=True)
        self.old_unit.write_text("[Service]\nExecStart=old\n")
        self.account = SimpleNamespace(pw_name="alice", pw_uid=1234, pw_gid=1234)
        self.expected_binary = self.install_root / "venv/bin/overwatch"
        self.patches = [
            mock.patch.object(installer, "INSTALL_ROOT", self.install_root),
            mock.patch.object(installer, "UNIT_PATH", self.unit_path),
            mock.patch.object(installer, "BIN_PATH", self.bin_path),
            mock.patch.object(installer, "resolve_account", return_value=(self.account, "alice", self.home)),
            mock.patch.object(installer.os, "geteuid", return_value=0),
            mock.patch.object(installer, "user_systemctl"),
            mock.patch.object(installer.subprocess, "run"),
            mock.patch.object(installer, "run"),
            mock.patch.object(installer.venv, "EnvBuilder"),
            mock.patch("sys.argv", [str(SCRIPT_PATH), "--user", "alice"]),
        ]
        self.mocks = [patcher.start() for patcher in self.patches]
        self.addCleanup(self.cleanup_patches)
        self.env_builder = self.mocks[8]

        def create_environment(path):
            bin_dir = Path(path) / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            (bin_dir / "python").touch()
            (bin_dir / "overwatch").touch()

        self.env_builder.return_value.create.side_effect = create_environment

    def cleanup_patches(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.temporary.cleanup()

    def test_old_user_service_is_removed_only_after_new_service_is_healthy(self):
        events = []
        installer.run.side_effect = lambda command: None
        installer.user_systemctl.side_effect = lambda account, home, *args: events.append(
            (args[0], self.old_unit.exists()))

        def healthy():
            events.append(("health", self.old_unit.exists()))
            return True

        with mock.patch.object(installer, "healthy", side_effect=healthy), redirect_stdout(StringIO()):
            self.assertEqual(installer.main(), 0)

        self.assertEqual(events[0], ("stop", True))
        self.assertIn(("health", True), events)
        disable_index = next(i for i, event in enumerate(events) if event[0] == "disable")
        health_index = next(i for i, event in enumerate(events) if event[0] == "health")
        self.assertGreater(disable_index, health_index)
        self.assertFalse(self.old_unit.exists())
        self.assertTrue(self.unit_path.is_file())

    def test_unhealthy_system_service_retains_and_restarts_old_user_service(self):
        with mock.patch.object(installer, "healthy", return_value=False):
            with self.assertRaises(SystemExit):
                installer.main()

        self.assertTrue(self.old_unit.is_file())
        commands = [call.args[0] for call in installer.subprocess.run.call_args_list]
        self.assertTrue(any("disable" in command for command in commands))
        self.assertTrue(any("start" in command and "--user" in command for command in commands))
        self.assertNotIn("disable", [call.args[2] for call in installer.user_systemctl.call_args_list])

    def test_unmanaged_system_unit_is_rejected_before_installation(self):
        self.unit_path.write_text("[Unit]\nDescription=Some other service\n")

        with self.assertRaises(SystemExit):
            installer.main()

        installer.run.assert_not_called()
        self.assertTrue(self.old_unit.is_file())
        self.assertEqual(self.unit_path.read_text(), "[Unit]\nDescription=Some other service\n")


@unittest.skipUnless(shutil.which("systemd-analyze"), "systemd-analyze is unavailable")
class SystemdUnitValidationTests(unittest.TestCase):
    def test_rendered_unit_passes_systemd_analyze_verify(self):
        account = pwd.getpwuid(os.getuid())
        if account.pw_uid == 0:
            self.skipTest("requires a non-root account")
        group = grp.getgrgid(account.pw_gid).gr_name
        with tempfile.TemporaryDirectory(prefix="overwatch-unit-") as temporary:
            install_root = Path(temporary) / "opt" / "overwatch"
            executable = install_root / "venv/bin/overwatch"
            executable.parent.mkdir(parents=True)
            install_root.mkdir(parents=True, exist_ok=True)
            executable.symlink_to(sys.executable)
            unit_path = Path(temporary) / "overwatch.service"
            with mock.patch.object(installer, "INSTALL_ROOT", install_root):
                unit_path.write_text(installer.render_unit(account.pw_name, group, Path(account.pw_dir)))

            result = subprocess.run(
                ["systemd-analyze", "verify", str(unit_path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
