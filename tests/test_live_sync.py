import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "skill/server-live-sync/scripts/live_sync.py"
SPEC = importlib.util.spec_from_file_location("live_sync", SCRIPT)
live_sync = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(live_sync)


class ValidationTests(unittest.TestCase):
    def test_accepts_nested_relative_project(self):
        self.assertEqual(str(live_sync.validate_project("team/vla-2")), "team/vla-2")

    def test_rejects_project_traversal(self):
        with self.assertRaises(live_sync.SyncError):
            live_sync.validate_project("../private")

    def test_rejects_ssh_option_or_shell_input(self):
        for host in ("-oProxyCommand=bad", "gpu;rm", "gpu box"):
            with self.subTest(host=host), self.assertRaises(live_sync.SyncError):
                live_sync.validate_host(host)

    def test_accepts_common_ssh_hosts(self):
        for host in ("gpu-box", "alice@example.com", "[2001:db8::1]"):
            self.assertEqual(live_sync.validate_host(host), host)

    def test_rejects_broad_remote_root(self):
        for root in ("/", "/home", "/Users", "relative"):
            with self.subTest(root=root), self.assertRaises(live_sync.SyncError):
                live_sync.validate_remote_root(root)

    def test_folder_id_is_stable_and_host_specific(self):
        first = live_sync.make_folder_id("gpu-a", "/home/me/vla", "vla")
        self.assertEqual(first, live_sync.make_folder_id("gpu-a", "/home/me/vla", "vla"))
        self.assertNotEqual(first, live_sync.make_folder_id("gpu-b", "/home/me/vla", "vla"))
        self.assertRegex(first, r"^sls-vla-[0-9a-f]{8}$")


class ConfigTests(unittest.TestCase):
    def test_ensure_local_marker_repairs_missing_marker(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            marker = live_sync.ensure_local_marker(str(project))
            self.assertTrue(marker.is_dir())
            self.assertEqual(marker, project / ".stfolder")

    def test_save_project_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            entry = {
                "folder_id": "sls-vla-12345678",
                "ssh_host": "gpu",
                "remote_path": "/srv/projects/vla",
                "local_path": "/tmp/vla",
            }
            live_sync.save_project(entry, path)
            live_sync.save_project({**entry, "local_path": "/tmp/vla-new"}, path)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["projects"]), 1)
            self.assertEqual(data["projects"][0]["local_path"], "/tmp/vla-new")


if __name__ == "__main__":
    unittest.main()
