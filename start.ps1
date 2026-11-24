$ErrorActionPreference = 'Stop'
$update = $args.Count -gt 0 -and $args[0] -eq '--update'

if ($update -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.git'))) {
    [Console]::Error.WriteLine("This folder is a Git checkout. Run 'git pull' to update it.")
    exit 1
}

$architecture = $env:PROCESSOR_ARCHITEW6432
if (!$architecture) {
    $architecture = $env:PROCESSOR_ARCHITECTURE
}
if ($architecture -ne 'AMD64') {
    throw 'This launcher requires Windows on x64.'
}

if (!$update -and !(Get-CimInstance Win32_VideoController | Where-Object Name -Like 'NVIDIA*')) {
    throw 'This launcher requires an NVIDIA GPU.'
}

$uvVersion = '0.13.9'
$checksum = 'edd61f7f5728a56cc62455386661860bdfc7bab65872f39b0c25bdcc1d8e1beb'
$runtime = Join-Path $PSScriptRoot '.runtime'
$uv = Join-Path $runtime "uv-$uvVersion-x86_64-pc-windows-msvc.exe"
$venv = Join-Path $runtime 'venv-x86_64-pc-windows-msvc'
$python = Join-Path $venv 'Scripts\python.exe'

if ($update -and (Test-Path -LiteralPath $python -PathType Leaf)) {
    Push-Location -LiteralPath $PSScriptRoot
    try {
        & $python -B -m src.backend.update
        exit $LASTEXITCODE
    } finally {
        Pop-Location
    }
}

if (!(Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue)) {
    throw 'This launcher requires curl.exe, which is missing from PATH.'
}

[void][IO.Directory]::CreateDirectory($runtime)
if (!(Test-Path -LiteralPath $uv -PathType Leaf)) {
    Write-Host 'Downloading uv...'
    $downloadDir = Join-Path $runtime ("uv-install-" + [guid]::NewGuid().ToString('N'))
    [void][IO.Directory]::CreateDirectory($downloadDir)
    try {
        $archive = Join-Path $downloadDir 'uv.zip'
        $url = "https://github.com/astral-sh/uv/releases/download/$uvVersion/uv-x86_64-pc-windows-msvc.zip"
        & curl.exe --proto '=https' --proto-redir '=https' --location --fail --silent --show-error `
            --connect-timeout 15 --retry 3 --output $archive $url
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $checksum) {
            throw 'The downloaded uv archive failed SHA-256 verification.'
        }
        Expand-Archive -LiteralPath $archive -DestinationPath $downloadDir
        try {
            [IO.File]::Move((Join-Path $downloadDir 'uv.exe'), $uv)
        } catch [IO.IOException] {
            if (!(Test-Path -LiteralPath $uv -PathType Leaf)) {
                throw
            }
        }
    } finally {
        Remove-Item -LiteralPath $downloadDir -Recurse -Force
    }
}

$previousPythonDir = $env:UV_PYTHON_INSTALL_DIR
$previousCacheDir = $env:UV_CACHE_DIR
Push-Location -LiteralPath $PSScriptRoot
try {
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'python'
    $env:UV_CACHE_DIR = Join-Path $runtime 'cache'
    if (!(Test-Path -LiteralPath $python -PathType Leaf) -or
        !(Test-Path -LiteralPath (Join-Path $venv 'pyvenv.cfg') -PathType Leaf)) {
        & $uv --no-config venv --allow-existing --managed-python --python '3.12' $venv
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }

    if ($update) {
        & $python -B -m src.backend.update
        exit $LASTEXITCODE
    }

    & $uv --offline --no-config pip install --python $python --only-binary ':all:' --torch-backend cu130 -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        & $uv --no-config pip install --python $python --only-binary ':all:' --torch-backend cu130 -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    & $uv --no-config pip install --python $python --only-binary ':all:' --torch-backend cu130 --upgrade-package yt-dlp -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        & $uv --offline --no-config pip install --python $python --only-binary ':all:' --torch-backend cu130 -r requirements.txt
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
        Write-Warning 'Could not update yt-dlp. Continuing with the installed version.'
    }
    & $python -c 'import sys, torch; sys.exit(0 if torch.cuda.is_available() else 1)'
    if ($LASTEXITCODE -ne 0) {
        throw 'PyTorch cannot use CUDA. Update the NVIDIA driver to version 580 or later.'
    }
    & $python -m src.backend.bootstrap_runtime @args
    exit $LASTEXITCODE
} finally {
    $env:UV_PYTHON_INSTALL_DIR = $previousPythonDir
    $env:UV_CACHE_DIR = $previousCacheDir
    Pop-Location
}
