"""存储层测试。

@author ai-lhg
"""

import json

import pytest

from portproxy.model import Rule, utc_now
from portproxy.store import StoreError, load_rules, save_rules


def _make_rule(**overrides):
    now = utc_now()
    defaults = dict(id="abc123", listen_addr="0.0.0.0:80", target_addr="1.2.3.4:80",
                    created_at=now, updated_at=now)
    defaults.update(overrides)
    return Rule(**defaults)


class TestLoadRules:
    def test_file_not_exist_returns_empty(self, tmp_path):
        assert load_rules(tmp_path / "nope.json") == []

    def test_empty_file_returns_empty(self, tmp_path):
        (tmp_path / "rules.json").write_text("", encoding="utf-8")
        assert load_rules(tmp_path / "rules.json") == []

    def test_whitespace_only_returns_empty(self, tmp_path):
        (tmp_path / "rules.json").write_text("   \n  ", encoding="utf-8")
        assert load_rules(tmp_path / "rules.json") == []

    def test_valid_json_array(self, tmp_path):
        rule = _make_rule()
        data = json.dumps([rule.to_dict()])
        (tmp_path / "rules.json").write_text(data, encoding="utf-8")
        loaded = load_rules(tmp_path / "rules.json")
        assert len(loaded) == 1
        assert loaded[0].id == rule.id
        assert loaded[0].listen_addr == rule.listen_addr

    def test_bad_json_raises_store_error(self, tmp_path):
        (tmp_path / "rules.json").write_text("{{not json}}", encoding="utf-8")
        with pytest.raises(StoreError, match="格式错误"):
            load_rules(tmp_path / "rules.json")

    def test_non_array_raises_store_error(self, tmp_path):
        (tmp_path / "rules.json").write_text('{"key": "value"}', encoding="utf-8")
        with pytest.raises(StoreError, match="格式错误"):
            load_rules(tmp_path / "rules.json")

    def test_go_rfc3339_nano_compat(self, tmp_path):
        """Go time.Time JSON 为 RFC3339Nano，确认 Python 可正确解析。"""
        go_data = json.dumps([
            {
                "id": "go01",
                "listenAddr": "0.0.0.0:8080",
                "targetAddr": "192.168.1.1:8080",
                "createdAt": "2024-05-01T10:00:00.123456789+08:00",
                "updatedAt": "2024-05-01T10:00:00.987654321+08:00",
            }
        ])
        (tmp_path / "rules.json").write_text(go_data, encoding="utf-8")
        loaded = load_rules(tmp_path / "rules.json")
        assert len(loaded) == 1
        assert loaded[0].id == "go01"
        assert loaded[0].listen_addr == "0.0.0.0:8080"
        assert loaded[0].created_at.tzinfo is not None


class TestSaveRules:
    def test_round_trip(self, tmp_path):
        rule = _make_rule()
        save_rules(tmp_path / "rules.json", [rule])
        loaded = load_rules(tmp_path / "rules.json")
        assert len(loaded) == 1
        assert loaded[0].id == rule.id

    def test_atomic_no_tmp_residue(self, tmp_path):
        rule = _make_rule()
        save_rules(tmp_path / "rules.json", [rule])
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert len(tmp_files) == 0

    def test_creates_parent_dirs(self, tmp_path):
        nested = tmp_path / "a" / "b" / "rules.json"
        rule = _make_rule()
        save_rules(nested, [rule])
        assert nested.exists()

    def test_sorted_output(self, tmp_path):
        from datetime import timedelta
        r1 = _make_rule(id="r1", created_at=utc_now() + timedelta(hours=2))
        r2 = _make_rule(id="r2", created_at=utc_now())
        save_rules(tmp_path / "rules.json", [r1, r2])
        loaded = load_rules(tmp_path / "rules.json")
        assert loaded[0].id == "r2"  # 较早的排在前面
        assert loaded[1].id == "r1"
