#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
十倍股潜力评分引擎 (tenbagger-hunter)

用法:
    python score_stock.py --input case.json
    python score_stock.py --input case.json --format json
    python score_stock.py --demo

输入 JSON 结构:
{
  "name": "示例公司",
  "code": "600000",
  "industry": "示例行业",
  "dimensions": {
    "track":     {"space": 5, "growth": 4, "penetration": 4, "structure": 3},
    "company":   {"moat": 4, "management": 4, "business_model": 3, "expansion": 4},
    "financial": {"growth": 4, "quality": 4, "safety": 3},
    "valuation": {"valuation": 3, "entry_discipline": 4, "tracking_discipline": 4}
  },
  "red_flags": ["F3 商誉占比过高", "C5 大股东密集减持"],
  "notes": "可选备注"
}

子项分值范围 0-5。详见 references/scoring-rubric.md。
"""

import argparse
import json
import sys

# ---------------------------------------------------------------------------
# 评分卡配置（与 references/scoring-rubric.md 保持一致）
# ---------------------------------------------------------------------------

WEIGHTS = {
    "track":     {"space": 7, "growth": 6, "penetration": 6, "structure": 6},
    "company":   {"moat": 10, "management": 5, "business_model": 5, "expansion": 5},
    "financial": {"growth": 10, "quality": 8, "safety": 7},
    "valuation": {"valuation": 10, "entry_discipline": 7, "tracking_discipline": 8},
}

DIM_LABEL = {
    "track": "赛道（大行业）",
    "company": "公司（好公司）",
    "financial": "财务（成长与质量）",
    "valuation": "估值与纪律",
}

ITEM_LABEL = {
    "track": {
        "space": "市场空间 / 天花板",
        "growth": "需求持续增长",
        "penetration": "渗透率提升空间",
        "structure": "行业格局优化",
    },
    "company": {
        "moat": "竞争优势（技术/品牌/渠道/成本）",
        "management": "管理层（战略/执行/激励）",
        "business_model": "商业模式（可复制/提价/现金流）",
        "expansion": "扩张能力（新品/渠道/市占/出海）",
    },
    "financial": {
        "growth": "成长性（营收/利润/市占）",
        "quality": "盈利质量（毛利率/ROE/现金流）",
        "safety": "安全性（负债/应收/存货）",
    },
    "valuation": {
        "valuation": "估值合理性（与成长匹配）",
        "entry_discipline": "买入节奏纪律",
        "tracking_discipline": "跟踪与卖出纪律",
    },
}

# 单维度否决阈值
VETO_DIM_FLOOR = 10.0
# 避雷扣分
RED_FLAG_PENALTY = 5.0
RED_FLAG_PENALTY_CAP = 20.0
# 避雷项数量否决
RED_FLAG_VETO_COUNT = 3

RATING_BANDS = [
    (85.0, "S · 重点研究", "四维俱佳，进入深度尽调"),
    (70.0, "A · 跟踪池", "逻辑基本成立，持续验证关键假设"),
    (55.0, "B · 观察", "有明显短板，等待改善信号"),
    (0.0, "C · 暂不符合", "不满足十倍股框架"),
]

DEMO_CASE = {
    "name": "示例：某高端装备龙头",
    "code": "000000",
    "industry": "高端制造 / 国产替代",
    "dimensions": {
        "track": {"space": 5, "growth": 4, "penetration": 4, "structure": 3},
        "company": {"moat": 4, "management": 4, "business_model": 3, "expansion": 4},
        "financial": {"growth": 4, "quality": 4, "safety": 3},
        "valuation": {"valuation": 3, "entry_discipline": 4, "tracking_discipline": 4},
    },
    "red_flags": ["F5 存货增速高于营收"],
    "notes": "仅用于演示输出格式，非真实公司。",
}


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------

def _validate(data):
    errors = []
    dims = data.get("dimensions")
    if not isinstance(dims, dict):
        return ["缺少 dimensions 字段或类型错误"]

    for dim, items in WEIGHTS.items():
        if dim not in dims or not isinstance(dims[dim], dict):
            errors.append("缺少维度: %s" % dim)
            continue
        for key in items:
            if key not in dims[dim]:
                errors.append("缺少子项: %s.%s" % (dim, key))
                continue
            val = dims[dim][key]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                errors.append("子项 %s.%s 必须是数字" % (dim, key))
            elif not (0 <= val <= 5):
                errors.append("子项 %s.%s 超出 0-5 范围 (当前 %s)" % (dim, key, val))
    return errors


def score(data):
    dims = data["dimensions"]
    red_flags = data.get("red_flags") or []
    if isinstance(red_flags, str):
        red_flags = [red_flags]

    detail = {}
    dim_scores = {}
    for dim, items in WEIGHTS.items():
        rows = []
        subtotal = 0.0
        for key, weight in items.items():
            raw = float(dims[dim][key])
            got = raw / 5.0 * weight
            subtotal += got
            rows.append({
                "key": key,
                "label": ITEM_LABEL[dim][key],
                "raw": raw,
                "weight": weight,
                "score": round(got, 2),
            })
        detail[dim] = rows
        dim_scores[dim] = round(subtotal, 2)

    gross = round(sum(dim_scores.values()), 2)
    penalty = min(len(red_flags) * RED_FLAG_PENALTY, RED_FLAG_PENALTY_CAP)
    total = round(gross - penalty, 2)
    if total < 0:
        total = 0.0

    # 评级
    rating, meaning = "C · 暂不符合", "不满足十倍股框架"
    for floor, label, desc in RATING_BANDS:
        if total >= floor:
            rating, meaning = label, desc
            break

    # 否决规则
    vetoes = []
    if dim_scores["track"] < VETO_DIM_FLOOR:
        vetoes.append("赛道维度得分 %.1f < %.0f（赛道不行，公司再优秀也难出十倍）" % (dim_scores["track"], VETO_DIM_FLOOR))
    if dim_scores["financial"] < VETO_DIM_FLOOR:
        vetoes.append("财务维度得分 %.1f < %.0f（业绩不真，一切都是故事）" % (dim_scores["financial"], VETO_DIM_FLOOR))
    if len(red_flags) >= RED_FLAG_VETO_COUNT:
        vetoes.append("命中避雷点 %d 项 ≥ %d 项" % (len(red_flags), RED_FLAG_VETO_COUNT))

    if vetoes:
        rating, meaning = "C · 暂不符合", "触发单维度否决规则"

    return {
        "name": data.get("name", "(未命名)"),
        "code": data.get("code", ""),
        "industry": data.get("industry", ""),
        "notes": data.get("notes", ""),
        "detail": detail,
        "dim_scores": dim_scores,
        "gross": gross,
        "penalty": penalty,
        "total": total,
        "rating": rating,
        "rating_meaning": meaning,
        "vetoes": vetoes,
        "red_flags": red_flags,
    }


# ---------------------------------------------------------------------------
# 输出
# ---------------------------------------------------------------------------

def render_markdown(result):
    lines = []
    lines.append("# %s 十倍潜力评估" % result["name"])
    meta = []
    if result["code"]:
        meta.append("代码：%s" % result["code"])
    if result["industry"]:
        meta.append("行业：%s" % result["industry"])
    if meta:
        lines.append("")
        lines.append("　".join(meta))

    lines.append("")
    lines.append("## 四维评分")
    lines.append("")
    lines.append("| 维度 | 得分 | 满分 |")
    lines.append("|---|---|---|")
    for dim in ["track", "company", "financial", "valuation"]:
        lines.append("| %s | %.2f | 25 |" % (DIM_LABEL[dim], result["dim_scores"][dim]))

    lines.append("")
    lines.append("### 明细")
    for dim in ["track", "company", "financial", "valuation"]:
        lines.append("")
        lines.append("**%s**" % DIM_LABEL[dim])
        lines.append("")
        lines.append("| 子项 | 原始分(0-5) | 权重 | 加权得分 |")
        lines.append("|---|---|---|---|")
        for row in result["detail"][dim]:
            lines.append("| %s | %g | %g | %.2f |" % (row["label"], row["raw"], row["weight"], row["score"]))

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("- 加权总分（未扣分）：**%.2f**" % result["gross"])
    lines.append("- 避雷扣分：**-%.2f**（命中 %d 项）" % (result["penalty"], len(result["red_flags"])))
    lines.append("- **最终得分：%.2f / 100**" % result["total"])
    lines.append("- **评级：%s** —— %s" % (result["rating"], result["rating_meaning"]))

    if result["red_flags"]:
        lines.append("")
        lines.append("### 命中的避雷点")
        for rf in result["red_flags"]:
            lines.append("- %s" % rf)

    if result["vetoes"]:
        lines.append("")
        lines.append("### 否决提示")
        for v in result["vetoes"]:
            lines.append("- %s" % v)

    if result["notes"]:
        lines.append("")
        lines.append("> 备注：%s" % result["notes"])

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*本评分仅为方法论结构化的辅助工具，仅供参考，不构成任何投资建议。*")
    return "\n".join(lines)


def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main():
    if sys.platform == "win32":
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="十倍股潜力评分引擎")
    parser.add_argument("--input", "-i", help="输入 JSON 文件路径（省略则从 stdin 读取）")
    parser.add_argument("--format", "-f", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--demo", action="store_true", help="使用内置示例数据运行")
    args = parser.parse_args()

    if args.demo:
        data = DEMO_CASE
    elif args.input:
        with open(args.input, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        raw = sys.stdin.read()
        if not raw.strip():
            parser.error("未提供 --input，且 stdin 无内容。可用 --demo 查看示例。")
        data = json.loads(raw)

    errors = _validate(data)
    if errors:
        safe_print("输入校验失败：")
        for e in errors:
            safe_print("  - " + e)
        sys.exit(1)

    result = score(data)

    if args.format == "json":
        safe_print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        safe_print(render_markdown(result))


if __name__ == "__main__":
    main()
