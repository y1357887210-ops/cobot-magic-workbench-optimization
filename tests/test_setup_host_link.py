"""Unit tests for Phase 1 & Phase 2 host onboarding wizard and DHCP dry-run."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.setup_host_link import (
    build_installation_local_config,
    build_ssh_config_block,
    main as wizard_main,
    upsert_ssh_config,
    validate_alias,
    validate_ipv4,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class SetupHostLinkTests(unittest.TestCase):
    def test_validate_ipv4_and_alias(self):
        self.assertEqual(validate_ipv4("192.168.10.2"), "192.168.10.2")
        self.assertEqual(validate_alias("agilex-robot"), "agilex-robot")
        with self.assertRaises(ValueError):
            validate_ipv4("999.168.10.2")
        with self.assertRaises(ValueError):
            validate_alias("bad alias; rm -rf")

    def test_upsert_ssh_config_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config"
            cfg.write_text("Host other\n    HostName 10.0.0.5\n", encoding="utf-8")
            block1 = build_ssh_config_block("agilex-robot", "192.168.10.2", "agilex", Path("/tmp/id_ed25519"))
            out1 = upsert_ssh_config(cfg, "agilex-robot", block1)
            cfg.write_text(out1, encoding="utf-8")
            block2 = build_ssh_config_block("agilex-robot", "192.168.10.9", "agilex", Path("/tmp/id_ed25519"))
            out2 = upsert_ssh_config(cfg, "agilex-robot", block2)
            self.assertIn("Host other", out2)
            self.assertIn("HostName 192.168.10.9", out2)
            self.assertNotIn("HostName 192.168.10.2", out2)
            self.assertEqual(out2.count("# BEGIN COBOT-MAGIC-WORKBENCH (agilex-robot)"), 1)

    def test_installation_local_config_preserves_safety_redlines(self):
        data = build_installation_local_config(
            host_name="gpu-4090",
            robot_alias="agilex-robot",
            robot_ip="192.168.10.2",
            robot_user="agilex",
            example_path=REPO_ROOT / "server/installation.example.json",
        )
        self.assertEqual(data["robot_ssh_alias"], "agilex-robot")
        self.assertFalse(data["auto_load_models"])
        self.assertFalse(data["auto_clear_stop"])

    def test_wizard_dry_run_succeeds_without_mutating_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            ssh_dir = Path(tmp) / "ssh"
            rc = wizard_main(["--dry-run", "--ssh-dir", str(ssh_dir), "--repo-root", str(tmp)])
            self.assertEqual(rc, 0)
            self.assertFalse((ssh_dir / "config").exists())

    def test_robot_dhcp_script_dry_run(self):
        proc = subprocess.run(
            ["bash", str(REPO_ROOT / "tools/setup_robot_dhcp.sh"), "--iface", "eth0", "--dry-run"],
            capture_output=True, text=True, check=True,
        )
        self.assertIn("dhcp-option=3", proc.stdout)
        self.assertIn("dhcp-option=6", proc.stdout)


if __name__ == "__main__":
    unittest.main()
