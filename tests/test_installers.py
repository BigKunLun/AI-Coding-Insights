"""统一安装器契约测试。

安装器是**每家 harness 一条新接缝**（spec §3.4：「每个适配器是一条新接缝，各配一条契约测试」）。
接缝失配在本项目的危害不是报错而是**不报错**——playbook 落错位置、frontmatter 少字段、
或者静默走了降级编排却没在报告里说，用户拿到的都是「看起来正常的错东西」。故这份测试
把三件事钉死：

1. **覆盖面**：`ADAPTERS` 必须与 `sources.SOURCE_NAMES` 一一对应，新增来源忘了配适配器即红。
2. **占位符分层**：安装期占位符（`<ACI>` / `<SOURCE>`）必须被替换干净，
   运行期占位符（`<PLUGIN_ROOT>` / `<BATCHES_DIR>` / …）必须**原样保留**——
   后者的值由 LLM 从 scan 清单现取，安装器碰它就是 bug。
3. **降级不静默**：无子代理的 harness 渲染出的 playbook 必须同时含「替代编排指引」与
   「强制写进报告的降级告知」两段。
"""
import dataclasses
import inspect

import pytest

from ai_coding_insights import sources
from ai_coding_insights.installers import (
    ADAPTERS,
    DEGRADED_MARKER,
    DEGRADED_ORCHESTRATION,
    DEGRADED_REPORT_CAVEAT,
    INSTALL_PLACEHOLDERS,
    Adapter,
    InstallError,
    do_install,
    plan_install,
    render_playbook,
    session_root_conflict,
    unsubstituted_placeholders,
)

# ---------------------------------------------------------------- 夹具

# 样例 playbook：前面是一份「旧」frontmatter（必须被整段换掉），正文同时含
# 安装期占位符与全部运行期占位符。
SAMPLE = """---
description: 旧的描述，安装后不该再出现
disable-model-invocation: true
allowed-tools: Bash(uv run *)
---

# 正文标题

跑 `<ACI> scan --source <SOURCE> --emit-batches <BATCHES_DIR>`。

- 插件根：<PLUGIN_ROOT>
- 批次文件：<BATCH_FILE>，编号 <NN>，共 <N> 场
- 起始时刻：<RUN_STARTED>，并发体 <AGENT_N>
"""

RUNTIME_PLACEHOLDERS = (
    "<PLUGIN_ROOT>", "<BATCHES_DIR>", "<RUN_STARTED>",
    "<AGENT_N>", "<BATCH_FILE>", "<NN>", "<N>",
)


def split_frontmatter(text: str) -> tuple[dict, str]:
    """测试自带的极简 frontmatter 解析器。

    **故意不复用实现里的解析函数**：复用等于拿实现验实现，frontmatter 写歪了测试也跟着歪。
    """
    lines = text.split("\n")
    assert lines[0] == "---", "渲染结果必须以 frontmatter 开头"
    end = lines.index("---", 1)
    fm: dict = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        elif value in ("true", "false"):
            value = value == "true"
        fm[key.strip()] = value
    return fm, "\n".join(lines[end + 1:])


def degraded_adapter() -> Adapter:
    """造一个无子代理的适配器。

    三家在线 harness 目前**都有**子代理（见 installers.py 顶部查证），故降级分支没有
    现成适配器可测。用合成适配器把机制测活，避免它变成一段没人跑过的死代码——
    等哪天真来一家没子代理的 harness，分支已经是验证过的。
    """
    return dataclasses.replace(ADAPTERS[sources.CLAUDE_CODE], has_subagent=False)


# ---------------------------------------------------------------- 覆盖面

def test_adapters_cover_every_source():
    # 新增一家来源却忘了配安装适配器 → 这里红。安装器不允许「悄悄少一家」。
    assert set(ADAPTERS) == set(sources.SOURCE_NAMES)


def test_adapter_key_matches_its_source_field():
    for name, adapter in ADAPTERS.items():
        assert adapter.source == name


def test_adapter_source_is_resolvable_by_sources_registry():
    for adapter in ADAPTERS.values():
        assert sources.get_source(adapter.source).name == adapter.source


