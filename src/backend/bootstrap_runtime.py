import argparse
import hashlib
import ipaddress
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from .install_lock import server_installation


RUNTIME = Path(__file__).resolve().parents[2] / ".runtime"
DENO_RELEASE = "https://github.com/denoland/deno/releases/download/v2.9.8"


@dataclass(frozen=True)
class Tool:
    name: str
    url: str
    sha256: str
    member: str


TOOLS = {
    ("Darwin", "arm64"): (
        Tool("FFmpeg", "https://github.com/aagedal/ffmpeg-apple-silicon/releases/download/v9.0.2-1/"
             "ffmpeg-9.0.2-full-macos-arm64.zip",
             "004ed3719259c9ce973b82ae399ada187bba62b2eba0c09e7751524b15cbaec3",
             "ffmpeg-9.0.2-full-macos-arm64/ffmpeg"),
        Tool("Deno", f"{DENO_RELEASE}/deno-aarch64-apple-darwin.zip",
             "9f0bf660393da2eaff044cdc7444cb08c73f235849b61d224d825a194ce7c98e", "deno"),
    ),
    ("Windows", "amd64"): (
        Tool("FFmpeg", "https://github.com/GyanD/codexffmpeg/releases/download/8.1.2/"
             "ffmpeg-8.1.2-essentials_build.zip",
             "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
             "ffmpeg-8.1.2-essentials_build/bin/ffmpeg.exe"),
        Tool("Deno", f"{DENO_RELEASE}/deno-x86_64-pc-windows-msvc.zip",
             "cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf", "deno.exe"),
    ),
}


def tool_directory(tool: Tool) -> Path:
    return RUNTIME / "bin" / tool.sha256[:16]


def install_tool(tool: Tool, curl: str) -> Path:
    directory = tool_directory(tool)
    filename = Path(tool.member).name
    executable = directory / filename
    if executable.is_file():
        return directory

    print(f"Downloading {tool.name}...", flush=True)
    directory.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=directory) as temporary:
        archive = Path(temporary) / "download.zip"
        subprocess.run([curl, "--proto", "=https", "--proto-redir", "=https", "--location", "--fail",
                        "--silent", "--show-error", "--connect-timeout", "15", "--retry", "3",
                        "--output", str(archive), tool.url], check=True)
        with archive.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        if checksum != tool.sha256:
            raise ValueError(f"The downloaded {tool.name} archive failed SHA-256 verification.")
        staged = Path(temporary) / filename
        with ZipFile(archive) as package, package.open(tool.member) as source, staged.open("wb") as output:
            shutil.copyfileobj(source, output)
        staged.chmod(0o755)
        try:
            staged.replace(executable)
        except PermissionError:
            if not executable.is_file():
                raise
    return directory


def parse_server_args():
    parser = argparse.ArgumentParser(
        description="Adjust voice and background volume in YouTube and Twitch videos or live streams. "
                    "Watch the result over WebRTC.")
    parser.add_argument("--host", default="127.0.0.1", type=ipaddress.IPv4Address,
                        help="IPv4 address the server listens on (default: %(default)s).")
    parser.add_argument("--port", type=int, default=48763,
                        help="Port the server listens on (default: %(default)s).")
    parser.add_argument("--device", default="auto", choices=("auto", "mps", "cuda", "cpu"))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535.")
    return args


def prepare_runtime():
    system = platform.system()
    tools = TOOLS.get((system, platform.machine().lower()))
    if tools is None:
        raise SystemExit("This launcher supports macOS on Apple Silicon and Windows on x64.")
    curl = "curl.exe" if system == "Windows" else "/usr/bin/curl"
    try:
        directories = [str(install_tool(tool, curl)) for tool in tools]
    except (OSError, subprocess.CalledProcessError, ValueError, BadZipFile, KeyError) as error:
        raise SystemExit(f"Runtime setup failed: {error}") from None
    os.environ["PATH"] = os.pathsep.join([*directories, os.environ.get("PATH", os.defpath)])
    os.environ.setdefault("DENO_DIR", str(RUNTIME / "deno-cache"))


def main():
    args = parse_server_args()
    with server_installation(RUNTIME.parent, args.port):
        prepare_runtime()
        from .server import serve

        serve(args)


if __name__ == "__main__":
    main()
