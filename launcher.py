"""每日复盘 · 启动器入口（桌面快捷方式指向本文件）。

双击 启动.bat（或桌面快捷方式「每日复盘」）→ pythonw 运行本脚本
→ 把 src 加入 sys.path → 打开 tkinter 图形窗口。

命令行也可直接用：
    "E:/conda_envs/envs/mowan_dm/python.exe" -m daily_review launch [--dry-run]
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from daily_review.launcher import run_gui  # noqa: E402

if __name__ == "__main__":
    sys.exit(run_gui())