def test_every_adapter_carries_doc_url():
    # 路径与 frontmatter 都是查证得来的事实，必须留可复查的出处。
    for adapter in ADAPTERS.values():
        assert adapter.doc_url.startswith("https://")


def test_every_adapter_label_matches_source_label():
    for adapter in ADAPTERS.values():
        assert adapter.label == sources.get_source(adapter.source).label


# ---------------------------------------------------------------- 占位符分层

def test_install_placeholders_are_substituted():
    for adapter in ADAPTERS.values():
        out = render_playbook(SAMPLE, adapter)
        assert "<ACI>" not in out
        assert "<SOURCE>" not in out
        assert adapter.command_prefix in out
        assert f"--source {adapter.source}" in out


def test_runtime_placeholders_survive_verbatim():
    # 承重：这些值由 LLM 从 scan 清单现取，安装期替换掉就等于把运行时数据钉死成常量。
    for adapter in ADAPTERS.values():
        out = render_playbook(SAMPLE, adapter)
        for token in RUNTIME_PLACEHOLDERS:
            assert token in out, f"{adapter.source} 弄丢了运行期占位符 {token}"


def test_render_leaves_no_unsubstituted_install_placeholder():
    for adapter in ADAPTERS.values():
        assert unsubstituted_placeholders(render_playbook(SAMPLE, adapter)) == []


def test_unsubstituted_detects_leftovers():
    assert unsubstituted_placeholders("跑 <ACI> 看 <SOURCE>") == ["<ACI>", "<SOURCE>"]
    assert unsubstituted_placeholders("只剩 <SOURCE>") == ["<SOURCE>"]


def test_unsubstituted_ignores_runtime_placeholders():
    # 运行期占位符残留是**正常状态**，不该被安装器报成未替换。
    assert unsubstituted_placeholders(" ".join(RUNTIME_PLACEHOLDERS)) == []


def test_install_placeholder_set_is_exact():
    """安装期占位符是**封闭集合**：加一个就必须同步 render_playbook 的替换逻辑。

    只加进这个元组而忘了在 `render_playbook` 里替换，表现是渲染后残留占位符；
    只在 render_playbook 里替换而忘了登记，表现是 `unsubstituted_placeholders`
    永远查不到它——后者更坏，因为它让「渲染干净了吗」这道自检形同虚设。
    `<PLUGIN_ROOT_OPT>`：只有 CC 插件形态渲染成 `--plugin-root ${CLAUDE_PLUGIN_ROOT}`，
    别家渲染成空串（别家该变量恒为空，留着会让下一个参数被吞掉）。
    """
    assert set(INSTALL_PLACEHOLDERS) == {"<ACI>", "<SOURCE>", "<PLUGIN_ROOT_OPT>"}
    源码 = inspect.getsource(render_playbook)
    for ph in INSTALL_PLACEHOLDERS:
        assert f'"{ph}"' in 源码, f"{ph} 登记了却没在 render_playbook 里替换"


# ---------------------------------------------------------------- frontmatter

def test_frontmatter_is_replaced_wholesale():
    for adapter in ADAPTERS.values():
        fm, body = split_frontmatter(render_playbook(SAMPLE, adapter))
        assert fm == adapter.frontmatter
        assert "旧的描述" not in body, "旧 frontmatter 不能漏进正文"


def test_frontmatter_shapes_match_each_harness():
    # Claude Code：个人级 skill，CC 专属键（argument-hint / allowed-tools）可用。
    cc = ADAPTERS[sources.CLAUDE_CODE].frontmatter
    assert cc["name"] and cc["description"]
    assert "allowed-tools" in cc

    # Codex：skill 规范只认 name + description，argument-hint 不是合法 skill 字段，
    # 多写字段是给未来的解析器埋雷 → 只出这两个键。
    codex = ADAPTERS[sources.CODEX].frontmatter
    assert set(codex) == {"name", "description"}

    # opencode：command 的 frontmatter 里没有 name（命令名取自文件名），有 agent。
    oc = ADAPTERS[sources.OPENCODE].frontmatter
    assert "name" not in oc
    assert oc["description"]
    assert oc["agent"] == "build"


def test_body_content_survives_render():
    for adapter in ADAPTERS.values():
        _, body = split_frontmatter(render_playbook(SAMPLE, adapter))
        assert "# 正文标题" in body


