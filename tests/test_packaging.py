"""打包分发契约测试。

守的是「一条命令跑起来」这条接缝：PyPI 元数据、console script 入口、
零运行时依赖铁律、`scripts/aci` 的 uv → python3 fallback。

这些东西平时不跑测试也不会报错，坏了只有用户装的时候才发现——
所以每一条都钉成断言。
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
ACI_SCRIPT = REPO_ROOT / "scripts" / "aci"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"

# 控制台入口点的期望名字与目标。改名即红——用户装完敲的就是这个词。
EXPECTED_SCRIPT_NAME = "ai-coding-insights"


def _load_pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


@pytest.fixture(scope="module")
def pyproject() -> dict:
    return _load_pyproject()


# --------------------------------------------------------------------------
# 元数据
# --------------------------------------------------------------------------


def test_pyproject_可被_tomllib_解析(pyproject):
    assert pyproject["project"]["name"] == "ai-coding-insights"
    assert pyproject["project"]["version"]


def test_发布元数据字段齐备(pyproject):
    proj = pyproject["project"]
    for field in ("description", "readme", "authors", "keywords", "classifiers"):
        assert proj.get(field), f"pyproject [project] 缺字段: {field}"
    # license 可以是 SPDX 字符串（PEP 639）或 {file=/text=} 表，两种都认
    assert proj.get("license"), "pyproject [project] 缺 license"


def test_readme_指向的文件真实存在(pyproject):
    readme = pyproject["project"]["readme"]
    name = readme if isinstance(readme, str) else readme.get("file")
    assert name, "readme 字段没给出文件名"
    assert (REPO_ROOT / name).is_file(), f"readme 指向的文件不存在: {name}"


def test_license_文件真实存在(pyproject):
    lic = pyproject["project"]["license"]
    if isinstance(lic, dict) and "file" in lic:
        assert (REPO_ROOT / lic["file"]).is_file()
    else:
        # SPDX 字符串形态下，license-files 里的路径要能落到实体文件
        for pattern in pyproject["project"].get("license-files", ["LICENSE"]):
            assert list(REPO_ROOT.glob(pattern)), f"license-files 没匹配到文件: {pattern}"


def test_project_urls_可用且非占位(pyproject):
    urls = pyproject["project"].get("urls")
    assert urls, "缺 [project.urls]"
    for key in ("Homepage", "Repository", "Issues"):
        assert key in urls, f"[project.urls] 缺 {key}"
        assert urls[key].startswith("https://"), f"{key} 不是 https URL"
        assert "example.com" not in urls[key], f"{key} 是占位 URL"


def test_版本号符合_pep440(pyproject):
    version = pyproject["project"]["version"]
    # 只做形态校验：N(.N)*，可带 aN/bN/rcN 预发布后缀
    assert re.fullmatch(r"\d+(\.\d+)*((a|b|rc)\d+)?", version), f"版本号不合 PEP 440: {version}"


# --------------------------------------------------------------------------
# 两处版本号（PyPI ↔ CC plugin）必须表达同一个版本
# --------------------------------------------------------------------------

# PEP 440 预发布段的三种写法 → 规范化标签。plugin.json 走 semver，只能写
# `-alpha.1` 这类；pyproject 走 PEP 440，写的是 `a1`。两套拼写必须能对上。
_PRE_ALIASES = {
    "a": "a", "alpha": "a",
    "b": "b", "beta": "b",
    "rc": "rc", "c": "rc", "pre": "rc", "preview": "rc",
}


def _normalize_version(raw: str) -> tuple:
    """PEP 440 / semver 两种拼法 → 同一个可比较元组 (release, pre_tag, pre_num)。

    做规范化而不是字符串相等：`1.0.0a1`（PEP 440，pyproject 只认这个）与
    `1.0.0-alpha.1`（semver，CC plugin 只认这个）是**同一个版本的两种合法写法**，
    字符串比对会逼着其中一处写成对方不认的形态。
    正式版（无预发布段）返回 pre 为 None。
    """
    text = str(raw).strip()
    m = re.fullmatch(
        r"v?(\d+(?:\.\d+)*)"                       # release 段
        r"(?:[-._]?([A-Za-z]+)[-._]?(\d+))?",      # 可选预发布段：a1 / -alpha.1 / .rc2
        text,
    )
    assert m, f"版本号既不是 PEP 440 也不是 semver 的形态: {raw!r}"
    release = tuple(int(x) for x in m.group(1).split("."))
    # 末尾补零到 3 段：semver 恒 3 段，PEP 440 允许 `1.0`，两者要能对上
    release = release + (0,) * (3 - len(release)) if len(release) < 3 else release
    if m.group(2) is None:
        return (release, None, None)
    tag = _PRE_ALIASES.get(m.group(2).lower())
    assert tag, f"无法识别的预发布标签 {m.group(2)!r}（版本号 {raw!r}）"
    return (release, tag, int(m.group(3)))


def test_plugin_json_版本号是合法_semver():
    """CC plugin 清单走 semver：PEP 440 的 `1.0.0a1` 在这里**不是**合法写法。

    这条单独立起来，是因为下面那条规范化比对会把两种拼法都读成同一个版本——
    只有它是拦不住「plugin.json 里直接抄了 PEP 440 字符串」的。
    """
    version = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?(\+[0-9A-Za-z.]+)?", version), (
        f"plugin.json 的 version 不是合法 semver: {version}（预发布段要写成 -alpha.1 这样）"
    )


def test_两处版本号表达同一个版本(pyproject):
    """`pyproject.toml`（PyPI/uvx 入口）与 `.claude-plugin/plugin.json`（CC 插件入口）
    是同一份产物的两个分发面，版本号漂移会让用户装到两个不同的东西还以为是一个。

    比的是**规范化后的版本**，不是字符串：两边的合法拼法本来就不同
    （PEP 440 `1.0.0a1` vs semver `1.0.0-alpha.1`）。
    """
    py_ver = pyproject["project"]["version"]
    plugin_ver = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))["version"]
    assert _normalize_version(py_ver) == _normalize_version(plugin_ver), (
        f"版本号漂移：pyproject={py_ver}、plugin.json={plugin_ver}"
    )


def test_版本号规范化能识破真漂移():
    """守卫本身的守卫：规范化不能宽松到把不同版本也判成相等。"""
    assert _normalize_version("1.0.0a1") == _normalize_version("1.0.0-alpha.1")
    assert _normalize_version("1.0") == _normalize_version("1.0.0")
    assert _normalize_version("1.0.0a1") != _normalize_version("1.0.0a2")
    assert _normalize_version("1.0.0a1") != _normalize_version("1.0.0-beta.1")
    assert _normalize_version("1.0.0a1") != _normalize_version("1.0.0")
    assert _normalize_version("0.1.0") != _normalize_version("1.0.0-alpha.1")


# --------------------------------------------------------------------------
# 铁律：零运行时依赖
# --------------------------------------------------------------------------


def test_零运行时依赖(pyproject):
    """定位级铁律：纯 stdlib。有人偷偷加依赖必须红。"""
    assert pyproject["project"]["dependencies"] == [], (
        "运行时依赖必须为空（纯 stdlib 是定位级铁律），"
        f"当前: {pyproject['project']['dependencies']}"
    )
    assert not pyproject["project"].get("optional-dependencies"), (
        "不接受 optional-dependencies：用户 uvx 一跑就得能用，不给二选一"
    )


# --------------------------------------------------------------------------
# console script 入口
# --------------------------------------------------------------------------


def test_console_script_入口存在(pyproject):
    scripts = pyproject["project"].get("scripts")
    assert scripts, "缺 [project.scripts]"
    assert EXPECTED_SCRIPT_NAME in scripts, f"[project.scripts] 缺 {EXPECTED_SCRIPT_NAME}"


def test_console_script_入口能真的_import_到(pyproject):
    """按 `module:func` 解析并 getattr——入口改名 / 挪窝即红。"""
    target = pyproject["project"]["scripts"][EXPECTED_SCRIPT_NAME]
    assert ":" in target, f"入口点格式应为 module:func，实际: {target}"
    module_name, _, attr = target.partition(":")

    module = importlib.import_module(module_name)
    func = getattr(module, attr, None)
    assert callable(func), f"{target} 不可调用"

    # console script 包装器是零参调用 `main()`，所以入口必须能不传参就跑
    sig = inspect.signature(func)
    required = [
        p
        for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty
        and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
    ]
    assert not required, f"入口函数不能有必填参数（console script 是零参调用），实际: {required}"


def test_入口函数返回退出码而非抛_SystemExit(pyproject, tmp_path):
    """console script 包装器会把返回值交给 sys.exit()，所以返回 int 是对的。

    这条钉住「失败路径返回非 0 的 int」，防止有人改成返回 None——
    那样任何失败在 shell 里都会变成退出码 0，CI 与 hook 全部瞎掉。
    走的是「--config 指向不存在的文件」这条纯配置错误路径：不碰本机会话，不产报告。
    """
    target = pyproject["project"]["scripts"][EXPECTED_SCRIPT_NAME]
    module_name, _, attr = target.partition(":")
    func = getattr(importlib.import_module(module_name), attr)

    rc = func(["scan", "--config", str(tmp_path / "根本不存在.toml")])
    assert isinstance(rc, int), f"入口函数应返回 int 退出码，实际返回 {type(rc)}"
    assert rc != 0, "失败路径应返回非 0 退出码"


# --------------------------------------------------------------------------
# requires-python ↔ classifiers 一致性
# --------------------------------------------------------------------------


def _minor_versions_from_classifiers(classifiers: list[str]) -> list[int]:
    out = []
    for c in classifiers:
        m = re.fullmatch(r"Programming Language :: Python :: 3\.(\d+)", c)
        if m:
            out.append(int(m.group(1)))
    return sorted(out)


def test_requires_python_与_classifiers_一致(pyproject):
    proj = pyproject["project"]
    requires = proj["requires-python"]
    m = re.search(r">=\s*3\.(\d+)", requires)
    assert m, f"requires-python 应形如 '>=3.11'，实际: {requires}"
    floor = int(m.group(1))

    minors = _minor_versions_from_classifiers(proj["classifiers"])
    assert minors, "classifiers 里没有任何 'Programming Language :: Python :: 3.x'"
    assert min(minors) == floor, (
        f"requires-python 下限是 3.{floor}，但 classifiers 最低只声明到 3.{min(minors)}——"
        "两处口径必须一致"
    )
    # 中间不能挖空：3.11/3.13 却漏 3.12 属于口径不自洽
    assert minors == list(range(min(minors), max(minors) + 1)), (
        f"classifiers 的 Python 版本不连续: {minors}"
    )


def test_classifiers_含许可与主题(pyproject):
    classifiers = pyproject["project"]["classifiers"]
    assert any(c.startswith("License ::") for c in classifiers), "classifiers 缺 License ::"
    assert any(c.startswith("Topic ::") for c in classifiers), "classifiers 缺 Topic ::"


# --------------------------------------------------------------------------
# scripts/aci —— uv → python3 fallback
# --------------------------------------------------------------------------


def test_aci_脚本存在且可执行():
    assert ACI_SCRIPT.is_file(), "缺 scripts/aci"
    mode = ACI_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR, "scripts/aci 没有执行位（chmod +x）"


def test_aci_脚本两条分支齐全():
    text = ACI_SCRIPT.read_text(encoding="utf-8")
    assert text.startswith("#!"), "scripts/aci 缺 shebang"
    assert "set -euo pipefail" in text, "scripts/aci 缺 set -euo pipefail"
    assert "uv run" in text, "scripts/aci 缺 uv 分支"
    assert "python3 -m ai_coding_insights" in text, "scripts/aci 缺 python3 fallback 分支"


def test_aci_脚本带_python_版本检查():
    text = ACI_SCRIPT.read_text(encoding="utf-8")
    # 版本下限必须与 pyproject 的 requires-python 同源
    requires = _load_pyproject()["project"]["requires-python"]
    floor = re.search(r">=\s*(3\.\d+)", requires).group(1)
    assert floor in text, f"scripts/aci 没有对 Python {floor} 的版本检查"
    assert "sys.version_info" in text or "version_info" in text, (
        "scripts/aci 的版本检查应基于 sys.version_info"
    )


def test_aci_脚本自己定位仓库根不依赖调用方_cwd():
    text = ACI_SCRIPT.read_text(encoding="utf-8")
    assert "BASH_SOURCE" in text, "scripts/aci 应用 BASH_SOURCE 自定位仓库根"


def test_aci_脚本能通过_bash_语法检查():
    proc = subprocess.run(
        ["bash", "-n", str(ACI_SCRIPT)], capture_output=True, text=True
    )
    assert proc.returncode == 0, f"scripts/aci 语法错误: {proc.stderr}"


def test_aci_脚本真跑一次能出_help():
    """端到端：不依赖调用方 cwd（故意在 / 下跑）。"""
    env = dict(os.environ)
    proc = subprocess.run(
        ["bash", str(ACI_SCRIPT), "--help"],
        capture_output=True,
        text=True,
        cwd="/",
        env=env,
        timeout=180,
    )
    assert proc.returncode == 0, f"scripts/aci --help 失败:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    assert "scan" in proc.stdout, f"--help 输出里没看到子命令: {proc.stdout[:400]}"


# --------------------------------------------------------------------------
# 构建冒烟
# --------------------------------------------------------------------------


def _build_backend_available() -> bool:
    try:
        importlib.import_module("hatchling")
        return True
    except ImportError:
        return False


def test_构建冒烟_wheel_里含包目录(tmp_path):
    """真构一个 wheel，断言里面有 ai_coding_insights/ 包与 console script 声明。"""
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "build", "--wheel", "--out-dir", str(tmp_path), str(REPO_ROOT)]
    else:
        try:
            importlib.import_module("build")
        except ImportError:
            pytest.skip("环境里既没有 uv 也没有 python -m build，无法做构建冒烟（未验证，不算通过）")
        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path), str(REPO_ROOT)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, f"构建失败:\n{proc.stdout}\n{proc.stderr}"

    wheels = list(tmp_path.glob("*.whl"))
    assert wheels, f"没产出 wheel: {proc.stdout}"

    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
        assert any(n.startswith("ai_coding_insights/") for n in names), (
            f"wheel 里没有 ai_coding_insights/ 包: {names[:20]}"
        )
        assert any(n.endswith("__main__.py") for n in names), "wheel 里缺 __main__.py"

        # 源码树里每个模块都得进 wheel——新加模块没被打包是「不报错的失配」，
        # 只有用户装完 import 才炸，所以在这里全量比对。
        src_modules = {
            p.name for p in (REPO_ROOT / "src" / "ai_coding_insights").glob("*.py")
        }
        shipped = {
            n.split("/", 1)[1]
            for n in names
            if n.startswith("ai_coding_insights/") and n.endswith(".py")
        }
        missing = src_modules - shipped
        assert not missing, f"这些模块没被打进 wheel: {sorted(missing)}"

        entry_points = [n for n in names if n.endswith("entry_points.txt")]
        assert entry_points, "wheel 里没有 entry_points.txt（console script 没打进去）"
        content = zf.read(entry_points[0]).decode("utf-8")
        assert EXPECTED_SCRIPT_NAME in content, f"entry_points.txt 里没有入口: {content}"
