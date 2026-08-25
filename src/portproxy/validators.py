"""地址校验工具（纯语法级校验，无网络 IO，UI 可安全同步调用）。

真实的主机解析由引擎在启动监听/拨号时完成，失败进入 ERROR 态展示。

@author ai-lhg
"""

from __future__ import annotations

import re


class ValidationError(ValueError):
    """地址格式错误（消息可直接展示给用户）。"""


# [IPv6]:port 形式
_IPV6_BRACKET_RE = re.compile(r"^\[(?P<host>[0-9a-fA-F:.]+)\]:(?P<port>\d+)$")
# IPv4 点分十进制段
_IPV4_SEG_RE = re.compile(r"^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)$")


def _parse_port(text: str) -> int:
    port = int(text)
    if not 1 <= port <= 65535:
        raise ValidationError(f"端口超出范围(1-65535): {port}")
    return port


def split_host_port(addr: str) -> tuple[str, str]:
    """拆分 host 与 port 文本；支持 ``host:port`` 与 ``[IPv6]:port``。

    @author ai-lhg
    """
    if addr is None or not addr.strip():
        raise ValidationError("地址为空")
    text = addr.strip()
    if text.startswith("["):
        matched = _IPV6_BRACKET_RE.match(text)
        if not matched:
            raise ValidationError(f"无效的 IPv6 地址格式，应为 [主机]:端口: {text}")
        return matched.group("host"), _parse_port(matched.group("port"))

    host, sep, port_part = text.rpartition(":")
    if not sep or not host or not port_part:
        raise ValidationError(f"缺少主机或端口，应为 host:port 形式: {text}")
    if not port_part.isdigit():
        raise ValidationError(f"端口必须是数字: {text}")
    return host, _parse_port(port_part)


def _validate_ipv4(host: str) -> None:
    segments = host.split(".")
    if len(segments) != 4:
        raise ValidationError(f"无效的 IPv4 地址: {host}")
    for seg in segments:
        if not _IPV4_SEG_RE.match(seg):
            raise ValidationError(f"无效的 IPv4 地址: {host}")


def validate_address(addr: str) -> None:
    """校验 ``host:port`` 语法与端口范围；失败抛 :class:`ValidationError`。

    @author ai-lhg
    """
    text = (addr or "").strip()
    if text.startswith("["):
        # 显式方括号 IPv6 形式：能成功拆分即合法
        split_host_port(text)
        return
    host, _ = split_host_port(text)
    if ":" in host:
        # 裸 IPv6 必须加方括号书写，避免歧义
        raise ValidationError(f"IPv6 地址请使用 [主机]:端口 形式: {text}")
    if host.lower() not in {"localhost"} and not _looks_like_hostname(host):
        raise ValidationError(f"无效的主机名或 IP: {host}")


def _looks_like_hostname(host: str) -> bool:
    """数字 IP 则校验点分段范围；否则按宽松主机名校验。"""
    if host.replace(".", "").isdigit():
        _validate_ipv4(host)
        return True
    if len(host) > 253:
        return False
    return all(label for label in host.split(".")) and " " not in host
