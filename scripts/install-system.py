#!/usr/bin/env python3
"""Install Overwatch as a system service for one non-root account."""

from __future__ import annotations

import argparse
import grp
import json
import os
import shutil
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
import venv

MANAGED_MARKER = "# Managed by Poto.si Overwatch install-system.py"
ROOT_MARKER = "Poto.si Overwatch system installation v1\n"
INSTALL_ROOT = Path("/opt/overwatch")
UNIT_PATH = Path("/etc/systemd/system/overwatch.service")
BIN_PATH = Path("/usr/local/bin/overwatch")


def quote_systemd(value: str) -> str:
    """Quote one systemd unit argument, including escapes and specifiers."""
    escaped = value.replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def render_unit(username: str, group: str, home: Path) -> str:
    executable = INSTALL_ROOT / "venv/bin/overwatch"
    command = [str(executable), "--codex-home", str(home / ".codex"),
               "--claude-home", str(home / ".claude")]
    return "\n".join((
        MANAGED_MARKER,
        "[Unit]",
        "Description=Poto.si Overwatch local agent observer",
        "After=network.target",
        "",
        "[Service]",
        "Type=exec",
        f"User={username}",
        f"Group={group}",
        "Environment=" + quote_systemd("HOME=" + str(home)),
        f"WorkingDirectory={INSTALL_ROOT}",
        "ExecStart=" + " ".join(quote_systemd(part) for part in command),
        "Restart=on-failure",
        "RestartSec=5",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ))


def resolve_account(username: str):
    try:
        account = pwd.getpwnam(username)
    except KeyError as exc:
        raise SystemExit(f"Unknown user: {username}") from exc
    if account.pw_uid == 0:
        raise SystemExit("--user must name a non-root account")
    home = Path(account.pw_dir)
    if not home.is_absolute() or not home.is_dir():
        raise SystemExit(f"The home directory for {username} is unavailable: {home}")
    return account, grp.getgrgid(account.pw_gid).gr_name, home


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def user_systemctl(account, home: Path, *arguments: str) -> None:
    runtime = Path("/run/user") / str(account.pw_uid)
    env = ["env", f"XDG_RUNTIME_DIR={runtime}",
           f"DBUS_SESSION_BUS_ADDRESS=unix:path={runtime}/bus", "systemctl", "--user", *arguments]
    run(["runuser", "-u", account.pw_name, "--", *env])


def write_unit(content: str, previous: bytes | None) -> None:
    temporary = UNIT_PATH.with_name(".overwatch.service.tmp")
    temporary.write_text(content)
    temporary.chmod(0o644)
    os.replace(temporary, UNIT_PATH)
    try:
        run(["systemd-analyze", "verify", str(UNIT_PATH)])
    except Exception:
        restore_unit(previous)
        raise


def restore_unit(previous: bytes | None) -> None:
    if previous is None:
        UNIT_PATH.unlink(missing_ok=True)
    else:
        UNIT_PATH.write_bytes(previous)
    run(["systemctl", "daemon-reload"])


