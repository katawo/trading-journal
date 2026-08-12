"""Build a portable Trading Journal bundle for the current operating system."""

from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILD_ROOT = PROJECT_ROOT / "build" / "pyinstaller"
DIST_ROOT = PROJECT_ROOT / "dist"
RELEASE_ROOT = PROJECT_ROOT / "release"
APPLICATION_NAME = "TradingJournal"


def _data_argument(source: Path, destination: str) -> str:
    return f"{source}{os.pathsep}{destination}"


def main() -> int:
    system = platform.system().lower()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        APPLICATION_NAME,
        "--paths",
        str(PROJECT_ROOT / "src"),
        # app.py is bundled as data and launched later by Streamlit, so its
        # imports are not visible to PyInstaller's normal static analysis.
        "--collect-submodules",
        "trading_journal",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT / "work"),
        "--specpath",
        str(BUILD_ROOT / "spec"),
        "--collect-all",
        "streamlit",
        "--collect-all",
        "plotly",
        "--add-data",
        _data_argument(PROJECT_ROOT / "app.py", "."),
        "--add-data",
        _data_argument(PROJECT_ROOT / "app_pages", "app_pages"),
        "--add-data",
        _data_argument(PROJECT_ROOT / "docs", "docs"),
        "--add-data",
        _data_argument(PROJECT_ROOT / ".streamlit", ".streamlit"),
        "--add-data",
        _data_argument(PROJECT_ROOT / "mql5", "mql5"),
    ]
    if system == "windows":
        command.append("--windowed")
    command.append(str(PROJECT_ROOT / "desktop_launcher.py"))
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)

    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    archive_stem = RELEASE_ROOT / f"trading-journal-{system}-x86_64"
    if system == "windows":
        archive = shutil.make_archive(str(archive_stem), "zip", root_dir=DIST_ROOT, base_dir=APPLICATION_NAME)
    else:
        archive = shutil.make_archive(str(archive_stem), "gztar", root_dir=DIST_ROOT, base_dir=APPLICATION_NAME)
    print(f"Portable bundle: {archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
