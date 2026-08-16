#!/usr/bin/env python3
"""把 demo 报告 HTML 录成滚动浏览 GIF。

**每一帧都是 headless Chrome 对真实 HTML 的真实截图**，脚本只负责决定每帧滚到哪、
再交给 ffmpeg 合成，不画任何像素、不叠任何文字。

原理：报告是纯静态 HTML（零 JS），所以把它塞进一个整页高的 iframe、再把 iframe
`top` 设成 `-y`，等价于滚到 y——但每次截图都是一次独立的真实渲染，结果确定可复现。
（headless Chrome 没有「滚到某处再截图」的开关，这是绕过去的办法。）

用法：
    uv run python docs/demo/录制GIF.py demo     # 15s 通览 → docs/demo/demo.gif
    uv run python docs/demo/录制GIF.py codex    # 5.6s 特写 → docs/demo/demo-codex-unmeasured.gif

依赖：本机装了 Chrome 与 ffmpeg。中间帧落在临时目录，跑完自动删。

改了报告版式后要重录：先按下面的「量一次页高与各节位置」把 PRESETS 里的
`page_h` 与关键帧 y 值对齐，再跑本脚本。量法：

    uv run python docs/demo/录制GIF.py demo --probe
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
HERE = pathlib.Path(__file__).resolve().parent

# 关键帧 (秒, 滚动位置 px)；段内用 smoothstep 缓动，相邻两帧 y 相同即为「停留」。
PRESETS = {
    "demo": {
        "report": HERE / "aci-report-demo.html",
        "out": HERE / "demo.gif",
        # 视口 1120x700 抓图、缩到 900 宽出片；page_h 是 1120 宽下的整页高度
        "vw": 1120, "vh": 700, "page_h": 5123,
        "shoot_fps": 12, "gif_fps": 10, "width": 900, "colors": 144,
        # y 值 = --probe 量出的各节位置减 ~50px 留白（2026-08-16 视觉返工后重新对齐）
        "keys": [
            (0.0, 0),      # 首屏停 2.6s：横幅四数 + 档位 beta 角标
            (2.6, 0),
            (3.9, 200),    # 01 指标明细
            (4.5, 200),
            (5.7, 750),    # 02 姿势分布与档位判据
            (6.6, 750),
            (7.9, 1290),   # 03 四维画像（雷达完整入画）
            (9.4, 1290),
            (10.7, 2245),  # 04 摩擦 + 建议
            (11.6, 2245),
            (13.9, 3540),  # 08 能力盲区 → 09 数据健康
            (15.0, 3540),  # 末尾停 1.1s
        ],
    },
    "codex": {
        "report": HERE / "aci-report-demo-codex.html",
        "out": HERE / "demo-codex-unmeasured.gif",
        # 视口收窄到 960 是为了放大字号——报告主体定宽，收窄只吃掉两侧留白
        "vw": 960, "vh": 600, "page_h": 4500,
        "shoot_fps": 12, "gif_fps": 12, "width": 900, "colors": 160,
        # 叙事：降级来源的报告长什么样。第 3 个元素 = 折叠区块是否展开。
        # 2026-08-15 改版后主网格只留实数（9 格），未测量项收进折叠区——
        # 所以高潮不再是「满屏未测量」，而是「全实数 + 点开才见测不到的 7 项」。
        "keys": [
            (0.0, 0),            # 横幅（Codex CLI · 第 2 档 beta）+「读数前提」卡片
            (1.8, 0),
            (3.4, 405),          # 指标明细：9 格全是实数，底下一行「▸ 本来源测不到的 7 项」
            (4.6, 405),
            (4.7, 405, True),    # 点开：7 个虚线标签展开，信息一条没丢
            (7.0, 405, True),
        ],
    },
}

_PROBE = """<!doctype html><meta charset="utf-8"><body style="margin:0">
<pre id="out">pending</pre>
<iframe id="f" src="{uri}" style="width:{vw}px;height:{vh}px;border:0;position:absolute;left:-99999px"></iframe>
<script>
var f=document.getElementById('f');
f.onload=function(){{
  var d=f.contentDocument, res={{page_h:d.documentElement.scrollHeight, secs:[]}}, ss=d.querySelectorAll('.sec');
  for(var i=0;i<ss.length;i++){{
    var t=ss[i].querySelector('.sec-title');
    res.secs.push([Math.round(ss[i].getBoundingClientRect().top+f.contentWindow.scrollY), t?t.textContent.trim():'']);
  }}
  document.getElementById('out').textContent=JSON.stringify(res);
}};
</script>"""


def probe(cfg: dict) -> None:
    """量一次整页高度与各节的 y 位置，用来对齐 PRESETS 里的关键帧。"""
    with tempfile.TemporaryDirectory() as td:
        p = pathlib.Path(td) / "probe.html"
        p.write_text(_PROBE.format(uri=cfg["report"].as_uri(), vw=cfg["vw"], vh=cfg["vh"]), "utf-8")
        dom = subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
             "--allow-file-access-from-files", "--virtual-time-budget=8000",
             f"--window-size={cfg['vw'] + 80},{cfg['vh'] + 60}", "--dump-dom", p.as_uri()],
            capture_output=True, text=True, timeout=120,
        ).stdout
    head, _, tail = dom.partition('<pre id="out">')
    payload = json.loads(tail.partition("</pre>")[0])
    print(f"视口 {cfg['vw']}x{cfg['vh']} → page_h = {payload['page_h']}")
    for y, title in payload["secs"]:
        print(f"  y={y:<6} {title}")


def _smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)


def _key3(k) -> tuple[float, int, bool]:
    """关键帧统一成 `(t, y, 折叠是否展开)`；第三元素省略即收起态。"""
    return (k[0], k[1], bool(k[2]) if len(k) > 2 else False)


def frame_offsets(keys, fps: int) -> list[tuple[int, bool]]:
    ks = [_key3(k) for k in keys]
    n = int(round(ks[-1][0] * fps))
    out = []
    for i in range(n):
        t = i / fps
        y, op = ks[-1][1], ks[-1][2]
        for (t0, y0, o0), (t1, y1, _o1) in zip(ks, ks[1:]):
            if t0 <= t <= t1:
                y = y1 if t1 == t0 else y0 + (y1 - y0) * _smoothstep((t - t0) / (t1 - t0))
                # 展开是瞬时状态（用户点一下），不随时间插值：整段按段起点的状态截
                op = o0
                break
        out.append((int(round(y)), op))
    return out


def open_folds(report: pathlib.Path, workdir: pathlib.Path) -> pathlib.Path:
    """把报告里的折叠区块预先展开，另存一份副本。

    等价于用户点了一下 `<details>`——除了这个属性，副本与真实报告逐字相同，
    截的仍是 Chrome 对真报告的渲染。不用 JS 注入是因为 file:// 的 iframe
    跨文档访问要额外放行 flag，改一个属性更小、更好审。
    """
    src = report.read_text("utf-8")
    out = src.replace('<details class="um-fold">', '<details class="um-fold" open>')
    if out == src:
        raise RuntimeError(f"报告里没有可展开的折叠区块：{report}")
    dst = workdir / f"{report.stem}-open.html"
    dst.write_text(out, "utf-8")
    return dst


def shoot(cfg: dict, frame: tuple[int, bool], workdir: pathlib.Path) -> None:
    y, op = frame
    report = cfg["report_open"] if op else cfg["report"]
    tag = f"{y:05d}-{int(op)}"
    wrapper = workdir / f"w-{tag}.html"
    wrapper.write_text(
        '<!doctype html><meta charset="utf-8">'
        "<style>html,body{margin:0;padding:0;overflow:hidden;background:#f3f5fa}"
        f"iframe{{position:absolute;left:0;top:{-y}px;width:{cfg['vw']}px;"
        f"height:{cfg['page_h']}px;border:0}}</style>"
        f'<iframe src="{report.as_uri()}" scrolling="no"></iframe>',
        "utf-8",
    )
    png = workdir / f"y-{tag}.png"
    for _ in range(4):  # 并发跑 Chrome 偶发被系统 kill，重试即可
        subprocess.run(
            [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
             "--force-device-scale-factor=2", "--virtual-time-budget=4000",
             f"--window-size={cfg['vw']},{cfg['vh']}", f"--screenshot={png}", wrapper.as_uri()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90,
        )
        if png.exists() and png.stat().st_size > 1000:
            break
    else:
        raise RuntimeError(f"截图失败：y={y} open={op}")
    wrapper.unlink()


def record(cfg: dict, jobs: int) -> None:
    for tool, path in (("Chrome", CHROME), ("ffmpeg", shutil.which("ffmpeg") or "")):
        if not path or not pathlib.Path(path).exists():
            sys.exit(f"缺依赖：{tool}")

    frames = frame_offsets(cfg["keys"], cfg["shoot_fps"])
    uniq = sorted(set(frames))
    print(f"帧 {len(frames)} 张（去重后需真实截图 {len(uniq)} 张）"
          f"· 时长 {len(frames) / cfg['shoot_fps']:.2f}s")

    with tempfile.TemporaryDirectory() as td:
        workdir = pathlib.Path(td)
        # 有任一帧要展开折叠区，就先备好那份副本（见 open_folds）
        cfg["report_open"] = (open_folds(cfg["report"], workdir)
                              if any(op for _, op in uniq) else cfg["report"])
        with ThreadPoolExecutor(max_workers=jobs) as ex:
            list(ex.map(lambda f: shoot(cfg, f, workdir), uniq))

        seq = workdir / "seq"
        seq.mkdir()
        for i, (y, op) in enumerate(frames):  # 停留帧靠硬链接复用，不重复截图
            os.link(workdir / f"y-{y:05d}-{int(op)}.png", seq / f"f-{i:04d}.png")

        # palettegen / paletteuse 两遍法：报告是浅底彩色，单遍 GIF 会脏
        vf = f"fps={cfg['gif_fps']},scale={cfg['width']}:-2:flags=lanczos"
        src = ["-framerate", str(cfg["shoot_fps"]), "-i", str(seq / "f-%04d.png")]
        pal = workdir / "palette.png"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", *src,
             "-vf", f"{vf},palettegen=max_colors={cfg['colors']}:stats_mode=diff", str(pal)],
            check=True)
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", *src, "-i", str(pal),
             # dither=none：UI 是大片平色，抖动只会把体积撑大
             "-lavfi", f"{vf} [x]; [x][1:v] paletteuse=dither=none:diff_mode=rectangle",
             "-loop", "0", str(cfg["out"])],
            check=True)

    size = cfg["out"].stat().st_size / 1024 / 1024
    print(f"→ {cfg['out']}  {cfg['width']}px 宽 · {size:.2f} MB")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("preset", choices=sorted(PRESETS))
    ap.add_argument("--probe", action="store_true", help="只量整页高度与各节 y 位置，不录")
    ap.add_argument("--jobs", type=int, default=4, help="并发 Chrome 数（调大易被系统 kill）")
    args = ap.parse_args()
    cfg = PRESETS[args.preset]
    probe(cfg) if args.probe else record(cfg, args.jobs)


if __name__ == "__main__":
    main()
