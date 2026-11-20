from pathlib import Path

from winagent import __main__ as cli
from winagent.connect_window import BAD_CODE_MESSAGE, NO_SERVER_MESSAGE, friendly_error
from winagent.http_client import HttpError, TransientError
from winagent.registration import RegistrationError


def test_a_rejected_code_gets_the_bad_code_message():
    assert friendly_error(HttpError(400, "invalid_registration_code")) == BAD_CODE_MESSAGE


def test_a_network_failure_gets_the_server_message():
    assert friendly_error(HttpError(None, "connection")) == NO_SERVER_MESSAGE
    assert friendly_error(TransientError(None, "timeout")) == NO_SERVER_MESSAGE


def test_a_short_code_message_is_shown_as_it_is():
    message = "registration code is missing or too short"
    assert friendly_error(RegistrationError(message)) == message


def test_autostart_starts_the_packaged_program_with_run(monkeypatch):
    monkeypatch.setattr(cli.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cli.sys, "executable", r"C:\WinAgent\winagent.exe")
    command = cli._autostart_command(Path("config.toml"))
    assert command.startswith('"C:\\WinAgent\\winagent.exe" run --config "')
    assert command.endswith('config.toml"')


def test_autostart_uses_module_mode_when_run_from_source(monkeypatch):
    monkeypatch.setattr(cli.sys, "frozen", False, raising=False)
    monkeypatch.setattr(cli.sys, "executable", r"C:\Python\python.exe")
    command = cli._autostart_command(Path("config.toml"))
    assert '-m winagent run --config "' in command


def test_autostart_source_run_uses_pythonw_so_no_console_opens(monkeypatch, tmp_path):
    fake_python = tmp_path / "python.exe"
    fake_python.write_bytes(b"")
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(cli.sys, "frozen", False, raising=False)
    monkeypatch.setattr(cli.sys, "executable", str(fake_python))
    command = cli._autostart_command(Path("config.toml"))
    assert command.startswith(f'"{tmp_path / "pythonw.exe"}" -m winagent run')


def test_running_with_no_command_uses_the_default_config_for_setup():
    args = cli._build_parser().parse_args([])
    assert args.command is None  # main() turns this into the employee setup
    assert args.config == cli._default_config()
