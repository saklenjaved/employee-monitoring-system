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
from winagent.health import read_health, status_line
from winagent.http_client import HttpError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="winagent", description="Windows Desktop Monitoring Agent (POC)")
    sub = parser.add_subparsers(dest="command", required=True)

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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
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
            reg.enable(f'"{sys.executable}" -m winagent run --config "{args.config.resolve()}"')
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
        return _register(cfg, code)
    return _run(cfg)


def _protector():
    from winagent.platform_win import DpapiProtector

    return DpapiProtector()


def _register(cfg, code: str) -> int:
    from winagent.api_client import AgentApi
    from winagent.http_client import HttpClient
    from winagent.registration import RegistrationError, register_device

    store = CredentialStore(cfg.credential_path, _protector())
    api = AgentApi(HttpClient(cfg.api.base_url, cfg.api.timeout_seconds, lambda: None, ca_bundle=cfg.api.ca_bundle))
    try:
        register_device(api, store, code, platform.node() or "device", platform.platform(), __version__)
    except (RegistrationError, HttpError) as exc:
        print(f"registration failed: {exc}", file=sys.stderr)
        return 1
    print("device registered")
    return 0


def _run(cfg) -> int:
    from winagent.bootstrap import build_agent

    agent, tray_hook = build_agent(cfg, __version__)
    stop = threading.Event()
    if tray_hook is not None:
        tray_hook(stop, lambda: agent.health)
    try:
        agent.run_forever(stop)
    except KeyboardInterrupt:
        stop.set()
    logging.getLogger("winagent").info("shutdown complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
