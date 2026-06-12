"""完整输入按字符预算分批（首次适应贪心装箱）。

不拆会话、不丢会话；单个会话即使自身超过预算也独占一批。
"""


def _session_chars(si: dict) -> int:
    return sum(t["chars"] for t in si.get("turns", []))


def make_batches(sessions_input: list[dict], batch_chars: int = 120_000) -> list[list[dict]]:
    """把会话输入顺序贪心装箱成多批，每批累计字符不超过 ``batch_chars``。

    - 顺序遍历，维护当前批与已累计字符。
    - 若加入当前会话会使累计字符超过 ``batch_chars`` 且当前批非空，
      则先收尾当前批、开新批，再放入该会话。
    - 单个会话自身超额时独占一批（当前批为空则无条件放入，避免空批）。
    - 展平后会话总数与顺序 == 输入；空输入返回 ``[]``。
    """
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0

    for si in sessions_input:
        size = _session_chars(si)
        if current and current_chars + size > batch_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(si)
        current_chars += size

    if current:
        batches.append(current)

    return batches
