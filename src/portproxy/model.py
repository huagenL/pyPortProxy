"""数据模型：转发规则与运行时视图。

存储字段名与参考项目（Go 版 dbirder/portproxy）保持一致，
可直接读取其 %APPDATA%/portproxy/rules.json 实现数据无缝迁移。

@author ai-lhg
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


def utc_now() -> datetime:
    """当前 UTC 时间（带时区，保证时间戳可比较）。"""
    return datetime.now(UTC)


def new_rule_id() -> str:
    """生成短规则 ID。"""
    return uuid.uuid4().hex[:12]


class RuntimeStatus(StrEnum):
    """单条规则的运行时状态。"""

    STOPPED = "stopped"
    RUNNING = "running"
    ERROR = "error"


def parse_datetime(value: str) -> datetime:
    """解析 ISO-8601 / RFC3339(含 Go RFC3339Nano) 时间字符串为 aware UTC。

    @author ai-lhg
    """
    text = value.strip()
    # Python 3.11+ 的 fromisoformat 兼容 'Z' 后缀与超长小数秒截断
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        # 无时区的旧数据按本地时区理解后归一化为 UTC
        dt = dt.astimezone()
    return dt.astimezone(UTC)


@dataclass
class Rule:
    """一条 TCP 转发规则。"""

    id: str
    listen_addr: str
    target_addr: str
    created_at: datetime
    updated_at: datetime
    remark: str = ""  # 备注：用于区分规则用途（Go 版旧数据无此字段，默认为空）

    def to_dict(self) -> dict[str, Any]:
        """序列化（camelCase 字段名与 Go 版一致，remark 为扩展字段）。"""
        return {
            "id": self.id,
            "listenAddr": self.listen_addr,
            "targetAddr": self.target_addr,
            "createdAt": self.created_at.isoformat(),
            "updatedAt": self.updated_at.isoformat(),
            "remark": self.remark,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> Rule:
        """反序列化；字段缺失/类型错误抛 ValueError 或 KeyError。

        兼容 Go 版旧数据：remark 缺失时取空字符串。
        """
        if not isinstance(raw, dict):
            raise ValueError(f"规则条目应为对象: {raw!r}")
        return cls(
            id=str(raw["id"]),
            listen_addr=str(raw["listenAddr"]),
            target_addr=str(raw["targetAddr"]),
            created_at=parse_datetime(str(raw["createdAt"])),
            updated_at=parse_datetime(str(raw["updatedAt"])),
            remark=str(raw.get("remark", "")),
        )


@dataclass(frozen=True)
class RuleView:
    """UI 展示用的只读快照行。"""

    rule: Rule
    status: RuntimeStatus = RuntimeStatus.STOPPED
    last_error: str = ""
    active_connections: int = 0
