# Demo 报告（**全部为假数据**）

这两份 HTML 是给没装过本工具的人看的样例。**数据零真实成分**：没有任何真实项目名、
路径、业务词，也不是任何真人的指标。渲染走的是仓库里真实的 `render-profile`，
所以版式、口径、caveat 与你自己跑一次完全一致——假的只有数字。

| 文件 | 场景 | 想让你看到什么 |
|---|---|---|
| [`aci-report-demo.html`](aci-report-demo.html) | Claude Code（能力全集） | 完整四维画像 + 档位判据 + 与上次的同比箭头 + 版本漂移雷达 |
| [`aci-report-demo-codex.html`](aci-report-demo-codex.html) | Codex CLI（能力子集） | **「未测量 ≠ 0」**的渲染，与置顶的「读数前提 · 来源口径」caveat 卡片 |

静态截图：`screenshot-hero.png`（Claude Code 场景首屏）、
`screenshot-codex-unmeasured.png`（Codex 场景的未测量与 caveat）。

演示 GIF：`demo.gif`（15s，通览 Claude Code 场景全报告）、
`demo-codex-unmeasured.gif`（7s，特写 Codex 场景：主网格 9 格全是实数，
点开折叠区才是测不到的 7 项）。

这四份**每一帧都是 headless Chrome 对下面生成出来的真实 HTML 的真实截图**，
不是设计稿，也没有任何手绘、拼贴或后期美化。录法见下面「GIF 是怎么录的」。

## 三处需要说明的地方

1. **证据指针全部标着 ⚠「指针未命中」，这是预期行为。**
   报告渲染前会逐条回看指针指向的会话文件，核不到就公开标注——这正是防 LLM 编造证据的
   机制。demo 的指针指向虚构路径（`/home/demo/.aci-demo/…`），当然核不到。
   你自己跑出来的报告里，这些指针是可点回看的真实位置。
2. **Codex 场景里 SubAgent / Workflow / MCP 等显示「未测量」而不是 0。**
   Codex 的会话记录里没有这些概念的对位信号，渲染成「0 次」等于告诉用户「你没用过」——
   那是错误结论。同理，它的成熟度档位跳过了一条判据，报告里明说了「与其他来源不直接可比」。
3. **档位挂 beta 角标**：阈值是初设值，只经本机单人分布粗校，无人群分位背书。

## 怎么重新生成

```bash
# 1) 造数据（假数据，零真实成分；codex 的 unmeasured 字段名从 sources.py 取真值）
uv run python docs/demo/生成demo数据.py

# 2) 渲染 Claude Code 场景
uv run python -m ai_coding_insights render-profile \
  --profile docs/demo/data/claude-code/profile.json \
  --metrics docs/demo/data/claude-code/_aggregate.json \
  --window docs/demo/data/claude-code/_window.json \
  --obs-glob 'docs/demo/data/claude-code/obs-*.json' \
  --config docs/demo/data/config.toml \
  --snapshot-dir docs/demo/data/claude-code/snapshots \
  --session-count 68 --project 项目1 --project 项目2 --project 项目3 \
  --project 项目4 --project 项目5 \
  --run-started 2026-08-10T09:00:00+00:00 --run-agents 8 \
  --no-snapshot --out docs/demo/aci-report-demo.html

# 3) 渲染 Codex 场景（未测量 + caveat）
uv run python -m ai_coding_insights render-profile \
  --profile docs/demo/data/codex/profile.json \
  --metrics docs/demo/data/codex/_aggregate.json \
  --window docs/demo/data/codex/_window.json \
  --obs-glob 'docs/demo/data/codex/obs-*.json' \
  --config docs/demo/data/config.toml \
  --snapshot-dir docs/demo/data/codex/snapshots \
  --session-count 47 --project 项目1 --project 项目2 --project 项目3 \
  --run-started 2026-08-10T14:20:00+00:00 --run-agents 8 \
  --no-snapshot --out docs/demo/aci-report-demo-codex.html
```

