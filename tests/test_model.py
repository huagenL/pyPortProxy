"""数据模型测试。

@author ai-lhg
"""


from portproxy.model import Rule, RuleView, RuntimeStatus, new_rule_id, parse_datetime, utc_now


def test_new_rule_id_length():
    rid = new_rule_id()
    assert len(rid) == 12
    assert rid.isalnum()


def test_utc_now_has_tzinfo():
    now = utc_now()
    assert now.tzinfo is not None


class TestRuleSerialization:
    def test_to_dict_camel_case(self):
        now = utc_now()
        rule = Rule(id="abc123", listen_addr="0.0.0.0:80", target_addr="1.2.3.4:80",
                    created_at=now, updated_at=now)
        d = rule.to_dict()
        assert d["id"] == "abc123"
        assert d["listenAddr"] == "0.0.0.0:80"
        assert d["targetAddr"] == "1.2.3.4:80"
        assert "listen_addr" not in d  # 确认用 camelCase

    def test_round_trip(self):
        now = utc_now()
        rule = Rule(id="x", listen_addr="0.0.0.0:1", target_addr="2.2.2.2:2",
                    created_at=now, updated_at=now)
        d = rule.to_dict()
        restored = Rule.from_dict(d)
        assert restored.id == rule.id
        assert restored.listen_addr == rule.listen_addr
        assert restored.created_at.isoformat() == rule.created_at.isoformat()

    def test_remark_round_trip(self):
        now = utc_now()
        rule = Rule(id="r1", listen_addr="0.0.0.0:1", target_addr="2.2.2.2:2",
                    created_at=now, updated_at=now, remark="内网 OA 转发")
        restored = Rule.from_dict(rule.to_dict())
        assert restored.remark == "内网 OA 转发"

    def test_default_remark_empty(self):
        now = utc_now()
        rule = Rule(id="x", listen_addr=":1", target_addr=":2",
                    created_at=now, updated_at=now)
        assert rule.remark == ""

    def test_from_dict_without_remark_go_compat(self):
        """Go 版旧数据文件无 remark 字段：应兼容读取为空字符串。"""
        legacy = {
            "id": "go01",
            "listenAddr": "0.0.0.0:8080",
            "targetAddr": "192.168.1.1:8080",
            "createdAt": "2024-05-01T10:00:00+08:00",
            "updatedAt": "2024-05-01T10:00:00+08:00",
        }
        rule = Rule.from_dict(legacy)
        assert rule.remark == ""

    def test_from_dict_missing_field_raises(self):
        import pytest
        with pytest.raises((KeyError, ValueError)):
            Rule.from_dict({"id": "1", "listenAddr": ":80"})

    def test_from_dict_non_dict_raises(self):
        import pytest
        with pytest.raises(ValueError, match="应为对象"):
            Rule.from_dict("not a dict")


class TestParseDatetime:
    def test_rfc3339_nano_z(self):
        dt = parse_datetime("2024-05-01T10:00:00.123456789Z")
        assert dt.tzinfo is not None
        assert dt.microsecond == 123456

    def test_rfc3339_with_offset(self):
        dt = parse_datetime("2024-05-01T10:00:00+08:00")
        assert dt.tzinfo is not None

    def test_naive_normalized(self):
        dt = parse_datetime("2024-05-01T10:00:00")
        assert dt.tzinfo is not None  # 会变成本地时区后转 UTC

    def test_z_suffix(self):
        dt = parse_datetime("2024-05-01T10:00:00Z")
        assert dt.tzinfo is not None


class TestRuleView:
    def test_frozen(self):
        rule = Rule(id="a", listen_addr="0.0.0.0:80", target_addr="1.1.1.1:80",
                    created_at=utc_now(), updated_at=utc_now())
        view = RuleView(
            rule=rule, status=RuntimeStatus.RUNNING, last_error="", active_connections=3
        )
        assert view.status is RuntimeStatus.RUNNING
        assert view.active_connections == 3
        assert view.last_error == ""
