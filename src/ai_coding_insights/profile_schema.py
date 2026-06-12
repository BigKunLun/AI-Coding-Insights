def validate_profile(obj, business_terms=()) -> list[str]:
    """返回错误信息列表；空列表 = 通过。零三方依赖的轻量校验。

    画像不含任何姿势字段：posture_distribution 与 l4_share 均已废弃，出现即报错
    （喂回 SKILL 重写重试环）。四档分布由规则层从 obs 的 posture_counts 聚合组装。

    维度块（breadth/depth/outcome）支持两种形态，均接受（缺字段不报错）：
      - 旧版散文：summary(str)
      - v5 结构化：headline(str) + points(list[str]) + metrics(list[{label,value}])
    顶层可选 frictions(list)：每项须含 observation(str) + suggestion(str) +
    pointers(list[str]，可为空)；无 frictions 键不报错。
    顶层可选 highlights(list)：每项须含 pointer + behavior(str)，结构与 evidence
    条目一致；无 highlights 键不报错。

    business_terms 非空时，作为脱敏兜底网：扫描画像中所有会进入渲染的自由文本——
    breadth/depth/outcome 的 summary / headline / points[] / metrics[].label /
    metrics[].value(字符串值) / tools[]，每条 evidence / highlights 的
    behavior 与 pointer（指针是文件路径，含业务词的文件名同属泄露面），
    以及 frictions[].observation / suggestion。命中任一业务方向词则报错
    （每词仅报一次）。不传 business_terms 时行为与旧版完全一致（向后兼容）。
    """
    errs = []
    if not isinstance(obj, dict):
        return ["画像必须是 JSON 对象"]
    if "posture_distribution" in obj:
        errs.append("posture_distribution 已废弃：四档分布由规则层从 obs 聚合组装，"
                    "画像不含任何姿势字段，删除该键")
    if "l4_share" in obj:
        errs.append("l4_share 已废弃：四档分布由规则层从 obs 的 posture_counts "
                    "聚合组装，删除该键")
    for dim in ("breadth", "depth", "outcome"):
        d = obj.get(dim)
        if not isinstance(d, dict):
            errs.append(f"缺维度 {dim}（须为对象）")
            continue
        # v5 结构化字段为软约束：缺失不报错，存在则查类型
        pts = d.get("points")
        if pts is not None and not (
            isinstance(pts, list) and all(isinstance(p, str) for p in pts)
        ):
            errs.append(f"{dim}.points 必须是字符串列表")
        ms = d.get("metrics")
        if ms is not None:
            if not isinstance(ms, list):
                errs.append(f"{dim}.metrics 必须是列表")
            else:
                for m in ms:
                    if not isinstance(m, dict) or "label" not in m or "value" not in m:
                        errs.append(f"{dim}.metrics 每项须含 label 与 value")
                        break
    fr = obj.get("frictions")
    if fr is not None:
        if not isinstance(fr, list):
            errs.append("frictions 必须是列表")
        else:
            for f in fr:
                if not isinstance(f, dict) or not isinstance(f.get("observation"), str) \
                        or not isinstance(f.get("suggestion"), str) \
                        or not isinstance(f.get("pointers"), list) \
                        or not all(isinstance(p, str) for p in f["pointers"]):
                    errs.append("每条 frictions 须含 observation 与 suggestion（字符串）"
                                "及 pointers（字符串列表，可为空但键必填）")
                    break
    ev = obj.get("evidence")
    if not isinstance(ev, list) or not ev:
        errs.append("evidence 必须是非空列表")
    else:
        for e in ev:
            if not isinstance(e, dict) or "pointer" not in e or "behavior" not in e:
                errs.append("每条 evidence 须含 pointer 与 behavior")
                break
    hl = obj.get("highlights")
    if hl is not None:
        if not isinstance(hl, list):
            errs.append("highlights 必须是列表")
        else:
            for h in hl:
                if not isinstance(h, dict) or "pointer" not in h \
                        or not isinstance(h.get("behavior"), str):
                    errs.append("每条 highlights 须含 pointer 与 behavior（字符串）")
                    break
    if business_terms:
        texts = []
        for dim in ("breadth", "depth", "outcome"):
            d = obj.get(dim)
            if not isinstance(d, dict):
                continue
            if isinstance(d.get("summary"), str):
                texts.append((dim, d["summary"]))
            if isinstance(d.get("headline"), str):
                texts.append((f"{dim}.headline", d["headline"]))
            pts = d.get("points")
            if isinstance(pts, list):
                for i, p in enumerate(pts):
                    if isinstance(p, str):
                        texts.append((f"{dim}.points[{i}]", p))
            ms = d.get("metrics")
            if isinstance(ms, list):
                for i, mtr in enumerate(ms):
                    if not isinstance(mtr, dict):
                        continue
                    if isinstance(mtr.get("label"), str):
                        texts.append((f"{dim}.metrics[{i}].label", mtr["label"]))
                    if isinstance(mtr.get("value"), str):
                        texts.append((f"{dim}.metrics[{i}].value", mtr["value"]))
            tl = d.get("tools")
            if isinstance(tl, list):
                for i, t in enumerate(tl):
                    if isinstance(t, str):
                        texts.append((f"{dim}.tools[{i}]", t))
        for key, lst in (("evidence", ev), ("highlights", hl)):
            if not isinstance(lst, list):
                continue
            for item in lst:
                if not isinstance(item, dict):
                    continue
                if isinstance(item.get("behavior"), str):
                    texts.append((key, item["behavior"]))
                if isinstance(item.get("pointer"), str):
                    texts.append((f"{key}.pointer", item["pointer"]))
        if isinstance(fr, list):
            for i, f in enumerate(fr):
                if not isinstance(f, dict):
                    continue
                if isinstance(f.get("observation"), str):
                    texts.append((f"frictions[{i}].observation", f["observation"]))
                if isinstance(f.get("suggestion"), str):
                    texts.append((f"frictions[{i}].suggestion", f["suggestion"]))
                if isinstance(f.get("pointers"), list):
                    for j, p in enumerate(f["pointers"]):
                        if isinstance(p, str):
                            texts.append((f"frictions[{i}].pointers[{j}]", p))
        for term in business_terms:
            for field, text in texts:
                if term in text:
                    errs.append(f"疑含业务方向词「{term}」：{field}")
                    break  # 每个词仅报一次，避免刷屏
    return errs