def test_render_without_frontmatter_still_produces_one():
    out = render_playbook("# 裸正文 <ACI>", ADAPTERS[sources.CODEX])
    fm, body = split_frontmatter(out)
    assert fm == ADAPTERS[sources.CODEX].frontmatter
    assert "# 裸正文" in body


def test_frontmatter_quotes_yaml_hostile_values():
    # `argument-hint: [days]` 不加引号会被 YAML 读成流式序列 → 必须带引号。
    adapter = dataclasses.replace(
        ADAPTERS[sources.CODEX],
        frontmatter={"argument-hint": "[days]", "description": "a: b", "flag": True},
    )
    out = render_playbook(SAMPLE, adapter)
    assert 'argument-hint: "[days]"' in out
    assert 'description: "a: b"' in out
    assert "flag: true" in out


# ---------------------------------------------------------------- 降级断点

def test_degraded_render_carries_both_breakpoints():
    out = render_playbook(SAMPLE, degraded_adapter())
    assert DEGRADED_MARKER in out
    assert DEGRADED_ORCHESTRATION in out, "缺替代编排指引：无子代理却没告诉它怎么改跑法"
    assert DEGRADED_REPORT_CAVEAT in out, "缺报告告知：静默劣化是本项目最危险故障"


def test_degraded_orchestration_names_the_replacement_flow():
    # 只喊「降级了」没用，得说清改成什么跑法。
    assert "单轮顺序" in DEGRADED_ORCHESTRATION
    assert "逐批" in DEGRADED_ORCHESTRATION
    assert "逐维" in DEGRADED_ORCHESTRATION


def test_degraded_caveat_forces_it_into_the_report():
    # 必须是「写进报告小结」的强指令，而不只是给编排端看一眼。
    assert "报告" in DEGRADED_REPORT_CAVEAT
    assert "降级编排" in DEGRADED_REPORT_CAVEAT
    assert "深度低于并行版" in DEGRADED_REPORT_CAVEAT


def test_degraded_block_sits_before_the_body():
    out = render_playbook(SAMPLE, degraded_adapter())
    assert out.index(DEGRADED_MARKER) < out.index("# 正文标题")


def test_shipped_adapters_are_not_degraded():
    for adapter in ADAPTERS.values():
        out = render_playbook(SAMPLE, adapter)
        assert adapter.has_subagent, f"{adapter.source} 被标成无子代理，与查证结论不符"
        assert DEGRADED_MARKER not in out
        assert DEGRADED_REPORT_CAVEAT not in out


def test_degraded_flag_surfaces_in_plan(tmp_path):
    adapter = dataclasses.replace(degraded_adapter(), target=lambda: tmp_path / "p.md")
    assert plan_install(SAMPLE, adapter)["degraded"] is True
    ok = dataclasses.replace(adapter, has_subagent=True)
    assert plan_install(SAMPLE, ok)["degraded"] is False


# ---------------------------------------------------------------- 落位安全

def test_shipped_targets_live_under_home(monkeypatch):
    from pathlib import Path
    # 清掉可能把 opencode 配置目录挪走的环境变量，测的是缺省落位。
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    home = Path.home()
    for adapter in ADAPTERS.values():
        target = adapter.target()
        assert target.is_absolute()
        assert "~" not in str(target), "target 必须已展开 ~"
        assert target.is_relative_to(home), f"{adapter.source} 落到了家目录外：{target}"
        assert target.suffix == ".md"


def test_shipped_targets_never_point_at_session_data():
    # 隐私 + 数据完整性双重红线：安装器写进任何一家的会话原文目录都是灾难
    # （污染取数源，且可能被当成会话被解析）。
    for adapter in ADAPTERS.values():
        assert session_root_conflict(adapter.target()) is None


def test_session_root_conflict_flags_each_source_root():
    from pathlib import Path
    for name in sources.SOURCE_NAMES:
        root = Path(sources.get_source(name).default_root).expanduser()
        assert session_root_conflict(root / "x" / "y.md") == name


def test_session_root_conflict_allows_unrelated_paths(tmp_path):
    assert session_root_conflict(tmp_path / "a.md") is None


