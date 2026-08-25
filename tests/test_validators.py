"""地址校验测试。

@author ai-lhg
"""

import pytest

from portproxy.validators import ValidationError, split_host_port, validate_address


class TestSplitHostPort:
    def test_ipv4_standard(self):
        assert split_host_port("0.0.0.0:8080") == ("0.0.0.0", 8080)

    def test_ipv4_loopback(self):
        assert split_host_port("127.0.0.1:80") == ("127.0.0.1", 80)

    def test_localhost(self):
        assert split_host_port("localhost:3000") == ("localhost", 3000)

    def test_ipv6_bracket(self):
        assert split_host_port("[::1]:9999") == ("::1", 9999)

    def test_hostname(self):
        assert split_host_port("myhost:1080") == ("myhost", 1080)

    def test_port_boundary(self):
        assert split_host_port("host:1") == ("host", 1)
        assert split_host_port("host:65535") == ("host", 65535)

    def test_port_out_of_range(self):
        with pytest.raises(ValidationError, match="超出范围"):
            split_host_port("host:0")
        with pytest.raises(ValidationError, match="超出范围"):
            split_host_port("host:70000")

    def test_port_not_number(self):
        with pytest.raises(ValidationError, match="端口必须是数字"):
            split_host_port("host:abc")

    def test_empty(self):
        with pytest.raises(ValidationError, match="地址为空"):
            split_host_port("")

    def test_whitespace_only(self):
        with pytest.raises(ValidationError, match="地址为空"):
            split_host_port("   ")

    def test_no_port(self):
        with pytest.raises(ValidationError, match="缺少主机或端口"):
            split_host_port("host:")

    def test_no_host(self):
        with pytest.raises(ValidationError, match="缺少主机或端口"):
            split_host_port(":8080")

    def test_bare_number(self):
        with pytest.raises(ValidationError, match="缺少主机或端口"):
            split_host_port("8080")


class TestValidateAddress:
    def test_valid_ipv4(self):
        validate_address("0.0.0.0:8080")  # 不抛

    def test_valid_local(self):
        validate_address("localhost:80")  # 不抛

    def test_valid_ipv6_bracket(self):
        validate_address("[::1]:9999")  # 不抛

    def test_invalid_ipv4_value(self):
        with pytest.raises(ValidationError, match="无效的 IPv4 地址"):
            validate_address("300.1.1.1:80")

    def test_invalid_ipv4_segment(self):
        with pytest.raises(ValidationError, match="无效的 IPv4 地址"):
            validate_address("1.2.3.4.5:80")

    def test_ipv6_without_bracket(self):
        with pytest.raises(ValidationError, match="请使用.*主机.*端口.*形式"):
            validate_address("::1:80")

    def test_empty(self):
        with pytest.raises(ValidationError):
            validate_address("")

    def test_no_port(self):
        with pytest.raises(ValidationError, match="缺少主机或端口"):
            validate_address("no-port")
