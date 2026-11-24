import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

from .install_lock import InstallationBusy, installation_lock, pending_update


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "mosamosa/oshigoe"
RELEASE_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
ASSET_NAME = "oshigoe.zip"
MANIFEST = "release-files.txt"
VERSION = "VERSION"


class UpdateError(Exception):
    pass


def version_number(text):
    if not re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", text, re.ASCII):
        raise UpdateError(f"Invalid release version: {text!r}")
    return tuple(map(int, text.removeprefix("v").split(".")))


def managed_paths(entries):
    paths = set()
    folded = set()
    for name in entries:
        parts = name.split("/")
        if (parts[0].lower() in {".runtime", ".git", ".venv"} or
                any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) or part.endswith(".") or
                    PureWindowsPath(part).is_reserved() for part in parts)):
            raise UpdateError(f"Invalid release file path: {name!r}")
        if name.lower() in folded:
            raise UpdateError(f"Duplicate release file path: {name!r}")
        folded.add(name.lower())
        paths.add(name)
    if not {MANIFEST, VERSION} <= paths:
        raise UpdateError("The release file list is incomplete.")
    return paths


def file_manifest(directory):
    return managed_paths((directory / MANIFEST).read_text(encoding="utf-8").splitlines())


def open_url(url):
    return urlopen(Request(url, headers={"User-Agent": "Oshigoe-updater"}), timeout=30)


def latest_release():
    with open_url(RELEASE_URL) as response:
        release = json.load(response)
    tag = release["tag_name"]
    version_number(tag)
    return tag, release["assets"]


def download_release(tag, assets, archive):
    asset = next((item for item in assets if item["name"] == ASSET_NAME), None)
    if asset is None:
        raise UpdateError(f"The {tag} release does not contain {ASSET_NAME}.")

    url = asset.get("browser_download_url")
    expected = asset.get("digest") or ""
    if (url != f"https://github.com/{REPOSITORY}/releases/download/{tag}/{ASSET_NAME}" or
            not re.fullmatch(r"sha256:[0-9a-f]{64}", expected)):
        raise UpdateError("The release ZIP location or SHA-256 digest is invalid.")
    with open_url(url) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    with archive.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if expected != f"sha256:{actual}":
        raise UpdateError("The downloaded release ZIP failed SHA-256 verification.")


def stage_release(archive, staging, tag):
    with ZipFile(archive) as package:
        entries = {}
        names = []
        for member in package.infolist():
            if member.is_dir():
                continue
            prefix, _, name = member.filename.partition("/")
            if prefix != "oshigoe":
                raise UpdateError("The release ZIP must contain an oshigoe folder.")
            names.append(name)
            entries[name] = member
        paths = managed_paths(names)
        manifest = package.read(entries[MANIFEST]).decode("utf-8")
        version = package.read(entries[VERSION]).decode("utf-8").strip()
        if managed_paths(manifest.splitlines()) != paths or version_number(version) != version_number(tag):
            raise UpdateError("The release ZIP does not match its file list or version.")

        for name in sorted(paths):
            member = entries[name]
            output = staging / name
            output.parent.mkdir(parents=True, exist_ok=True)
            with package.open(member) as source, output.open("xb") as target:
                shutil.copyfileobj(source, target)
            if member.external_attr >> 16 & 0o111:
                output.chmod(0o755)
    return paths


def check_destination(root, name):
    target = root / name
    for path in target.parents:
        if path == root:
            break
        if (path.is_symlink() or path.is_junction() or (path.exists() and not path.is_dir())):
            raise UpdateError(f"The release cannot update {name!r}.")
    if target.is_symlink() or target.is_junction() or (target.exists() and not target.is_file()):
        raise UpdateError(f"The release cannot update {name!r}.")
    return target


def finish_update(transaction):
    (transaction / "files.json").unlink(missing_ok=True)
    shutil.rmtree(transaction)


def recover_update(root):
    transaction = pending_update(root)
    if not transaction.exists():
        return
    journal = transaction / "files.json"
    if journal.exists():
        plan = json.loads(journal.read_text(encoding="utf-8"))
        paths = managed_paths(plan["paths"])
        original = set(plan["original"])
        if not original <= paths:
            raise UpdateError("The update recovery file list is invalid.")
        replacement = transaction / "restore"
        for name in sorted(paths):
            target = check_destination(root, name)
            if name in original:
                shutil.copy2(transaction / "backup" / name, replacement)
                os.replace(replacement, target)
            else:
                target.unlink(missing_ok=True)
    finish_update(transaction)


def apply_release(root, staging, old_paths, new_paths):
    transaction = pending_update(root)
    paths = sorted(old_paths | new_paths)
    present = {name for name in paths if check_destination(root, name).exists()}
    conflicts = present - old_paths
    if conflicts:
        raise UpdateError(f"Release files would replace local files: {', '.join(sorted(conflicts))}.")

    # Publish the rollback journal only after all original files are backed up.
    transaction.mkdir()
    try:
        for name in sorted(present):
            backup = transaction / "backup" / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / name, backup)
        plan = transaction / "files.tmp"
        plan.write_text(json.dumps({"paths": paths, "original": sorted(present)}), encoding="utf-8")
        os.replace(plan, transaction / "files.json")

        for name in paths:
            target = root / name
            if name in new_paths:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging / name, target)
            else:
                target.unlink(missing_ok=True)
    except BaseException:
        recover_update(root)
        raise
    finish_update(transaction)


def update(root):
    if (root / ".git").exists():
        raise UpdateError("This folder is a Git checkout. Run 'git pull' to update it.")
    with installation_lock(root):
        recover_update(root)
        current = (root / VERSION).read_text(encoding="utf-8").strip()
        old_paths = file_manifest(root)
        tag, assets = latest_release()
        if version_number(tag) <= version_number(current):
            print(f"Oshigoe {current} is up to date.")
            return

        with TemporaryDirectory(prefix="update-", dir=root / ".runtime") as temporary:
            temporary = Path(temporary)
            archive = temporary / ASSET_NAME
            staging = temporary / "staging"
            download_release(tag, assets, archive)
            new_paths = stage_release(archive, staging, tag)
            apply_release(root, staging, old_paths, new_paths)
        print(f"Updated Oshigoe to {tag}. Run the start script to use the new version.")


def main():
    argparse.ArgumentParser(description="Update Oshigoe from the latest GitHub release.").parse_args()
    try:
        update(ROOT)
    except InstallationBusy:
        print("Stop the Oshigoe servers or wait for the ongoing update to finish, then run the update script again.",
              file=sys.stderr)
        return 1
    except (OSError, UpdateError, BadZipFile, ValueError, KeyError) as error:
        print(f"Update failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