两条渲染命令都带 `--snapshot-dir`（指向 demo 自己的目录）与 `--no-snapshot`：
**不要漏**，否则会读到、或写进你本机真实的 `~/.ai-coding-insights/snapshots/`，
既污染你自己的增量窗口，也可能把你的真实数字算进 demo 的同比箭头里。

截图（需要本机装了 Chrome）：

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --hide-scrollbars --force-device-scale-factor=2 \
  --window-size=1280,860 --screenshot=docs/demo/screenshot-hero.png \
  "file://$PWD/docs/demo/aci-report-demo.html"
```

## GIF 是怎么录的

**报告改了版式就要重录**，两条命令：

```bash
uv run python docs/demo/录制GIF.py demo     # 15s 通览 → docs/demo/demo.gif                    (900×562, 10fps, 150 帧, 3.4MB)
uv run python docs/demo/录制GIF.py codex    # 7s 特写  → docs/demo/demo-codex-unmeasured.gif  (900×562, 12fps,  84 帧, 0.9MB)
```

需要本机装了 Chrome 与 ffmpeg。零 Python 依赖，`uv run` 只是借解释器。

**这不是录屏，是逐帧真实渲染。** headless Chrome 没有「滚到某处再截图」的开关，
所以脚本把报告塞进一个整页高的 iframe、再把 iframe 的 `top` 设成 `-y`，
等价于滚到 y——但每一帧都是一次独立的 `--screenshot` 真实渲染。报告是纯静态 HTML
（零 `<script>`），所以同一个 y 每次渲染出的像素完全一致，**整条流水线字节级可复现**
（改完重跑，`md5` 和上一版一模一样）。

流水线三段，都在 `录制GIF.py` 里：

1. **算每帧滚到哪** —— `PRESETS[...]["keys"]` 是 `(秒, 滚动 y)` 关键帧，段内 smoothstep 缓动；
   相邻两帧 y 相同即「停留」。停留帧靠硬链接复用同一张 PNG，不重复截图
   （15s / 180 帧只需真截 92 张，约 65 秒跑完）。关键帧可带第三个元素
   `(秒, y, True)` 表示**折叠区块展开**——脚本会另存一份把 `<details>` 加上 `open`
   的报告副本来截（等价于用户点了一下，除该属性外与真实报告逐字相同）。
2. **截图** —— 视口 `vw×vh` + `--force-device-scale-factor=2` 出 2 倍图，最后缩到 900 宽，
   字才不糊。`vw` 收窄（codex 那条用 960）是为了放大字号：报告主体定宽，收窄只吃掉两侧留白。
3. **合成** —— ffmpeg `palettegen` / `paletteuse` 两遍法。报告是浅底彩色，单遍 GIF 会脏。
   `dither=none`：UI 是大片平色，抖动只会把体积撑大一倍还多。

**改了报告版式后**：先量一次页高与各节位置，再照着改关键帧的 y 值——

```bash
uv run python docs/demo/录制GIF.py demo --probe
# 视口 1120x700 → page_h = 5123
#   y=249    指标明细
#   y=801    姿势分布与档位判据
#   ...
```

把量出来的 `page_h` 填回 `PRESETS`（**必须填对**，填小了页面会被截断），
关键帧 y 取「想让哪一节顶到上沿」减去 ~50px 的留白。

体积旋钮按这个顺序拧（**先降帧率，别先降宽度**，降到看不清数字就白录了）：
`gif_fps` → `colors` → `width`。当前两份分别 3.4MB / 0.9MB，都在 GitHub README 加载得动的范围内。

## 目录

- `生成demo数据.py` —— 造假数据（两套场景 + 一份虚构的「上次快照」用于同比）
- `生成能力矩阵.py` —— 从 `sources.py` 生成 README 里那张三家能力矩阵表
- `录制GIF.py` —— 把报告 HTML 录成滚动 GIF（见上一节）
- `录制脚本.md` —— **终端那一段**（装 → 触发）的分镜，需要人肉录屏，**尚未录制**
- `data/` —— 生成出来的假 `_aggregate.json` / `profile.json` / `_window.json` / `obs-*.json`
