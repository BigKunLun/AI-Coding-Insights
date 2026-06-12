from ai_coding_insights.batch import make_batches


def si(n_chars, sid=None):
    """造一个会话输入：turns 里只放 chars 字段即可（make_batches 不关心其他）。"""
    return {"session_id": sid or f"s{n_chars}", "turns": [{"chars": n_chars}]}


def _session_chars(s):
    return sum(t["chars"] for t in s.get("turns", []))


def _flatten(batches):
    return [s for batch in batches for s in batch]


def test_uniform_sessions_pack_under_budget():
    items = [si(50_000, sid=f"u{i}") for i in range(5)]
    batches = make_batches(items, batch_chars=120_000)

    # 每批字符和 <= 120_000（这些会话各自不超额）
    for batch in batches:
        total = sum(_session_chars(s) for s in batch)
        assert total <= 120_000

    flat = _flatten(batches)
    # 展平后数量与顺序保持
    assert len(flat) == 5
    assert [s["session_id"] for s in flat] == [f"u{i}" for i in range(5)]
    # 每批至少 1 个会话
    assert all(len(batch) >= 1 for batch in batches)


def test_single_oversized_session_gets_own_batch():
    big = si(200_000, sid="big")
    batches = make_batches([big], batch_chars=120_000)
    assert len(batches) == 1
    assert batches[0] == [big]


def test_mixed_packing_greedy_no_loss_order_preserved():
    items = [
        si(50_000, sid="a"),
        si(50_000, sid="b"),
        si(50_000, sid="c"),
        si(200_000, sid="d"),
        si(10_000, sid="e"),
    ]
    batches = make_batches(items, batch_chars=120_000)

    # 首次适应贪心：a+b=100k 装一批，c=50k 放不下 a+b（150k>120k）开新批，
    # d=200k 单独超额独占一批，e=10k 开新批。
    assert [[s["session_id"] for s in batch] for batch in batches] == [
        ["a", "b"],
        ["c"],
        ["d"],
        ["e"],
    ]

    # 无丢失、顺序对
    flat = _flatten(batches)
    assert [s["session_id"] for s in flat] == ["a", "b", "c", "d", "e"]

    # 除独占超额情形外，每批字符和 <= 120_000
    for batch in batches:
        total = sum(_session_chars(s) for s in batch)
        if len(batch) > 1:
            assert total <= 120_000


def test_empty_input_returns_empty():
    assert make_batches([]) == []


def test_session_without_turns_counts_as_zero():
    items = [{"session_id": "z"}, si(120_000, sid="full")]
    batches = make_batches(items, batch_chars=120_000)
    # 无 turns 的会话字符量为 0，可与 full 同批（0+120k = 120k 不超）
    flat = _flatten(batches)
    assert [s["session_id"] for s in flat] == ["z", "full"]
    assert len(batches) == 1