def healthy() -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            for provider in ("codex", "claude"):
                with urlopen(f"http://127.0.0.1:8765/api/snapshot?provider={provider}", timeout=1) as response:
                    if response.status != 200:
                        raise ValueError("snapshot returned an error")
                    snapshot = json.load(response)
                    if snapshot.get("loading") is not False or snapshot.get("provider") != provider:
                        raise ValueError("snapshot is not ready")
            return True
        except (OSError, ValueError, URLError):
            time.sleep(0.25)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.environ.get("SUDO_USER"),
                        help="non-root account to observe (defaults to SUDO_USER)")
    parser.add_argument("--print-unit", action="store_true", help="print the generated unit without installing")
    args = parser.parse_args()
    if not args.user:
        parser.error("provide --user or run through sudo so SUDO_USER is set")
    account, group, home = resolve_account(args.user)
    unit = render_unit(account.pw_name, group, home)
    if args.print_unit:
        print(unit, end="")
        return 0
    if os.geteuid() != 0:
        parser.error("installation requires root; use sudo or run --print-unit")

    repository = Path(__file__).resolve().parent.parent
    if not (repository / "pyproject.toml").is_file():
        raise SystemExit(f"Cannot find the Overwatch project at {repository}")
    marker = INSTALL_ROOT / ".managed-by-overwatch"
    if INSTALL_ROOT.exists() and (not marker.is_file() or marker.read_text() != ROOT_MARKER):
        raise SystemExit(f"Refusing to modify unrelated installation directory: {INSTALL_ROOT}")
    if UNIT_PATH.is_symlink() or (UNIT_PATH.exists() and MANAGED_MARKER not in UNIT_PATH.read_text(errors="replace")):
        raise SystemExit(f"Refusing to overwrite unrelated system unit: {UNIT_PATH}")
    expected_binary = INSTALL_ROOT / "venv/bin/overwatch"
    if BIN_PATH.exists() or BIN_PATH.is_symlink():
        if not BIN_PATH.is_symlink() or BIN_PATH.resolve() != expected_binary:
            raise SystemExit(f"Refusing to overwrite unrelated command: {BIN_PATH}")

    INSTALL_ROOT.mkdir(mode=0o755, parents=True, exist_ok=True)
    marker.write_text(ROOT_MARKER)
    environment = INSTALL_ROOT / "venv"
    if not (environment / "bin/python").exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    with tempfile.TemporaryDirectory(prefix="overwatch-install-") as temporary:
        source = Path(temporary) / "source"
        source.mkdir()
        shutil.copy2(repository / "pyproject.toml", source / "pyproject.toml")
        shutil.copy2(repository / "README.md", source / "README.md")
        shutil.copytree(repository / "src", source / "src",
                        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
        run([str(environment / "bin/python"), "-m", "pip", "install", "--upgrade", "--no-deps", str(source)])
    if not expected_binary.is_file():
        raise SystemExit("The package install did not create the overwatch command")
    run([str(expected_binary), "--help"])
    run([str(environment / "bin/python"), "-c",
         "from importlib.resources import files; assert all(files('overwatch').joinpath('web').joinpath(p).is_file() for p in ('index.html', 'style.css', 'app.js', 'theme.js', 'theme.css', 'assets/inter-tight-800.ttf', 'assets/openai.svg', 'assets/claude.svg'))"])

    if not BIN_PATH.is_symlink():
        temporary_link = BIN_PATH.with_name(".overwatch.tmp")
        temporary_link.unlink(missing_ok=True)
        temporary_link.symlink_to(expected_binary)
        os.replace(temporary_link, BIN_PATH)

    old_user_unit = home / ".config/systemd/user/overwatch.service"
    migrate_user_service = old_user_unit.is_file()
    previous_unit = UNIT_PATH.read_bytes() if UNIT_PATH.exists() else None
    write_unit(unit, previous_unit)
    try:
        run(["systemctl", "daemon-reload"])
    except Exception:
        restore_unit(previous_unit)
        raise
    try:
        if migrate_user_service:
            user_systemctl(account, home, "stop", "overwatch.service")
        run(["systemctl", "enable", "--now", "overwatch.service"])
        run(["systemctl", "restart", "overwatch.service"])
        run(["systemctl", "is-active", "--quiet", "overwatch.service"])
        if not healthy():
            raise RuntimeError("the Codex and Claude snapshot endpoints did not become ready")
    except Exception as exc:
        subprocess.run(["systemctl", "disable", "--now", "overwatch.service"], check=False)
        if migrate_user_service:
            subprocess.run(["runuser", "-u", account.pw_name, "--", "env",
                            f"XDG_RUNTIME_DIR=/run/user/{account.pw_uid}",
                            f"DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{account.pw_uid}/bus",
                            "systemctl", "--user", "start", "overwatch.service"], check=False)
        previous = "restart of the old user service was attempted" if migrate_user_service else "no old user service was found"
        raise SystemExit(f"System service verification failed; {previous}: {exc}") from exc

    if migrate_user_service:
        user_systemctl(account, home, "disable", "overwatch.service")
        old_user_unit.unlink()
        user_systemctl(account, home, "daemon-reload")
    print("Overwatch is active as a system service at http://127.0.0.1:8765")
    return 0


if __name__ == "__main__":
    sys.exit(main())
