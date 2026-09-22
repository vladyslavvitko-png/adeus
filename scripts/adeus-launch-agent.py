#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import plistlib
import socket
import subprocess
import sys
import time
from pathlib import Path

LABEL = "com.adeus.scheduler"
ROOT = Path(__file__).resolve().parent.parent
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = ROOT / "adeus-launch-agent.log"
PYTHON_PATH = ROOT / ".venv" / "bin" / "python"
DOMAIN = f"gui/{os.getuid()}"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _loaded() -> bool:
    return _run("print", f"{DOMAIN}/{LABEL}").returncode == 0


def _port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", 8000)) == 0


def _unload() -> None:
    result = _run("bootout", DOMAIN, str(PLIST_PATH))
    if result.returncode != 0:
        _run("unload", str(PLIST_PATH))


def install() -> None:
    if sys.platform != "darwin":
        raise SystemExit("This installer only supports macOS.")
    if not PYTHON_PATH.exists():
        raise SystemExit("Create .venv and install requirements before installing the service.")
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    if _loaded():
        _unload()
        time.sleep(0.5)
    if _port_in_use():
        raise SystemExit(
            "Port 8000 is already in use. Stop the manually started Adeus server, "
            "then run install again."
        )
    payload = {
        "Label": LABEL,
        "ProgramArguments": [
            str(PYTHON_PATH),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(LOG_PATH),
        "StandardErrorPath": str(LOG_PATH),
    }
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    result = _run("bootstrap", DOMAIN, str(PLIST_PATH))
    if result.returncode != 0:
        result = _run("load", "-w", str(PLIST_PATH))
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "launchctl could not load Adeus.")
    print(f"Installed and started {LABEL}.")
    print("Adeus will now start at login and keep the daily scheduler running.")


def uninstall() -> None:
    if _loaded() or PLIST_PATH.exists():
        _unload()
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print(f"Removed {LABEL}.")


def status() -> None:
    installed = PLIST_PATH.exists()
    loaded = _loaded()
    print(f"Installed: {'yes' if installed else 'no'}")
    print(f"Loaded: {'yes' if loaded else 'no'}")
    print(f"Plist: {PLIST_PATH}")
    print(f"Log: {LOG_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Install or remove the Adeus macOS background service."
    )
    parser.add_argument("command", choices=("install", "uninstall", "status"))
    args = parser.parse_args()
    {"install": install, "uninstall": uninstall, "status": status}[args.command]()


if __name__ == "__main__":
    main()
