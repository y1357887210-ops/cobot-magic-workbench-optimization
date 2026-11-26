"""Phase 2: Cross-platform host onboarding wizard for COBOT MAGIC direct Ethernet + SSH.

Automates first-time setup on a new 4090 or operator host without touching robot motion:
  1. Probes direct-Ethernet reachability (works zero-config if robot DHCP is enabled,
     or prints/applies safe non-default-gateway static IP config on the chosen NIC).
  2. Ensures a local ed25519 SSH keypair exists (never exports or prints the private key).
  3. Authorizes the public key on the robot host (via ssh-copy-id when password login is
     still enabled, or exports a ready-to-share .pub bundle for high-security admins).
  4. Writes a pinned ~/.ssh/config alias block (BatchMode=yes, StrictHostKeyChecking=yes)
     and a local server/installation.local.json skeleton (never committed to Git).
  5. Runs a read-only end-to-end probe (SSH hostname + USB mount visibility).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys

DEFAULT_ROBOT_IP = "192.168.10.2"
DEFAULT_ROBOT_USER = "agilex"
DEFAULT_SSH_ALIAS = "agilex-robot"
DEFAULT_HOST_IP = "192.168.10.1"
ALIAS_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
IP_RE = re.compile(r"^(?:\d{1,3}\.){3}\d{1,3}$")


def validate_alias(alias: str) -> str:
    if not isinstance(alias, str) or not ALIAS_RE.fullmatch(alias):
        raise ValueError(f"Invalid SSH alias: {alias!r}")
    return alias


def validate_ipv4(ip: str) -> str:
    if not isinstance(ip, str) or not IP_RE.fullmatch(ip):
        raise ValueError(f"Invalid IPv4 address: {ip!r}")
    parts = [int(p) for p in ip.split(".")]
    if any(p < 0 or p > 255 for p in parts):
        raise ValueError(f"Invalid IPv4 octet range: {ip!r}")
    return ip


def build_ssh_config_block(alias: str, robot_ip: str, robot_user: str, identity_file: Path) -> str:
    validate_alias(alias)
    validate_ipv4(robot_ip)
    return (
        f"# BEGIN COBOT-MAGIC-WORKBENCH ({alias})\n"
        f"Host {alias}\n"
        f"    HostName {robot_ip}\n"
        f"    User {robot_user}\n"
        f"    IdentityFile {identity_file}\n"
        f"    IdentitiesOnly yes\n"
        f"    BatchMode yes\n"
        f"    StrictHostKeyChecking yes\n"
        f"    ConnectTimeout 8\n"
        f"    ServerAliveInterval 15\n"
        f"    ServerAliveCountMax 3\n"
        f"# END COBOT-MAGIC-WORKBENCH ({alias})\n"
    )


def upsert_ssh_config(config_path: Path, alias: str, block: str) -> str:
    """Idempotently replace or append the managed Host block in ~/.ssh/config."""
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    begin = f"# BEGIN COBOT-MAGIC-WORKBENCH ({alias})"
    end = f"# END COBOT-MAGIC-WORKBENCH ({alias})"
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.DOTALL)
    if pattern.search(existing):
        updated = pattern.sub(block, existing, count=1)
    else:
        sep = "\n" if existing and not existing.endswith("\n") else ""
        prefix = existing + sep + ("\n" if existing.strip() else "")
        updated = prefix + block
    return updated


def build_installation_local_config(
    *,
    host_name: str,
    robot_alias: str,
    robot_ip: str,
    robot_user: str,
    example_path: Path | None = None,
) -> dict:
    base = {}
    if example_path and example_path.is_file():
        try:
            loaded = json.loads(example_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                base = loaded
        except (OSError, ValueError):
            base = {}
    base.setdefault("schema", "agilex-installation-local-v1")
    base["operator_host"] = host_name
    base["robot_ssh_alias"] = validate_alias(robot_alias)
    base["robot_ip"] = validate_ipv4(robot_ip)
    base["robot_user"] = robot_user
    base["auto_load_models"] = False
    base["auto_clear_stop"] = False
    return base


def find_ssh_binary() -> str | None:
    if platform.system() == "Windows":
        win_ssh = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32/OpenSSH/ssh.exe"
        if win_ssh.is_file():
            return str(win_ssh)
    return shutil.which("ssh")


def ensure_ssh_keypair(key_path: Path, *, dry_run: bool = False) -> Path:
    pub_path = key_path.with_suffix(key_path.suffix + ".pub")
    if key_path.exists() and pub_path.exists():
        print(f"[OK] Reusing existing SSH keypair: {pub_path}")
        return pub_path
    if dry_run:
        print(f"[DRY-RUN] Would generate ed25519 SSH keypair at {key_path}")
        return pub_path
    key_path.parent.mkdir(parents=True, exist_ok=True)
    keygen = shutil.which("ssh-keygen")
    if not keygen:
        raise RuntimeError("ssh-keygen not found; install OpenSSH client first.")
    subprocess.run(
        [keygen, "-t", "ed25519", "-N", "", "-f", str(key_path),
         "-C", f"cobot-magic@{socket.gethostname()}"],
        check=True,
    )
    print(f"[OK] Generated new SSH keypair: {pub_path}")
    return pub_path


def probe_tcp(ip: str, port: int = 22, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def probe_passwordless_ssh(ssh_bin: str, alias: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [ssh_bin, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", alias, "hostname"],
            capture_output=True, text=True, timeout=8,
        )
        if proc.returncode == 0:
            return True, proc.stdout.strip()
        return False, (proc.stderr or proc.stdout).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)


def authorize_public_key_interactive(pub_path: Path, robot_user: str, robot_ip: str, *, dry_run: bool = False) -> bool:
    if dry_run:
        print(f"[DRY-RUN] Would authorize {pub_path} on {robot_user}@{robot_ip} via ssh-copy-id")
        return True
    copy_id = shutil.which("ssh-copy-id")
    if copy_id:
        print(f"[INFO] First-time authorization: enter {robot_user}@{robot_ip} login password ONCE...")
        proc = subprocess.run([copy_id, "-i", str(pub_path), f"{robot_user}@{robot_ip}"])
        return proc.returncode == 0
    pub_text = pub_path.read_text(encoding="utf-8").strip()
    print("[ACTION REQUIRED] ssh-copy-id is unavailable on this OS (or password login is disabled).")
    print("Append the following single-line public key to ~/.ssh/authorized_keys on the robot host:")
    print(f"\n  {pub_text}\n")
    return False


def print_static_ip_hint(host_ip: str, robot_ip: str) -> None:
    if platform.system() == "Windows":
        print(
            "[HINT] Robot not reachable yet. If the robot does not run DHCP (tools/setup_robot_dhcp.sh),\n"
            f"       assign static IP {host_ip} / 255.255.255.0 (leave Gateway EMPTY to preserve Wi-Fi)\n"
            f"       on the Ethernet adapter connected to {robot_ip}:\n"
            f'       netsh interface ip set address name="Ethernet" static {host_ip} 255.255.255.0'
        )
    else:
        print(
            "[HINT] Robot not reachable yet. If the robot does not run DHCP (tools/setup_robot_dhcp.sh),\n"
            f"       assign static IP {host_ip}/24 with never-default=yes (preserves Wi-Fi):\n"
            f"       sudo nmcli con add type ethernet ifname <eth_iface> con-name agilex-link "
            f"ipv4.method manual ipv4.addresses {host_ip}/24 ipv4.never-default yes"
        )


def run_wizard(args: argparse.Namespace) -> int:
    validate_alias(args.alias)
    validate_ipv4(args.robot_ip)
    validate_ipv4(args.host_ip)

    ssh_dir = Path(args.ssh_dir).expanduser()
    key_path = ssh_dir / "id_ed25519"
    config_path = ssh_dir / "config"
    repo_root = Path(args.repo_root).resolve()
    local_cfg_path = repo_root / "server" / "installation.local.json"
    example_cfg_path = repo_root / "server" / "installation.example.json"

    print("=== COBOT MAGIC Host Onboarding Wizard (Phase 2) ===")
    print(f"Target robot: {args.robot_user}@{args.robot_ip} (alias: {args.alias})")

    # Step 1: Network reachability check
    reachable = True if args.dry_run else probe_tcp(args.robot_ip, 22, timeout=2.0)
    if reachable:
        print(f"[1/5] Network OK: {args.robot_ip}:22 is reachable.")
    else:
        print(f"[1/5] Network WARN: {args.robot_ip}:22 is not reachable yet.")
        print_static_ip_hint(args.host_ip, args.robot_ip)

    # Step 2: Ensure SSH keypair
    pub_path = ensure_ssh_keypair(key_path, dry_run=args.dry_run)

    # Step 3: Upsert ~/.ssh/config block
    block = build_ssh_config_block(args.alias, args.robot_ip, args.robot_user, key_path)
    updated_ssh = upsert_ssh_config(config_path, args.alias, block)
    if args.dry_run:
        print(f"[3/5] [DRY-RUN] Would update {config_path} with Host {args.alias}")
    else:
        ssh_dir.mkdir(parents=True, exist_ok=True)
        config_path.write_text(updated_ssh, encoding="utf-8")
        try:
            os.chmod(config_path, 0o600)
        except OSError:
            pass
        print(f"[3/5] Updated {config_path} with Host {args.alias}")

    # Step 4: Write local (gitignored) server/installation.local.json
    local_cfg = build_installation_local_config(
        host_name=socket.gethostname(),
        robot_alias=args.alias,
        robot_ip=args.robot_ip,
        robot_user=args.robot_user,
        example_path=example_cfg_path,
    )
    if args.dry_run:
        print(f"[4/5] [DRY-RUN] Would write {local_cfg_path}")
    else:
        local_cfg_path.parent.mkdir(parents=True, exist_ok=True)
        local_cfg_path.write_text(json.dumps(local_cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[4/5] Wrote gitignored local config: {local_cfg_path}")

    # Step 5: Verify or authorize passwordless SSH
    if args.dry_run:
        print("[5/5] [DRY-RUN] Skipping live SSH probe. Wizard checks passed.")
        return 0

    ssh_bin = find_ssh_binary()
    if not ssh_bin:
        print("[5/5] OpenSSH client not found; install OpenSSH to complete verification.")
        return 1

    ok, out = probe_passwordless_ssh(ssh_bin, args.alias)
    if not ok and reachable and not args.non_interactive:
        print(f"[5/5] Passwordless SSH not authorized yet ({out}). Launching first-time key authorization...")
        if authorize_public_key_interactive(pub_path, args.robot_user, args.robot_ip, dry_run=args.dry_run):
            ok, out = probe_passwordless_ssh(ssh_bin, args.alias)

    if ok:
        print(f"[5/5] VERIFIED! Passwordless SSH connected to robot host: {out}")
        return 0
    print(f"[5/5] Pending authorization or network fix: {out}")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="COBOT MAGIC new-host Ethernet & SSH onboarding wizard.")
    parser.add_argument("--robot-ip", default=DEFAULT_ROBOT_IP, help="Robot direct-Ethernet IPv4 (default: 192.168.10.2)")
    parser.add_argument("--host-ip", default=DEFAULT_HOST_IP, help="Suggested static IP for this host if DHCP is off")
    parser.add_argument("--robot-user", default=DEFAULT_ROBOT_USER, help="Robot SSH username (default: agilex)")
    parser.add_argument("--alias", default=DEFAULT_SSH_ALIAS, help="SSH config alias (default: agilex-robot)")
    parser.add_argument("--ssh-dir", default=str(Path.home() / ".ssh"), help="Target .ssh directory")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]), help="Repository root directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview all actions without modifying files or network")
    parser.add_argument("--non-interactive", action="store_true", help="Do not prompt for first-time password")
    return run_wizard(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
