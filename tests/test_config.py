from pathlib import Path

import pytest

from ai_coding_insights.config import Config, ConfigError, load_config, parse_config
from ai_coding_insights.models import RemoteRule

RULES_TOML = {"include_remotes": [{"host": "git.example.com"},
                                  {"host": "github.com", "org": "example-org"}]}


def test_load_config_include(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        'mode = "include"\nlookback_days = 30\n'
        '[[include_remotes]]\nhost = "git.example.com"\n'
        '[[include_remotes]]\nhost = "github.com"\norg = "example-org"\n'
    )
    cfg = load_config(p)
    assert cfg.mode == "include"
    assert cfg.lookback_days == 30
    assert RemoteRule("git.example.com") in cfg.include_remotes
    assert RemoteRule("github.com", "example-org") in cfg.include_remotes


def test_load_config_none_is_builtin_all():
    cfg = load_config(None)
    assert cfg.mode == "all"
    assert cfg.include_remotes == []
    assert cfg.lookback_days == 30


def test_parse_mode_all_explicit():
    assert parse_config({"mode": "all"}).mode == "all"


def test_parse_mode_missing_with_rules_is_include():
    # 向后兼容：现存团队配置无 mode 字段
    assert parse_config(RULES_TOML).mode == "include"


def test_parse_mode_missing_no_rules_is_all():
    assert parse_config({}).mode == "all"


def test_parse_include_without_rules_raises():
    with pytest.raises(ConfigError):
        parse_config({"mode": "include"})


def test_parse_all_with_rules_raises():
    # 自相矛盾的错配必须响，不静默裁决
    with pytest.raises(ConfigError):
        parse_config({"mode": "all", **RULES_TOML})


def test_parse_unknown_mode_raises():
    with pytest.raises(ConfigError):
        parse_config({"mode": "blacklist"})


def test_parse_business_terms_must_be_list():
    # 字符串会被逐字符迭代，脱敏兜底网静默退化——必须响
    with pytest.raises(ConfigError):
        parse_config({"business_terms": "secret"})


def test_parse_rule_missing_host_raises():
    # 缺 host 若抛裸 KeyError 会绕过 main 的 ConfigError 出口（traceback 而非退出码 2）
    with pytest.raises(ConfigError):
        parse_config({"include_remotes": [{"org": "example-org"}]})


def test_parse_include_remotes_not_table_array_raises():
    # include_remotes = "host" 会被逐字符迭代——必须响
    with pytest.raises(ConfigError):
        parse_config({"include_remotes": "git.example.com"})


def test_parse_unknown_top_level_key_raises():
    # 拼错键名（include_remote）或遗留键（company_remotes）若静默忽略，
    # 取数范围会悄悄变成 mode=all——错配必须响
    with pytest.raises(ConfigError):
        parse_config({"include_remote": [{"host": "git.example.com"}]})
    with pytest.raises(ConfigError):
        parse_config({"company_remotes": [{"host": "git.example.com"}]})


def test_parse_rule_unknown_key_raises():
    # business_terms 误置 [[include_remotes]] 之后会归属该表，顶层取不到、
    # 脱敏兜底静默置空——这个 TOML 键序坑必须有代码防线，不能只靠示例注释
    with pytest.raises(ConfigError):
        parse_config({"include_remotes": [
            {"host": "git.example.com", "business_terms": ["x"]}]})


def test_parse_non_integer_lookback_raises():
    with pytest.raises(ConfigError):
        parse_config({"lookback_days": "abc"})


def test_discovery_rules_derivation():
    # discover_sessions 的 rules 参数语义：include 给规则，all 给 None（跳过归属判定）
    inc = parse_config(RULES_TOML)
    assert inc.discovery_rules == inc.include_remotes
    assert Config().discovery_rules is None


from ai_coding_insights.config import resolve_config_path


def test_load_config_unparseable_raises_config_error(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("mode = [broken")
    with pytest.raises(ConfigError):
        load_config(p)


def test_resolve_explicit_missing_raises(tmp_path):
    with pytest.raises(ConfigError):
        resolve_config_path(str(tmp_path / "nope.toml"), None)


def test_resolve_explicit_wins_over_plugin_root(tmp_path):
    explicit = tmp_path / "e.toml"; explicit.write_text("")
    root = tmp_path / "root"; root.mkdir(); (root / "config.toml").write_text("")
    assert resolve_config_path(str(explicit), str(root)) == explicit


def test_resolve_plugin_root_wins_over_user(tmp_path, monkeypatch):
    import ai_coding_insights.config as config
    user = tmp_path / "user.toml"; user.write_text("")
    monkeypatch.setattr(config, "DEFAULT_USER_CONFIG", user)
    root = tmp_path / "root"; root.mkdir(); (root / "config.toml").write_text("")
    assert resolve_config_path(None, str(root)) == root / "config.toml"


def test_resolve_user_fallback(tmp_path, monkeypatch):
    import ai_coding_insights.config as config
    user = tmp_path / "user.toml"; user.write_text("")
    monkeypatch.setattr(config, "DEFAULT_USER_CONFIG", user)
    assert resolve_config_path(None, str(tmp_path / "no-root")) == user


def test_resolve_nothing_returns_none(tmp_path):
    # conftest 已把 DEFAULT_USER_CONFIG 隔离到不存在的 tmp 路径
    assert resolve_config_path(None, None) is None
