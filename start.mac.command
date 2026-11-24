#!/bin/sh
set -eu

if [ "${1:-}" != --update ]; then
    trap '
        launch_exit_code=$?
        printf "\nPress Enter to close... "
        IFS= read -r _ || :
        exit "$launch_exit_code"
    ' EXIT
    trap 'exit 130' INT
fi

CDPATH='' cd -P -- "$(dirname -- "$0")"

if [ "${1:-}" = --update ] && [ -e .git ]; then
    printf '%s\n' "This folder is a Git checkout. Run 'git pull' to update it." >&2
    exit 1
fi

if [ "$(uname -s)" != Darwin ]; then
    printf '%s\n' 'This launcher requires macOS.' >&2
    exit 1
fi

if [ "$(uname -m)" != arm64 ]; then
    printf '%s\n' 'This launcher requires a Mac with Apple Silicon.' >&2
    exit 1
fi

uv_version=0.13.9
target=aarch64-apple-darwin
checksum=5aeca67f2283f75235de40d0bd9c0a5bbc5fce23b6d48b3bf9fa1b2db08382ca

runtime="$PWD/.runtime"
uv="$runtime/uv-$uv_version-$target"
venv="$runtime/venv-$target"
python="$venv/bin/python"

if [ "${1:-}" = --update ] && [ -x "$python" ]; then
    exec "$python" -B -m src.backend.update
fi

mkdir -p "$runtime"
if [ ! -x "$uv" ]; then
    (
        printf '%s\n' 'Downloading uv...'
        download_dir=$(mktemp -d "$runtime/uv-install.XXXXXX")
        trap 'rm -rf -- "$download_dir"' EXIT
        trap 'exit 130' INT
        trap 'exit 143' TERM

        archive="$download_dir/uv.tar.gz"
        curl --proto '=https' --proto-redir '=https' --location --fail --silent --show-error \
            --connect-timeout 15 --retry 3 --output "$archive" \
            "https://github.com/astral-sh/uv/releases/download/$uv_version/uv-$target.tar.gz"
        actual=$(shasum -a 256 < "$archive")
        if [ "${actual%% *}" != "$checksum" ]; then
            printf '%s\n' 'The downloaded uv archive failed SHA-256 verification.' >&2
            exit 1
        fi
        tar -xzf "$archive" -C "$download_dir" "uv-$target/uv"
        mv -f -- "$download_dir/uv-$target/uv" "$uv"
    )
fi

export UV_PYTHON_INSTALL_DIR="$runtime/python"
export UV_CACHE_DIR="$runtime/cache"
if [ ! -x "$python" ] || [ ! -s "$venv/pyvenv.cfg" ]; then
    "$uv" --no-config venv --allow-existing --managed-python --python 3.12 "$venv"
fi

if [ "${1:-}" = --update ]; then
    exec "$python" -B -m src.backend.update
fi

if ! "$uv" --offline --no-config pip install --python "$python" --only-binary :all: -r requirements.txt; then
    "$uv" --no-config pip install --python "$python" --only-binary :all: -r requirements.txt
fi
if ! "$uv" --no-config pip install --python "$python" --only-binary :all: --upgrade-package yt-dlp -r requirements.txt; then
    "$uv" --offline --no-config pip install --python "$python" --only-binary :all: -r requirements.txt
    printf '%s\n' 'Could not update yt-dlp. Continuing with the installed version.' >&2
fi
"$python" -m src.backend.bootstrap_runtime "$@"
