"""开发与打包通用入口。

@author ai-lhg
"""

import sys
from pathlib import Path

# 保证 src layout 下直接 `python main.py` 可运行；PyInstaller 打包同样以本文件为入口
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from portproxy.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
