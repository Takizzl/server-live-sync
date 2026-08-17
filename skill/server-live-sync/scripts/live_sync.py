#!/usr/bin/env python3
"""Configure a one-way Syncthing project mirror over an existing SSH connection."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


MIN_PYTHON = (3, 9)
DEFAULT_CONFIG = Path.home() / ".server-live-sync" / "config.json"
PROJECT_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SSH_HOST = re.compile(r"^[A-Za-z0-9_.@:\[\]-]+$")


class SyncError(RuntimeError):
    pass


def run(command: list[str], *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SyncError(f"Required command not found: {command[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise SyncError(f"Command timed out: {shlex.join(command)}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no error output"
        raise SyncError(f"Command failed ({result.returncode}): {shlex.join(command)}\n{detail}")
    return result


def ssh(host: str, command: str, *, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return run(["ssh", host, command], check=check, timeout=timeout)


def local_st(args: Iterable[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["syncthing", "cli", *args], check=check)


def remote_st(host: str, args: Iterable[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    command = shlex.join(["syncthing", "cli", *args])
    return ssh(host, command, check=check)


def parse_json(text: str, context: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SyncError(f"Invalid JSON from {context}: {exc}") from exc
    if not isinstance(value, dict):
        raise SyncError(f"Unexpected JSON type from {context}")
    return value


def validate_project(project: str) -> PurePosixPath:
    candidate = PurePosixPath(project)
    if candidate.is_absolute() or not candidate.parts:
        raise SyncError("Project must be a relative path below the remote root.")
    if any(part in ("", ".", "..") or not PROJECT_SEGMENT.fullmatch(part) for part in candidate.parts):
        raise SyncError("Project segments may contain only letters, digits, dot, underscore, and hyphen; traversal is forbidden.")
    return candidate


def validate_host(host: str) -> str:
    if host.startswith("-") or not SSH_HOST.fullmatch(host):
        raise SyncError("SSH host must be an alias, hostname, IP address, or user@host without shell characters.")
    return host


def validate_remote_root(remote_root: str) -> PurePosixPath:
    root = PurePosixPath(remote_root)
    if not root.is_absolute() or str(root) in ("/", "/home", "/Users") or ".." in root.parts:
        raise SyncError("Remote root must be an explicit absolute project root, not a broad system or home parent.")
    return root


def slug(value: str) -> str:
    result = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (result or "project")[:36]


def make_folder_id(host: str, remote_path: str, project: str) -> str:
    digest = hashlib.sha256(f"{host}\0{remote_path}".encode()).hexdigest()[:8]
    return f"sls-{slug(PurePosixPath(project).name)}-{digest}"


def list_keys(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def get_local_folder(folder_id: str) -> dict[str, Any]:
    result = local_st(["config", "folders", folder_id, "dump-json"])
    return parse_json(result.stdout, f"local folder {folder_id}")


def get_remote_folder(host: str, folder_id: str) -> dict[str, Any]:
    result = remote_st(host, ["config", "folders", folder_id, "dump-json"])
    return parse_json(result.stdout, f"remote folder {folder_id}")


def folder_for_path(host: str | None, path: str) -> tuple[str, dict[str, Any]] | None:
    result = remote_st(host, ["config", "folders", "list"]) if host else local_st(["config", "folders", "list"])
    for folder_id in list_keys(result):
        folder = get_remote_folder(host, folder_id) if host else get_local_folder(folder_id)
        if str(folder.get("path", "")) == path:
            return folder_id, folder
    return None


def ensure_prerequisites(host: str) -> tuple[dict[str, Any], dict[str, Any]]:
    missing = [name for name in ("ssh", "scp", "syncthing") if shutil.which(name) is None]
    if missing:
        raise SyncError("Missing local prerequisites: " + ", ".join(missing))
    probe = ssh(host, "command -v syncthing >/dev/null && printf ok", check=False)
    if probe.returncode != 0 or probe.stdout.strip() != "ok":
        raise SyncError("Remote Syncthing is missing or SSH access failed. Run the doctor command for details.")
    local_result = local_st(["show", "system"], check=False)
    if local_result.returncode != 0:
        raise SyncError("Local Syncthing is not running. Start it, then retry.")
    remote_result = remote_st(host, ["show", "system"], check=False)
    if remote_result.returncode != 0:
        raise SyncError("Remote Syncthing is not running. Start its user service, then retry.")
    return parse_json(local_result.stdout, "local Syncthing"), parse_json(remote_result.stdout, "remote Syncthing")


def add_device_local(device_id: str, name: str) -> None:
    if device_id not in list_keys(local_st(["config", "devices", "list"])):
        local_st(["config", "devices", "add", f"--device-id={device_id}", f"--name={name}"])


def add_device_remote(host: str, device_id: str, name: str) -> None:
    if device_id not in list_keys(remote_st(host, ["config", "devices", "list"])):
        remote_st(host, ["config", "devices", "add", f"--device-id={device_id}", f"--name={name}"])


def add_folder_device_local(folder_id: str, device_id: str) -> None:
    keys = list_keys(local_st(["config", "folders", folder_id, "devices", "list"]))
    if device_id not in keys:
        local_st(["config", "folders", folder_id, "devices", "add", f"--device-id={device_id}"])


def add_folder_device_remote(host: str, folder_id: str, device_id: str) -> None:
    keys = list_keys(remote_st(host, ["config", "folders", folder_id, "devices", "list"]))
    if device_id not in keys:
        remote_st(host, ["config", "folders", folder_id, "devices", "add", f"--device-id={device_id}"])


def install_default_ignore(host: str, remote_path: str, ignore_file: Path) -> str:
    target = f"{remote_path}/.stignore"
    exists = ssh(host, f"test -e {shlex.quote(target)}", check=False)
    if exists.returncode == 0:
        return "preserved-existing"
    result = run(["scp", str(ignore_file), f"{host}:{shlex.quote(target)}"], check=False, timeout=120)
    if result.returncode != 0:
        raise SyncError(f"Could not install remote ignore file: {result.stderr.strip()}")
    return "installed-default"


def ensure_local_marker(local_path: str, marker_name: str = ".stfolder") -> Path:
    folder = Path(local_path)
    folder.mkdir(parents=True, exist_ok=True)
    marker = folder / marker_name
    if not marker.exists():
        marker.mkdir()
    return marker


def enable_startup_local() -> str:
    system = platform.system()
    executable = shutil.which("syncthing")
    if not executable:
        return "missing-syncthing"
    if system == "Windows":
        appdata = os.environ.get("APPDATA")
        if not appdata:
            return "windows-startup-unavailable"
        startup = Path(appdata) / "Microsoft/Windows/Start Menu/Programs/Startup"
        startup.mkdir(parents=True, exist_ok=True)
        vbs = startup / "Start-Syncthing.vbs"
        if not vbs.exists():
            content = 'Set shell = CreateObject("WScript.Shell")\r\nshell.Run """' + executable + '""", 0, False\r\n'
            vbs.write_text(content, encoding="utf-8")
        return str(vbs)
    if system == "Linux" and shutil.which("systemctl"):
        result = run(["systemctl", "--user", "enable", "--now", "syncthing.service"], check=False)
        return "systemd-enabled" if result.returncode == 0 else "systemd-enable-failed"
    if system == "Darwin" and shutil.which("brew"):
        result = run(["brew", "services", "start", "syncthing"], check=False, timeout=120)
        return "brew-service-enabled" if result.returncode == 0 else "brew-service-enable-failed"
    return "manual-startup-required"


def enable_startup_remote(host: str) -> tuple[str, str]:
    service = ssh(host, "systemctl --user enable --now syncthing.service", check=False)
    service_state = "systemd-enabled" if service.returncode == 0 else "manual-startup-required"
    linger = ssh(host, "loginctl show-user \"$(id -un)\" -p Linger 2>/dev/null || true", check=False).stdout.strip()
    return service_state, linger or "Linger=unknown"


def syncthing_config_path() -> Path | None:
    candidates: list[Path] = []
    if platform.system() == "Windows" and os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "Syncthing/config.xml")
    elif platform.system() == "Darwin":
        candidates.append(Path.home() / "Library/Application Support/Syncthing/config.xml")
    else:
        candidates.extend([
            Path.home() / ".config/syncthing/config.xml",
            Path.home() / ".local/state/syncthing/config.xml",
        ])
    return next((path for path in candidates if path.is_file()), None)


def local_api(path: str, method: str = "GET") -> dict[str, Any] | None:
    config_path = syncthing_config_path()
    if not config_path:
        return None
    root = ET.parse(config_path).getroot()
    gui = root.find("gui")
    if gui is None:
        return None
    key = gui.findtext("apikey")
    address = gui.findtext("address") or "127.0.0.1:8384"
    if address.startswith(":"):
        address = "127.0.0.1" + address
    scheme = "https" if (gui.findtext("tls") or "false").lower() == "true" else "http"
    request = urllib.request.Request(
        f"{scheme}://{address}{path}", headers={"X-API-Key": key or ""}, method=method
    )
    context = ssl._create_unverified_context() if scheme == "https" else None
    try:
        with urllib.request.urlopen(request, timeout=10, context=context) as response:
            payload = response.read()
            value = json.loads(payload) if payload else {}
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def disk_status(host: str, remote_path: str) -> dict[str, Any]:
    command = f"df -Pk {shlex.quote(remote_path)} | tail -n 1"
    result = ssh(host, command, check=False)
    fields = result.stdout.split()
    if result.returncode != 0 or len(fields) < 6:
        return {"available": None, "warning": "Could not read remote disk usage"}
    available = int(fields[3]) * 1024
    used_percent = int(fields[4].rstrip("%"))
    warning = None
    if available < 5 * 1024**3 or used_percent >= 99:
        warning = f"Remote disk is low: {available / 1024**3:.1f} GiB available, {used_percent}% used"
    return {"available_bytes": available, "used_percent": used_percent, "warning": warning}


def save_project(entry: dict[str, Any], config_path: Path) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, Any] = {"version": 1, "projects": []}
    if config_path.is_file():
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            pass
    projects = data.setdefault("projects", [])
    if not isinstance(projects, list):
        projects = data["projects"] = []
    projects[:] = [item for item in projects if not isinstance(item, dict) or item.get("folder_id") != entry["folder_id"]]
    projects.append(entry)
    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def configure(args: argparse.Namespace) -> int:
    validate_host(args.ssh_host)
    project = validate_project(args.project)
    remote_root = validate_remote_root(args.remote_root)
    remote_path = str(remote_root.joinpath(project))
    local_root = Path(args.local_root).expanduser().resolve()
    local_name = args.local_name or project.name
    if not PROJECT_SEGMENT.fullmatch(local_name):
        raise SyncError("Local name contains unsupported characters.")
    local_path = str(local_root / local_name)

    exists = ssh(args.ssh_host, f"test -d {shlex.quote(remote_path)}", check=False)
    if exists.returncode != 0:
        raise SyncError(f"Remote project does not exist: {args.ssh_host}:{remote_path}")

    local_system, remote_system = ensure_prerequisites(args.ssh_host)
    local_id = str(local_system.get("myID", ""))
    remote_id = str(remote_system.get("myID", ""))
    if not local_id or not remote_id:
        raise SyncError("Could not determine both Syncthing device IDs.")

    remote_match = folder_for_path(args.ssh_host, remote_path)
    local_match = folder_for_path(None, local_path)
    if remote_match and local_match and remote_match[0] != local_match[0]:
        raise SyncError("The requested paths are already configured with different folder IDs.")
    folder_id = (remote_match or local_match or (make_folder_id(args.ssh_host, remote_path, args.project), {}))[0]

    plan = {
        "folder_id": folder_id,
        "remote": f"{args.ssh_host}:{remote_path}",
        "remote_type": "sendonly",
        "local": local_path,
        "local_type": "receiveonly",
        "watch_delay_seconds": 2,
        "full_scan_seconds": 300,
        "dry_run": bool(args.dry_run),
    }
    print(json.dumps({"plan": plan}, indent=2))
    if args.dry_run:
        return 0

    local_startup = enable_startup_local()
    remote_startup, linger = enable_startup_remote(args.ssh_host)
    add_device_local(remote_id, "server-live-sync-remote")
    add_device_remote(args.ssh_host, local_id, "server-live-sync-local")

    ignore_file = Path(args.ignore_file).expanduser().resolve() if args.ignore_file else Path(__file__).with_name("default.stignore")
    if not ignore_file.is_file():
        raise SyncError(f"Ignore file does not exist: {ignore_file}")
    ignore_status = install_default_ignore(args.ssh_host, remote_path, ignore_file)
    ensure_local_marker(local_path)

    if remote_match:
        if remote_match[1].get("type") != "sendonly":
            raise SyncError(f"Existing remote folder {folder_id} is not sendonly.")
    else:
        remote_st(args.ssh_host, [
            "config", "folders", "add", f"--id={folder_id}", f"--label={project.name}",
            f"--path={remote_path}", "--type=sendonly", "--rescan-intervals=300",
            "--fswatcher-enabled", "--fswatcher-delays=2",
        ])
    add_folder_device_remote(args.ssh_host, folder_id, local_id)

    if local_match:
        if local_match[1].get("type") != "receiveonly":
            raise SyncError(f"Existing local folder {folder_id} is not receiveonly.")
    else:
        local_st([
            "config", "folders", "add", f"--id={folder_id}", f"--label={local_name}",
            f"--path={local_path}", "--type=receiveonly", "--rescan-intervals=300",
            "--fswatcher-enabled", "--fswatcher-delays=10",
        ])
    add_folder_device_local(folder_id, remote_id)

    # A restored marker is recognized when the folder is scanned again.
    local_api(f"/rest/db/scan?folder={folder_id}", method="POST")

    entry = {
        "folder_id": folder_id,
        "ssh_host": args.ssh_host,
        "remote_path": remote_path,
        "local_path": local_path,
    }
    save_project(entry, Path(args.config).expanduser())

    status: dict[str, Any] | None = None
    deadline = time.time() + args.wait_seconds
    while time.time() < deadline:
        status = local_api(f"/rest/db/status?folder={folder_id}")
        if status and status.get("state") not in ("scanning", "scan-waiting", "sync-preparing"):
            break
        time.sleep(1)
    connections = local_api("/rest/system/connections") or {}
    remote_connection = (connections.get("connections") or {}).get(remote_id, {})
    healthy = bool(status) and not status.get("error") and int(status.get("errors") or 0) == 0
    result = {
        "configured": True,
        "healthy": healthy,
        **entry,
        "remote_type": "sendonly",
        "local_type": "receiveonly",
        "ignore": ignore_status,
        "local_startup": local_startup,
        "remote_startup": remote_startup,
        "remote_linger": linger,
        "connected": remote_connection.get("connected"),
        "connection_type": remote_connection.get("type"),
        "state": status.get("state") if status else None,
        "scan_error": status.get("error") if status else None,
        "scan_errors": status.get("errors") if status else None,
        "need_items": status.get("needTotalItems") if status else None,
        "need_bytes": status.get("needBytes") if status else None,
        "disk": disk_status(args.ssh_host, remote_path),
        "config": str(Path(args.config).expanduser()),
    }
    print(json.dumps({"result": result}, indent=2))
    return 0 if healthy else 1


def doctor(args: argparse.Namespace) -> int:
    validate_host(args.ssh_host)
    checks: dict[str, Any] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    ok = sys.version_info >= MIN_PYTHON
    for name in ("ssh", "scp", "syncthing"):
        path = shutil.which(name)
        checks[f"local_{name}"] = path
        ok = ok and path is not None
    ssh_probe = ssh(args.ssh_host, "printf connected", check=False)
    checks["ssh"] = ssh_probe.returncode == 0 and ssh_probe.stdout == "connected"
    ok = ok and checks["ssh"]
    remote_binary = ssh(args.ssh_host, "command -v syncthing", check=False)
    checks["remote_syncthing"] = remote_binary.stdout.strip() or None
    ok = ok and remote_binary.returncode == 0
    local_system = local_st(["show", "system"], check=False) if shutil.which("syncthing") else None
    checks["local_running"] = bool(local_system and local_system.returncode == 0)
    remote_system = remote_st(args.ssh_host, ["show", "system"], check=False) if checks["ssh"] and checks["remote_syncthing"] else None
    checks["remote_running"] = bool(remote_system and remote_system.returncode == 0)
    ok = ok and checks["local_running"] and checks["remote_running"]
    print(json.dumps({"ok": ok, "checks": checks}, indent=2))
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    doctor_parser = subparsers.add_parser("doctor", help="Check local, SSH, and remote prerequisites")
    doctor_parser.add_argument("--ssh-host", required=True, help="SSH alias or user@host")
    doctor_parser.set_defaults(func=doctor)

    add_parser = subparsers.add_parser("add", help="Configure or verify one project mirror")
    add_parser.add_argument("--ssh-host", required=True, help="SSH alias or user@host")
    add_parser.add_argument("--remote-root", required=True, help="Absolute server directory containing projects")
    add_parser.add_argument("--project", required=True, help="Project path relative to remote root")
    add_parser.add_argument("--local-root", default=str(Path.home() / "code"), help="Local directory containing project mirrors")
    add_parser.add_argument("--local-name", help="Optional local folder name; defaults to project basename")
    add_parser.add_argument("--ignore-file", help="Optional Syncthing ignore file used only when the remote project has none")
    add_parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Non-secret local metadata file")
    add_parser.add_argument("--wait-seconds", type=int, default=20, help="Maximum verification wait")
    add_parser.add_argument("--dry-run", action="store_true", help="Validate and print the plan without changes")
    add_parser.set_defaults(func=configure)
    return parser


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        print("Python 3.9 or newer is required.", file=sys.stderr)
        return 2
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except SyncError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
