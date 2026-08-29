"""运行时目录约定。

源码检出和已安装包共用这一处路径解析，避免命令行入口把仓库布局当成公开协议。
"""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parents[1]
_ASSET_ROOT = PACKAGE_ROOT / "assets"


def is_source_checkout() -> bool:
    """当前解释器是否直接从项目源码目录加载。"""

    return (SOURCE_ROOT / "pyproject.toml").is_file() and (SOURCE_ROOT / "web").is_dir()


def asset_root() -> Path:
    """返回随发行包携带的只读资源根目录。"""

    return SOURCE_ROOT if is_source_checkout() else _ASSET_ROOT


def asset_path(*parts: str) -> Path:
    return asset_root().joinpath(*parts)


def python_import_root() -> Path:
    """Observer 注入外部解释器时需要的平台包搜索根。"""

    return SOURCE_ROOT / "src" if is_source_checkout() else PACKAGE_ROOT.parent


def default_data_root() -> Path:
    """开发时沿用 .runtime；安装版不得在 site-packages 内写实验产物。"""

    configured = os.environ.get("REGRESSION_LAB_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    if is_source_checkout():
        return SOURCE_ROOT / ".runtime"
    return Path.home() / ".regression-lab"


def runtime_root(data_dir: str | Path | None = None) -> Path:
    return Path(data_dir).expanduser().resolve() if data_dir else default_data_root()