def test_targets_are_pairwise_distinct():
    assert len({str(a.target()) for a in ADAPTERS.values()}) == len(ADAPTERS)


def test_opencode_target_follows_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    target = ADAPTERS[sources.OPENCODE].target()
    assert target.is_relative_to(tmp_path / "cfg" / "opencode")


# ---------------------------------------------------------------- plan（--print 预演）

def _tmp_adapter(tmp_path, name="out.md"):
    return dataclasses.replace(
        ADAPTERS[sources.CLAUDE_CODE], target=lambda: tmp_path / "sub" / name)


def test_plan_install_shape(tmp_path):
    adapter = _tmp_adapter(tmp_path)
    plan = plan_install(SAMPLE, adapter)
    assert set(plan) == {"source", "target", "bytes", "exists", "frontmatter",
                         "degraded", "invocation"}
    assert plan["source"] == sources.CLAUDE_CODE
    assert plan["target"] == str(tmp_path / "sub" / "out.md")
    assert plan["exists"] is False
    assert plan["frontmatter"] == adapter.frontmatter
    assert plan["bytes"] == len(render_playbook(SAMPLE, adapter).encode("utf-8"))
    # 触发提示：CC 取所在目录名（不是文件名，也不是 frontmatter 的 name）
    assert plan["invocation"] == "/sub"


def test_plan_install_writes_nothing(tmp_path):
    plan_install(SAMPLE, _tmp_adapter(tmp_path))
    assert list(tmp_path.iterdir()) == [], "--print 预演必须零 IO"


def test_plan_install_reports_existing_target(tmp_path):
    adapter = _tmp_adapter(tmp_path)
    do_install(SAMPLE, adapter)
    assert plan_install(SAMPLE, adapter)["exists"] is True


def test_plan_frontmatter_is_a_copy(tmp_path):
    # 返回值被调用方改坏不能反噬 ADAPTERS 里的共享 dict。
    plan = plan_install(SAMPLE, _tmp_adapter(tmp_path))
    plan["frontmatter"]["description"] = "被改坏了"
    assert ADAPTERS[sources.CLAUDE_CODE].frontmatter["description"] != "被改坏了"


# ---------------------------------------------------------------- do_install（唯一 IO）

def test_do_install_creates_parents_and_writes(tmp_path):
    adapter = _tmp_adapter(tmp_path)
    path = do_install(SAMPLE, adapter)
    assert path == tmp_path / "sub" / "out.md"
    text = path.read_text(encoding="utf-8")
    assert text == render_playbook(SAMPLE, adapter)
    assert "<ACI>" not in text and "<PLUGIN_ROOT>" in text


def test_do_install_refuses_to_clobber_existing(tmp_path):
    adapter = _tmp_adapter(tmp_path)
    do_install(SAMPLE, adapter)
    adapter.target().write_text("用户手改过的 playbook", encoding="utf-8")
    with pytest.raises(InstallError):
        do_install(SAMPLE, adapter)
    # 报错后原文必须原封不动。
    assert adapter.target().read_text(encoding="utf-8") == "用户手改过的 playbook"


def test_do_install_force_overwrites(tmp_path):
    adapter = _tmp_adapter(tmp_path)
    adapter.target().parent.mkdir(parents=True)
    adapter.target().write_text("旧内容", encoding="utf-8")
    path = do_install(SAMPLE, adapter, force=True)
    assert path.read_text(encoding="utf-8") == render_playbook(SAMPLE, adapter)


def test_do_install_rejects_session_directory_target(tmp_path):
    from pathlib import Path
    root = Path(sources.get_source(sources.CLAUDE_CODE).default_root).expanduser()
    adapter = dataclasses.replace(
        ADAPTERS[sources.CLAUDE_CODE], target=lambda: root / "boom" / "SKILL.md")
    with pytest.raises(InstallError):
        do_install(SAMPLE, adapter)
    assert not (root / "boom").exists()


def test_do_install_is_idempotent_under_force(tmp_path):
    adapter = _tmp_adapter(tmp_path)
    first = do_install(SAMPLE, adapter).read_text(encoding="utf-8")
    second = do_install(SAMPLE, adapter, force=True).read_text(encoding="utf-8")
    assert first == second
