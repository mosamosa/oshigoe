import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

from src.backend import update
from src.backend.install_lock import installation_lock, pending_update, server_installation


class UpdateTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        self.old_files = {"VERSION": "0.1.0\n", "start.mac.command": "old start", "retired.txt": "old file"}
        self.old_files[update.MANIFEST] = "\n".join([*self.old_files, update.MANIFEST]) + "\n"
        for name, value in self.old_files.items():
            (self.root / name).write_text(value, encoding="utf-8")
        (self.root / "user.txt").write_text("mine", encoding="utf-8")
        (self.root / ".runtime").mkdir()
        (self.root / ".runtime" / "keep").write_text("installed", encoding="utf-8")

    def release(self, *, extra_path=None, bad_digest=False):
        files = {"VERSION": "0.2.0\n", "start.mac.command": "new start", "src/new.py": "new file"}
        files[update.MANIFEST] = "\n".join([*files, update.MANIFEST]) + "\n"
        zip_bytes = io.BytesIO()
        with ZipFile(zip_bytes, "w") as archive:
            for name, value in files.items():
                info = ZipInfo(f"oshigoe/{name}")
                if name == "start.mac.command":
                    info.external_attr = 0o100755 << 16
                archive.writestr(info, value)
            if extra_path:
                archive.writestr(extra_path, "unexpected")
        payload = zip_bytes.getvalue()
        tag = "v0.2.0"
        url = f"https://github.com/mosamosa/oshigoe/releases/download/{tag}/{update.ASSET_NAME}"
        digest = "0" * 64 if bad_digest else hashlib.sha256(payload).hexdigest()
        metadata = {"tag_name": tag, "assets": [
            {"name": update.ASSET_NAME, "browser_download_url": url, "digest": f"sha256:{digest}"},
        ]}
        responses = {
            "https://api.github.com/repos/mosamosa/oshigoe/releases/latest": json.dumps(metadata).encode(),
            url: payload,
        }
        return patch.object(update, "open_url", side_effect=lambda address: io.BytesIO(responses[address]))

    def assert_old_files(self):
        for name, value in self.old_files.items():
            self.assertEqual((self.root / name).read_text(encoding="utf-8"), value, name)
        self.assertFalse((self.root / "src/new.py").exists())

    def test_updates_managed_files_and_preserves_runtime_and_local_files(self):
        (self.root / "start.mac.command").write_text("modified by user", encoding="utf-8")
        with self.release() as network:
            update.update(self.root)
            self.assertEqual(network.call_count, 2)
            update.update(self.root)
            self.assertEqual(network.call_count, 3)

        self.assertEqual((self.root / "VERSION").read_text(), "0.2.0\n")
        self.assertEqual((self.root / "start.mac.command").read_text(), "new start")
        if os.name != "nt":
            self.assertEqual((self.root / "start.mac.command").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / "src/new.py").read_text(), "new file")
        self.assertFalse((self.root / "retired.txt").exists())
        self.assertEqual((self.root / "user.txt").read_text(), "mine")
        self.assertEqual((self.root / ".runtime" / "keep").read_text(), "installed")
        self.assertFalse(pending_update(self.root).exists())

    def test_running_server_blocks_update_until_the_last_server_stops(self):
        with server_installation(self.root, 48764):
            with server_installation(self.root, 48763):
                self.assertTrue((self.root / ".runtime" / "server-48763.lock").is_file())
            with (patch.object(update, "ROOT", self.root), patch.object(sys, "argv", ["update"]),
                  patch.object(update, "open_url") as network, patch("sys.stderr", new=io.StringIO()) as error):
                self.assertEqual(update.main(), 1)
            network.assert_not_called()
            self.assertIn("Stop the Oshigoe servers", error.getvalue())
        self.assert_old_files()
        self.assertFalse(pending_update(self.root).exists())
        with self.release():
            update.update(self.root)
        self.assertEqual((self.root / "VERSION").read_text(), "0.2.0\n")

    def test_git_checkout_points_to_git_pull_before_starting_an_update(self):
        for worktree in (False, True):
            with self.subTest(worktree=worktree), TemporaryDirectory() as directory:
                root = Path(directory)
                marker = root / ".git"
                if worktree:
                    marker.write_text("gitdir: /example/worktree\n")
                else:
                    marker.mkdir()

                with (patch.object(update, "open_url") as network,
                      self.assertRaisesRegex(update.UpdateError, "git pull")):
                    update.update(root)
                network.assert_not_called()
                self.assertEqual(list(root.iterdir()), [marker])

    def test_installation_lock_is_visible_to_other_processes(self):
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from src.backend.install_lock import server_installation\n"
            "with server_installation(Path(sys.argv[1]), int(sys.argv[2])):\n"
            "    print('acquired')\n"
        )
        command = [sys.executable, "-c", script, str(self.root)]
        with server_installation(self.root, 48763):
            duplicate = subprocess.run([*command, "48763"], capture_output=True, text=True, timeout=15)
            other = subprocess.run([*command, "48764"], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("already running on port 48763", duplicate.stderr)
        self.assertEqual(other.returncode, 0, other.stderr)
        self.assertEqual(other.stdout.strip(), "acquired")

        with installation_lock(self.root):
            blocked = subprocess.run([*command, "48765"], capture_output=True, text=True, timeout=15)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("update is in progress", blocked.stderr)

    def test_interrupted_update_blocks_new_servers(self):
        pending_update(self.root).mkdir()
        with self.assertRaisesRegex(SystemExit, "An update was interrupted"):
            with server_installation(self.root, 48763):
                self.fail("server started before update recovery")

    def test_rejects_bad_digest_and_outside_paths_without_touching_the_installation(self):
        for fixture in ({"bad_digest": True}, {"extra_path": "oshigoe/../other.txt"}):
            with self.subTest(**fixture), self.release(**fixture), self.assertRaises(update.UpdateError):
                update.update(self.root)
            self.assert_old_files()

    def test_rolls_back_after_a_file_replacement_fails(self):
        original_replace = os.replace

        def fail_on_new_start(source, target):
            if source.parent.name == "staging" and target.name == "start.mac.command":
                raise OSError("simulated replacement failure")
            original_replace(source, target)

        with (self.release(), patch.object(update.os, "replace", side_effect=fail_on_new_start),
              self.assertRaisesRegex(OSError, "simulated replacement failure")):
            update.update(self.root)

        self.assert_old_files()
        self.assertFalse(pending_update(self.root).exists())

    def test_restores_files_from_an_interrupted_update_before_checking_for_a_release(self):
        transaction = pending_update(self.root)
        backups = transaction / "backup"
        backups.mkdir(parents=True)
        for name, content in self.old_files.items():
            (backups / name).write_text(content, encoding="utf-8")
        (transaction / "files.json").write_text(json.dumps({
            "paths": [*self.old_files, "src/new.py"], "original": list(self.old_files),
        }), encoding="utf-8")
        (self.root / "VERSION").write_text("0.2.0\n", encoding="utf-8")
        (self.root / "retired.txt").unlink()
        (self.root / "src").mkdir()
        (self.root / "src/new.py").write_text("new file", encoding="utf-8")

        with patch.object(update, "latest_release", return_value=("v0.1.0", [])):
            update.update(self.root)

        self.assert_old_files()
        self.assertFalse(pending_update(self.root).exists())
