"""Command line entry point: register, run, status, autostart (TAD 7).

Usage (see README.md):
  winagent register --config agent.toml --code <REGISTRATION_CODE>
  winagent run --config agent.toml
  winagent status --config agent.toml
  winagent autostart enable|disable --config agent.toml
  winagent unregister --config agent.toml
"""

from __future__ import annotations

import argparse
import logging
import platform
import sys
import threading
from pathlib import Path

from winagent import __version__
from winagent.config import ConfigError, load_config
from winagent.credentials import CredentialStore
from winagent.health import HealthSnapshot, read_health, status_line, write_health
from winagent.http_client import HttpError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="winagent", description="Windows Desktop Monitoring Agent (POC)")
    parser.set_defaults(config=_default_config())
    sub = parser.add_subparsers(dest="command", required=False)

    reg = sub.add_parser("register", help="Register this device with an organization registration code")
    reg.add_argument("--config", required=True, type=Path)
    reg.add_argument("--code", help="Registration code (default: [registration] code in the config file)")

    run = sub.add_parser("run", help="Run the monitoring agent in the foreground")
    run.add_argument("--config", required=True, type=Path)

    status = sub.add_parser("status", help="Print the last health snapshot")
    status.add_argument("--config", required=True, type=Path)

    auto = sub.add_parser("autostart", help="Enable or disable start at sign-in (per user, no admin)")
    auto.add_argument("action", choices=["enable", "disable"])
    auto.add_argument("--config", required=True, type=Path)

    unreg = sub.add_parser("unregister", help="Remove the local device credential")
    unreg.add_argument("--config", required=True, type=Path)

    connect = sub.add_parser("connect", help="Employee setup: paste the dashboard code in a window, then run")
    connect.add_argument("--config", type=Path, default=_default_config())
    return parser


def _default_config() -> Path:
    """config.toml next to winagent.exe when packaged (a double-click starts in that folder), else in the current folder."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "config.toml"
    return Path("config.toml")


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    # No command at all (for example a double-click on winagent.exe) runs the agent. A PC with no
    # credential pairs itself: the employee approves it on their dashboard, with no code to type.
    if args.command is None:
        args.command = "run"
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "status":
        snap = read_health(cfg.health_path)
        if snap is None:
            print("no health data yet")
            return 1
        print(status_line(snap))
        return 0

    if args.command == "unregister":
        CredentialStore(cfg.credential_path, _protector()).clear()
        print("local credential removed")
        return 0

    if args.command == "autostart":
        from winagent.platform_win import WinregAutostart

        reg = WinregAutostart()
        if args.action == "enable":
            reg.enable(_autostart_command(args.config))
            print("autostart enabled for this user")
        else:
            reg.disable()
            print("autostart disabled for this user")
        return 0

    from winagent.logging_setup import configure_logging

    configure_logging(cfg.log_dir, __version__)
    if args.command == "register":
        from winagent.config import load_registration_code

        code = args.code or load_registration_code(args.config)
        if not code:
            print("no registration code: pass --code or set [registration] code in the config", file=sys.stderr)
            return 2
        error = _try_register(cfg, code)
        if error:
            print(f"registration failed: {error}", file=sys.stderr)
            return 1
        print("device registered")
        return 0
    if args.command == "connect":
        return _connect(cfg, args.config)
    return _run(cfg, args.config)


def _autostart_command(config: Path) -> str:
    """The command the Run key starts at sign-in. The config path is absolute, since the start folder is not known.

    Sign-in must not open a console window, so a source run uses pythonw.exe (same as python.exe, no console).
    The packaged winagent.exe is built with --windowed (see build.ps1) for the same reason.
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" run --config "{config.resolve()}"'
    return f'"{_windowless_python()}" -m winagent run --config "{config.resolve()}"'


