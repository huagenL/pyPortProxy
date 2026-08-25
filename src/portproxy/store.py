"""规则 JSON 持久化存储（临时文件 + 原子替换）。

@author ai-lhg
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from portproxy.model import Rule


class StoreError(RuntimeError):
    """存储层错误（消息可直接展示给用户）。"""


def load_rules(path: str | Path) -> list[Rule]:
    """从 JSON 文件加载规则列表。

    文件不存在或为空视为空配置；解析失败抛 StoreError。
    """
    file_path = Path(path)
    try:
        data = file_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise StoreError(f"读取规则文件失败: {exc}") from exc

    if not data.strip():
        return []
    try:
        raw_list = json.loads(data)
        if not isinstance(raw_list, list):
            raise ValueError("规则文件内容应为 JSON 数组")
        rules = [Rule.from_dict(item) for item in raw_list]
    except (json.JSONDecodeError, ValueError, KeyError) as exc:
        raise StoreError(f"规则文件格式错误: {file_path} ({exc})") from exc
    return sort_rules(rules)


def save_rules(path: str | Path, rules: list[Rule]) -> None:
    """原子化保存：先写 .tmp 再 os.replace 替换目标文件。"""
    file_path = Path(path)
    tmp_path = file_path.with_suffix(file_path.suffix + ".tmp")
    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [rule.to_dict() for rule in sort_rules(rules)],
            ensure_ascii=False,
            indent=2,
        )
        tmp_path.write_text(payload + "\n", encoding="utf-8")
        os.replace(tmp_path, file_path)
    except OSError as exc:
        # 清理残留的临时文件后抛错
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise StoreError(f"保存规则文件失败: {exc}") from exc


def sort_rules(rules: list[Rule]) -> list[Rule]:
    """稳定排序：创建时间升序，其次 ID，保证存储顺序不抖动。"""
    return sorted(rules, key=lambda r: (r.created_at, r.id))
