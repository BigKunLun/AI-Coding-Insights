import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .models import RemoteRule


class ConfigError(ValueError):
    """配置语义错误。错配必须报错中止，不静默兜底：
    include 漏规则若静默退化成全量分析，违反「归属宁漏勿误」。"""


@dataclass
class Config:
    mode: str = "all"  # "all"=个人形态全纳入；"include"=仅纳入 include_remotes 命中的项目
    include_remotes: list[RemoteRule] = field(default_factory=list)
    lookback_days: int = 30
    short_turn_max_chars: int = 6
    business_terms: list[str] = field(default_factory=list)

    @property
    def discovery_rules(self) -> list[RemoteRule] | None:
        """discover_sessions 的 rules 参数：include 给规则列表，all 给 None（跳过归属判定）。
        这是隐私边界的承重开关，派生逻辑只在此一处，调用方不得自行重写三元式。"""
        return self.include_remotes if self.mode == "include" else None


_KNOWN_KEYS = frozenset({"mode", "include_remotes", "lookback_days",
                         "short_turn_max_chars", "business_terms"})
_KNOWN_RULE_KEYS = frozenset({"host", "org"})


def parse_config(data: dict) -> Config:
    # 未知键必须响：拼错键名（include_remote）或遗留键（company_remotes）若静默忽略，
    # 取数范围会悄悄变成 mode=all，违反「归属宁漏勿误」
    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise ConfigError(f"未知配置项：{', '.join(sorted(unknown))}（请改正或删除）")
    raw_rules = data.get("include_remotes", [])
    if not isinstance(raw_rules, list) or not all(isinstance(r, dict) for r in raw_rules):
        raise ConfigError("include_remotes 必须是表数组（[[include_remotes]]）")
    rules = []
    for r in raw_rules:
        extra = set(r) - _KNOWN_RULE_KEYS
        if extra:
            # 典型成因是 TOML 键序：business_terms 等顶层键误置 [[include_remotes]]
            # 之后会归属该表，顶层取不到、脱敏兜底静默置空
            raise ConfigError(f"include_remotes 条目含未知键：{', '.join(sorted(extra))}"
                              "（顶层键须放在所有 [[include_remotes]] 之前）")
        if "host" not in r:
            raise ConfigError("include_remotes 条目缺少 host")
        rules.append(RemoteRule(host=r["host"], org=r.get("org")))
    mode = data.get("mode")
    if mode is None:
        mode = "include" if rules else "all"  # 兼容无 mode 字段的现存配置
    if mode not in ("all", "include"):
        raise ConfigError(f'mode 只能是 "all" 或 "include"，得到 {mode!r}')
    if mode == "include" and not rules:
        raise ConfigError('mode = "include" 但 include_remotes 为空：'
                          '请补充规则，或改 mode = "all"')
    if mode == "all" and rules:
        raise ConfigError('mode = "all" 但配置了 include_remotes：'
                          '自相矛盾，请删除规则或改 mode = "include"')
    bt = data.get("business_terms", [])
    if not isinstance(bt, list):
        raise ConfigError("business_terms 必须是字符串列表（如 [\"词1\", \"词2\"]）")
    try:
        lookback = int(data.get("lookback_days", 30))
        short_max = int(data.get("short_turn_max_chars", 6))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"lookback_days / short_turn_max_chars 必须是整数：{exc}") from exc
    return Config(
        mode=mode,
        include_remotes=rules,
        lookback_days=lookback,
        short_turn_max_chars=short_max,
        business_terms=bt,
    )


def load_config(path: Path | None) -> Config:
    if path is None:
        return Config()  # 零配置 = 个人形态 mode="all"
    try:
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"配置文件不可读或格式错误（{path}）：{exc}") from exc
    return parse_config(data)


DEFAULT_USER_CONFIG = Path.home() / ".claude" / "ai-coding-insights" / "config.toml"


def resolve_config_path(explicit: str | None, plugin_root: str | None) -> Path | None:
    """解析顺序：显式 --config（缺失即错，不静默回退）> 插件根 config.toml（团队
    随插件分发的通道）> 用户级 DEFAULT_USER_CONFIG（个人自管）> None（内置默认 mode="all"）。"""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise ConfigError(f"--config 指定的配置文件不存在：{p}")
        return p
    if plugin_root:
        p = Path(plugin_root) / "config.toml"
        if p.is_file():
            return p
    if DEFAULT_USER_CONFIG.is_file():
        return DEFAULT_USER_CONFIG
    return None
