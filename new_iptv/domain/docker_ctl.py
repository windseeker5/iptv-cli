"""Docker and docker-compose orchestration helpers."""

import os
import subprocess
from pathlib import Path

from new_iptv.domain import config


def compose_binary() -> list[str]:
    """Return the docker-compose command as a list."""
    # Prefer the plugin form if available, else legacy docker-compose.
    try:
        subprocess.run(
            ["docker", "compose", "version"], capture_output=True, check=True, timeout=5
        )
        return ["docker", "compose"]
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return ["docker-compose"]


def project_root() -> Path:
    """Return the project root directory."""
    return Path(__file__).resolve().parents[2]


def run_compose(
    args: list[str], check: bool = False, timeout: int | None = 60, capture: bool = True
) -> subprocess.CompletedProcess:
    """Run docker-compose with the given arguments."""
    cmd = compose_binary() + args
    kwargs = {"cwd": project_root()}
    if capture:
        kwargs["capture_output"] = True
        kwargs["text"] = True
    if timeout:
        kwargs["timeout"] = timeout
    if check:
        kwargs["check"] = True
    return subprocess.run(cmd, **kwargs)


def check_docker_available() -> bool:
    """Return True if docker is installed and responsive."""
    try:
        subprocess.run(["docker", "version"], capture_output=True, check=True, timeout=5)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def container_status(container_name: str) -> dict:
    """Return status info for a named container."""
    status = {"running": False, "exists": False, "raw": ""}
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        raw = result.stdout.strip()
        status["raw"] = raw
        if raw and "Up" in raw:
            status["running"] = True
            status["exists"] = True
            return status

        # Check if container exists but is stopped
        result = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={container_name}",
                "--format",
                "{{.Status}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        raw = result.stdout.strip()
        status["raw"] = raw
        if raw:
            status["exists"] = True
    except Exception:
        pass
    return status


def service_status() -> dict[str, dict]:
    """Return status for all known services."""
    return {
        "nginx-rtmp": container_status("iptv-nginx-rtmp"),
        "jellyfin": container_status("iptv-jellyfin"),
        "samba": container_status("iptv-samba"),
        "caddy": container_status("iptv-caddy"),
        "viewer-counter": container_status("iptv-viewer-counter"),
    }


def start_service(service: str | None = None, build: bool = False) -> dict:
    """Start one or all docker-compose services."""
    args = ["up", "-d"]
    if build:
        args.append("--build")
    if service:
        args.append(service)

    try:
        result = run_compose(args, timeout=300)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout if result.stdout else "",
            "stderr": result.stderr if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Timeout starting service"}
    except FileNotFoundError:
        return {"success": False, "message": "docker-compose not found"}
    except Exception as e:
        return {"success": False, "message": f"Error starting service: {e}"}


def stop_service(service: str | None = None) -> dict:
    """Stop one or all docker-compose services."""
    args = ["down"] if service is None else ["stop", service]
    try:
        result = run_compose(args, timeout=60)
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout if result.stdout else "",
            "stderr": result.stderr if result.stderr else "",
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "message": "Timeout stopping service"}
    except FileNotFoundError:
        return {"success": False, "message": "docker-compose not found"}
    except Exception as e:
        return {"success": False, "message": f"Error stopping service: {e}"}


def service_logs(service: str, tail: int = 50) -> str:
    """Return recent logs for a container."""
    container_map = {
        "nginx-rtmp": "iptv-nginx-rtmp",
        "jellyfin": "iptv-jellyfin",
        "samba": "iptv-samba",
        "caddy": "iptv-caddy",
        "viewer-counter": "iptv-viewer-counter",
    }
    container_name = container_map.get(service, service)
    try:
        result = subprocess.run(
            ["docker", "logs", container_name, "--tail", str(tail)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
        return result.stderr or f"Could not read logs for {container_name}"
    except Exception as e:
        return f"Error reading logs: {e}"


def validate_compose() -> dict:
    """Validate docker-compose.yml configuration."""
    try:
        result = run_compose(["config", "-q"], timeout=10)
        return {"valid": result.returncode == 0, "message": ""}
    except subprocess.CalledProcessError as e:
        return {"valid": False, "message": e.stderr or "Validation failed"}
    except Exception as e:
        return {"valid": False, "message": str(e)}
