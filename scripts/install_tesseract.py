#!/usr/bin/env python3
"""scripts/install_tesseract.py — Auto-install Tesseract OCR on Windows (T-052).

Downloads the UB-Mannheim installer (latest 5.x), runs it silently, and
writes the install path to TESSERACT_CMD in .env so pytesseract can find it.

Usage:
    python scripts/install_tesseract.py
    python scripts/install_tesseract.py --check   # just check if installed
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# UB-Mannheim latest 5.x MSI (64-bit)
_INSTALLER_URL = (
    "https://github.com/UB-Mannheim/tesseract/releases/download/"
    "v5.3.3.20231005/tesseract-ocr-w64-setup-5.3.3.20231005.exe"
)

_COMMON_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def _find_existing() -> str | None:
    for p in _COMMON_PATHS:
        if Path(p).exists():
            return p
    # Check PATH
    import shutil
    return shutil.which("tesseract")


def _set_env(cmd: str) -> None:
    """Write TESSERACT_CMD to .env (append or update)."""
    env_path = ROOT / ".env"
    key = "TESSERACT_CMD"
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines = [l for l in lines if not l.startswith(f"{key}=")]
        new_lines.append(f'{key}="{cmd}"')
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f'{key}="{cmd}"\n', encoding="utf-8")
    print(f"[tesseract] {key} written to .env → {cmd}")


def check() -> bool:
    existing = _find_existing()
    if existing:
        print(f"[tesseract] Found: {existing}")
        return True
    print("[tesseract] Not found.")
    return False


def install() -> int:
    if sys.platform != "win32":
        print("[tesseract] This script is for Windows only.")
        print("  Linux:  sudo apt install tesseract-ocr")
        print("  macOS:  brew install tesseract")
        return 1

    existing = _find_existing()
    if existing:
        print(f"[tesseract] Already installed: {existing}")
        _set_env(existing)
        return 0

    print(f"[tesseract] Downloading installer from UB-Mannheim…")
    try:
        import urllib.request
        with tempfile.NamedTemporaryFile(suffix=".exe", delete=False) as f:
            tmp = f.name
        urllib.request.urlretrieve(_INSTALLER_URL, tmp)
        print(f"[tesseract] Downloaded to {tmp}")
    except Exception as e:
        print(f"[tesseract] Download failed: {e}")
        return 1

    print("[tesseract] Running installer (silent, admin required)…")
    try:
        result = subprocess.run(
            [tmp, "/S", "/D=C:\\Program Files\\Tesseract-OCR"],
            check=True,
            timeout=120,
        )
    except subprocess.CalledProcessError as e:
        print(f"[tesseract] Installer failed (exit {e.returncode}) — try running as Administrator")
        return 1
    except FileNotFoundError:
        print("[tesseract] Could not run installer — try running as Administrator")
        return 1
    finally:
        try:
            os.unlink(tmp)
        except Exception:
            pass

    installed = _find_existing()
    if installed:
        print(f"[tesseract] Installed: {installed}")
        _set_env(installed)
        return 0
    else:
        print("[tesseract] Installation may have succeeded but exe not found in default path.")
        print("  Add TESSERACT_CMD=<path> to .env manually.")
        return 1


def main() -> int:
    if "--check" in sys.argv:
        return 0 if check() else 1
    return install()


if __name__ == "__main__":
    sys.exit(main())
