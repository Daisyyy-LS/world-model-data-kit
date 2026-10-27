#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
world-model eval kit · score.py
--------------------------------
世界模型 / 交互视频生成结果的 8 维打分器，并把评测结果翻译成数据采集动作。

设计出发点（也是这个仓库想表达的产品观点）：
    评测不应该止于打个分。一条样本的失败维度，应当直接指向
    "它属于哪一层数据、下次采集要补什么标注"。
    否则评测集和训练集是两张皮，badcase 永远沉淀不下来。

用法：
    python score.py samples/clip_A_interactive.json
    python score.py samples/clip_A_interactive.json --json
    python score.py samples/clip_A_interactive.json --markdown out.md

只依赖 Python 标准库。
"""

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple


# ---------------------------------------------------------------- 维度定义

@dataclass
class Dimension:
    key: str
    name: str
    weight: float                     # 总评权重
    desc: str                         # 这一维在测什么
    failure_hint: str                 # 典型失败形态
    layer: str                        # 失败时应回流到的数据层
    action: str                       # 对应的采集 / 标注动作
    score_fn: Callable[[dict], Tuple[float, str]] = field(repr=False, default=None)


def _safe(d: Optional[dict]) -> dict:
    return d if isinstance(d, dict) else {}


def _ratio(num: float, den: float) -> float:
    """安全除法：分母为 0 时视为无失败。"""
    return 0.0 if not den else num / den


def _band(rate: float, good: float, ok: float, fair: float, poor: float) -> float:
    """
    把「失败率」映射到 0-5 分。
    rate <= good  -> 5 分；每越过一档降 1 分；>= poor -> 1 分。
    """
    if rate <= good:
        return 5.0
    if rate <= ok:
        return 4.0
    if rate <= fair:
        return 3.0
    if rate <= poor:
        return 2.0
    return 1.0


DIMENSIONS: List[Dimension] = [
    Dimension(
        key="consistency", name="时空一致性",
        weight=1.4, desc="同一场景多次回访，静态元素是否稳定",
        failure_hint="墙上的画，第二次进去变了",
        layer="L1 结构化标注",
        action="补跨帧静态元素（家具/装饰/光照）的持久 ID 与属性快照",
        score_fn=lambda c: (
            _band(_ratio(_safe(c.get("revisit")).get("static_element_mismatch", 0),
                         _safe(c.get("revisit")).get("checked_elements", 0)),
                  0.0, 0.03, 0.10, 0.25),
            f"回访 {_safe(c.get('revisit')).get('n_revisits', 0)} 次，"
            f"静态元素错配率 {_ratio(_safe(c.get('revisit')).get('static_element_mismatch', 0), _safe(c.get('revisit')).get('checked_elements', 0)):.1%}",
        ),
    ),
    Dimension(
        key="permanence", name="物体恒存",
        weight=1.3, desc="物体被遮挡后再出现，属性是否保持一致",
        failure_hint="藏起来的杯子回来变成别的颜色",
        layer="L1 结构化标注",
        action="补遮挡前后同一物体的 ID 对齐与属性（颜色/形状/位置）标注",
        score_fn=lambda c: (
            _band(_ratio(_safe(c.get("object_permanence")).get("attr_changed_after_occlusion", 0),
                         _safe(c.get("object_permanence")).get("occluded_objects", 0)),
                  0.0, 0.05, 0.15, 0.30),
            f"遮挡物体 {_safe(c.get('object_permanence')).get('occluded_objects', 0)} 个，"
            f"属性漂移 {_safe(c.get('object_permanence')).get('attr_changed_after_occlusion', 0)} 个",
        ),
    ),
    Dimension(
        key="physics", name="物理合理性",
        weight=1.2, desc="碰撞、支撑、流体等是否符合直觉",
        failure_hint="杯子穿过桌面",
        layer="L1 结构化标注 + L3 失败样本",
        action="把穿模/失重样本打为 hard negative，标注违反的物理类别",
        score_fn=lambda c: (
            _band(_ratio(len(_safe(c.get("physics")).get("violations", [])),
                         max(_safe(c).get("duration_sec", 0) / 10.0, 1.0)),
                  0.0, 0.25, 0.60, 1.20),
            f"检查 {_safe(c.get('physics')).get('frames_checked', 0)} 帧，"
            f"违反 {len(_safe(c.get('physics')).get('violations', []))} 处",
        ),
    ),
    Dimension(
        key="camera", name="相机控制精度",
        weight=1.1, desc="给定运镜指令，实际轨迹与指令的偏差",
        failure_hint="「左移环绕」做成了平移",
        layer="L2 交互轨迹",
        action="补 (运镜指令, 实际相机轨迹) 配对数据，含景别与轴线标签",
        score_fn=lambda c: (
            _band(_ratio(_safe(c.get("camera")).get("trajectory_error_px", 0),
                         _safe(c.get("camera")).get("max_tolerated_px", 20)),
                  0.25, 0.50, 0.85, 1.20),
            f"轨迹误差 {_safe(c.get('camera')).get('trajectory_error_px', 0)}px "
            f"/ 容差 {_safe(c.get('camera')).get('max_tolerated_px', 20)}px",
        ),
    ),
    Dimension(
        key="instruction", name="动作 / 指令跟随",
        weight=1.2, desc="自然语言或动作指令是否被正确执行",
        failure_hint="「把门打开」没被执行",
        layer="L2 交互轨迹",
        action="补指令—执行结果的人工判定对，未执行的进 L3 归因为「指令未落地」",
        score_fn=lambda c: (
            _band(_ratio(_safe(c.get("instruction_following")).get("n_failed", 0),
                         _safe(c.get("instruction_following")).get("n_instructions", 0)),
                  0.0, 0.10, 0.25, 0.45),
            f"指令 {_safe(c.get('instruction_following')).get('n_instructions', 0)} 条，"
            f"未执行 {_safe(c.get('instruction_following')).get('n_failed', 0)} 条",
        ),
    ),
    Dimension(
        key="latency", name="交互延迟",
        weight=0.9, desc="从输入动作到画面响应的端到端时延",
        failure_hint="超过 300ms 就失去交互感",
        layer="L2 交互轨迹",
        action="采集时记录每步 action 的时间戳与响应延迟，作为独立字段进入训练集",
        score_fn=lambda c: (
            _band(_safe(c.get("interaction")).get("p95_latency_ms", 999) / 300.0,
                  0.6, 1.0, 1.6, 2.5),
            f"P95 延迟 {_safe(c.get('interaction')).get('p95_latency_ms', 999)}ms（基准 300ms）",
        ),
    ),
    Dimension(
        key="memory", name="长时记忆与因果",
        weight=1.3, desc="多步操作后，能否回溯前序状态与因果链",
        failure_hint="第三步时忘了第一步挪过椅子",
        layer="L3 偏好与失败样本",
        action="构造多步任务链并标注「关键状态记忆点」，失败链整条入库而非单帧",
        score_fn=lambda c: (
            _band(_ratio(_safe(c.get("memory")).get("n_steps", 0) - _safe(c.get("memory")).get("recall_correct", 0),
                         _safe(c.get("memory")).get("n_steps", 0)),
                  0.0, 0.20, 0.40, 0.65),
            f"{_safe(c.get('memory')).get('n_steps', 0)} 步任务链，"
            f"正确回溯 {_safe(c.get('memory')).get('recall_correct', 0)} 步",
        ),
    ),
    Dimension(
        key="editability", name="局部可编辑性",
        weight=0.8, desc="局部重绘后，周边光影与几何是否保持一致",
        failure_hint="改了沙发，地板光影穿帮",
        layer="L1 结构化标注",
        action="补「编辑前 / 编辑后」成对样本 + 遮罩区域标注，用于训练局部一致性",
        score_fn=lambda c: (
            _band(_ratio(_safe(c.get("editability")).get("inconsistent_after_edit", 0),
                         _safe(c.get("editability")).get("local_edits", 0)),
                  0.0, 0.15, 0.35, 0.55),
            f"局部编辑 {_safe(c.get('editability')).get('local_edits', 0)} 处，"
            f"周边穿帮 {_safe(c.get('editability')).get('inconsistent_after_edit', 0)} 处",
        ),
    ),
]

PASS_LINE = 3.5   # 单维低于此分视为需要回流


# ---------------------------------------------------------------- 打分

@dataclass
class Row:
    dim: Dimension
    score: Optional[float]   # None 表示该维度对本样本不适用（N/A）
    note: str

    @property
    def need_fix(self) -> bool:
        return self.score is not None and self.score < PASS_LINE

    @property
    def priority(self) -> float:
        """缺口越大、权重越高，越优先处理。"""
        if self.score is None:
            return 0.0
        return (PASS_LINE - max(self.score, 0.0)) * self.dim.weight


def evaluate(clip: dict) -> Tuple[List[Row], float]:
    na = set(clip.get("not_applicable", []) or [])
    rows = []
    for dim in DIMENSIONS:
        if dim.key in na:
            rows.append(Row(dim=dim, score=None, note="本任务类型不适用（N/A）"))
            continue
        score, note = dim.score_fn(clip)
        score = max(0.0, min(5.0, float(score)))
        rows.append(Row(dim=dim, score=score, note=note))
    counted = [r for r in rows if r.score is not None]
    total_weight = sum(r.dim.weight for r in counted)
    weighted = sum(r.score * r.dim.weight for r in counted)
    return rows, (weighted / total_weight if total_weight else 0.0)


def overall_label(score: float) -> str:
    if score >= 4.3:
        return "高质量，可进入试点"
    if score >= 3.5:
        return "可用，但需针对弱项补数据"
    if score >= 2.5:
        return "暂不适合交互场景"
    return "不建议进入评测"


# ---------------------------------------------------------------- 输出

def render_text(clip: dict, rows: List[Row], total: float) -> str:
    name = clip.get("clip_id", "unknown")
    scenario = clip.get("scenario", "-")
    dur = clip.get("duration_sec", "-")

    lines = []
    lines.append("=" * 68)
    lines.append(f"世界模型输出评测卡 · {name}")
    lines.append(f"场景：{scenario}    时长：{dur}s")
    lines.append("=" * 68)
    lines.append(f"{'维度':<14}{'得分':<7}{'权重':<7}{'证据 / 说明'}")
    lines.append("-" * 68)
    for r in rows:
        if r.score is None:
            lines.append(f"{r.dim.name:<12}{'N/A':>5}{'':<3}{r.dim.weight:>6.1f}   {r.note}")
            continue
        flag = " ⚠" if r.need_fix else "  "
        lines.append(f"{r.dim.name:<12}{r.score:>4.1f}{flag}{r.dim.weight:>6.1f}   {r.note}")
    lines.append("-" * 68)
    lines.append(f"加权总分：{total:.2f} / 5.00    →  {overall_label(total)}")
    lines.append("")

    bad = sorted([r for r in rows if r.need_fix], key=lambda r: -r.priority)
    if not bad:
        lines.append("回流建议：全部维度达标，该样本可作为 positive 进入 L3 偏好对的 chosen 侧。")
    else:
        lines.append(f"回流建议：{len(bad)} 个维度低于 {PASS_LINE} 分，按优先级处理——")
        lines.append("")
        for i, r in enumerate(bad, 1):
            lines.append(f"{i}. 【{r.dim.name}】{r.score:.1f} 分（优先级 {r.priority:.2f}）")
            lines.append(f"   失败形态：{r.dim.failure_hint}")
            lines.append(f"   回流层　：{r.dim.layer}")
            lines.append(f"   采集动作：{r.dim.action}")
            lines.append("")
        worst = bad[0]
        lines.append(f"本条样本主归因：【{worst.dim.name}】，建议整条入库为 "
                     f"hard negative，而非截取单帧。")
    lines.append("=" * 68)
    return "\n".join(lines)


def render_markdown(clip: dict, rows: List[Row], total: float) -> str:
    name = clip.get("clip_id", "unknown")
    bad = sorted([r for r in rows if r.need_fix], key=lambda r: -r.priority)
    md = [f"## 评测卡 · {name}", "",
          f"- 场景：{clip.get('scenario', '-')}｜时长：{clip.get('duration_sec', '-')}s",
          f"- **加权总分：{total:.2f} / 5.00** → {overall_label(total)}", "",
          "| 维度 | 得分 | 权重 | 说明 |", "|---|---:|---:|---|"]
    for r in rows:
        mark = " ⚠️" if r.need_fix else ""
        cell = "N/A" if r.score is None else f"{r.score:.1f}"
        md.append(f"| {r.dim.name}{mark} | {cell} | {r.dim.weight} | {r.note} |")
    md.append("")
    if bad:
        md.append("### 数据回流建议")
        md.append("")
        for r in bad:
            md.append(f"- **{r.dim.name}**（{r.score:.1f}）→ 回流 `{r.dim.layer}`：{r.dim.action}")
    else:
        md.append("### 数据回流建议")
        md.append("")
        md.append("- 全部维度达标，可作为 L3 偏好对的 chosen 侧样本。")
    return "\n".join(md)


def render_json(clip: dict, rows: List[Row], total: float) -> str:
    payload = {
        "clip_id": clip.get("clip_id", "unknown"),
        "scenario": clip.get("scenario"),
        "total_score": round(total, 3),
        "verdict": overall_label(total),
        "dimensions": [
            {
                "key": r.dim.key, "name": r.dim.name,
                "score": None if r.score is None else round(r.score, 2),
                "weight": r.dim.weight, "evidence": r.note, "need_fix": r.need_fix,
            }
            for r in rows
        ],
        "data_actions": [
            {"dimension": r.dim.name, "layer": r.dim.layer, "action": r.dim.action,
             "priority": round(r.priority, 3)}
            for r in sorted([r for r in rows if r.need_fix], key=lambda r: -r.priority)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="世界模型输出 8 维打分器：把评测结果翻译成数据采集动作")
    ap.add_argument("input", help="生成结果元信息 JSON 路径")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--markdown", metavar="PATH", help="额外输出一份 Markdown 评测卡")
    args = ap.parse_args(argv)

    try:
        with open(args.input, "r", encoding="utf-8") as f:
            clip = json.load(f)
    except FileNotFoundError:
        print(f"[错误] 找不到输入文件：{args.input}")
        return 2
    except json.JSONDecodeError as e:
        print(f"[错误] JSON 解析失败：{e}")
        return 2

    rows, total = evaluate(clip)

    if args.json:
        print(render_json(clip, rows, total))
    else:
        print(render_text(clip, rows, total))

    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(render_markdown(clip, rows, total))
        print(f"\n已写入 Markdown 评测卡：{args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