def _windowless_python() -> str:
    """pythonw.exe next to python.exe when it exists; otherwise the interpreter as it is."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    return str(pythonw) if exe.name.lower() == "python.exe" and pythonw.exists() else str(exe)


def _protector():
    from winagent.platform_win import DpapiProtector

    return DpapiProtector()


def _try_register(cfg, code: str) -> str | None:
    """Register this device. Returns None when it worked, or a short message when it did not."""
    from winagent.api_client import AgentApi
    from winagent.connect_window import friendly_error
    from winagent.http_client import HttpClient
    from winagent.registration import RegistrationError, register_device

    store = CredentialStore(cfg.credential_path, _protector())
    api = AgentApi(HttpClient(cfg.api.base_url, cfg.api.timeout_seconds, lambda: None, ca_bundle=cfg.api.ca_bundle))
    try:
        register_device(api, store, code, platform.node() or "device", platform.platform(), __version__)
    except (RegistrationError, HttpError) as exc:
        logging.getLogger("winagent").warning("registration failed: %s", type(exc).__name__)
        return friendly_error(exc)
    return None


def _connect(cfg, config: Path) -> int:
    """Employee path: no credential yet means show the paste-code window first. Then turn on start-at-sign-in and run."""
    from winagent.connect_window import ask_for_code
    from winagent.platform_win import WinregAutostart

    store = CredentialStore(cfg.credential_path, _protector())
    if store.load() is None:
        if not ask_for_code(lambda code: _try_register(cfg, code)):
            return 1
        WinregAutostart().enable(_autostart_command(config))
    return _run(cfg, config)


def _pair_this_pc(cfg, config: Path) -> bool:
    """Connect this PC through the employee's dashboard approval. Turns on start-at-sign-in once connected."""
    import webbrowser

    from winagent.api_client import AgentApi
    from winagent.http_client import HttpClient
    from winagent.pairing import DASHBOARD_MARKER, dashboard_url, open_dashboard_once, pair_this_pc
    from winagent.platform_win import WinregAutostart

    store = CredentialStore(cfg.credential_path, _protector())
    api = AgentApi(HttpClient(cfg.api.base_url, cfg.api.timeout_seconds, lambda: None, ca_bundle=cfg.api.ca_bundle))
    if not cfg.api.org_token:
        logging.getLogger("winagent").error("config has no enrollment token; reinstall WinAgent with /ORGTOKEN")
        return False
    details = {
        "device_name": platform.node() or "device",
        "os_version": platform.platform(),
        "agent_version": __version__,
        "org_token": cfg.api.org_token,
    }
    stop = threading.Event()
    marker = cfg.data_dir / DASHBOARD_MARKER
    paired = pair_this_pc(
        api,
        store,
        details,
        dashboard_url(cfg.api.base_url),
        stop_requested=stop.is_set,
        wait=stop.wait,
        # Once per install or pairing, even across restarts and sign-ins.
        open_browser=lambda url: open_dashboard_once(url, marker, webbrowser.open),
    )
    if paired:
        marker.unlink(missing_ok=True)  # a later pairing (after a revoke) opens the dashboard once again
        WinregAutostart().enable(_autostart_command(config))
    return paired


def _run(cfg, config: Path) -> int:
    """Run the agent. If the server rejects its credential, forget it and connect again, without a reinstall."""
    from winagent.bootstrap import build_agent

    store = CredentialStore(cfg.credential_path, _protector())
    stop = threading.Event()
    tray_started = False
    agent = None
    while not stop.is_set():
        if store.load() is None:
            write_health(cfg.health_path, HealthSnapshot(agent_version=__version__, monitoring_state="awaiting_pairing"))
            if not _pair_this_pc(cfg, config):
                return 1
        agent, tray_hook = build_agent(cfg, __version__)
        if tray_hook is not None and not tray_started:
            tray_hook(stop, lambda: agent.health)
            tray_started = True
        try:
            agent.run_forever(stop)
        except KeyboardInterrupt:
            stop.set()
        if stop.is_set():
            break
        logging.getLogger("winagent").info("credential rejected; connecting this PC again")
        store.clear()
    logging.getLogger("winagent").info("shutdown complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
