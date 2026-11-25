"""打包脚本：生成 Windows 单文件程序。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
ICON = ROOT / "assets" / "app.ico"


def build(name: str, entry: str) -> int:
    command = [
        str(PYTHON),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        name,
        "--icon",
        str(ICON),
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        "--specpath",
        str(ROOT / "build"),
        str(ROOT / entry),
    ]
    print(" ".join(command))
    return subprocess.run(command, cwd=str(ROOT)).returncode


def copy_exe(name: str, target_name: str) -> Path:
    source = ROOT / "dist" / f"{name}.exe"
    target = ROOT / "dist" / target_name
    shutil.copy2(source, target)
    archive = ROOT / "build" / "dist" / source.name
    archive.parent.mkdir(parents=True, exist_ok=True)
    try:
        archive.unlink(missing_ok=True)
        shutil.move(str(source), str(archive))
    except OSError as exc:
        print(f"提示：未能归档构建中间文件：{exc}")
    print(f"\n完成：{target}")
    print(f"大小：{target.stat().st_size / 1024 / 1024:.1f} MB")
    return target


def main() -> int:
    if not PYTHON.exists():
        print(f"未找到虚拟环境解释器：{PYTHON}")
        return 1
    if not ICON.exists():
        from app.icons import save_ico

        save_ico(ICON)

    code = build("CampusNetAutoLogin", "main.py")
    if code != 0:
        return code
    copy_exe("CampusNetAutoLogin", "校园网自动登录v0.2.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())