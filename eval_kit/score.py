#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exploratory evaluator for manually observed world-model sample metadata.

This script does not inspect video or evaluate a model. Missing measurements are
reported as unscored; they are never silently treated as successful outcomes.
The score bands are an uncalibrated prototype profile and must not be used as a
benchmark or release gate.
"""

import argparse
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


@dataclass
class Dimension:
    key: str
    name: str
    description: str
    failure_hint: str
    stage: str
    action: str
    score_fn: Callable[[dict], Optional[Tuple[float, str]]]


def _obj(clip: dict, key: str) -> dict:
    value = clip.get(key)
    return value if isinstance(value, dict) else {}


def _number(value, label: str, *, minimum: float = 0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} 必须是数字")
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{label} 必须是有限且不小于 {minimum} 的数字")
    return value


def _count(obj: dict, key: str, label: str) -> Optional[int]:
    if key not in obj:
        return None
    value = obj[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} 必须是非负整数")
    return value


def validate_clip(clip: dict) -> None:
    if not isinstance(clip, dict):
        raise ValueError("输入 JSON 顶层必须是对象")
    for key in ("clip_id", "scenario"):
        if not isinstance(clip.get(key), str) or not clip[key].strip():
            raise ValueError(f"缺少必填文本字段：{key}")
    _number(clip.get("duration_sec"), "duration_sec", minimum=0.001)
    sample_type = clip.get("sample_type")
    if not isinstance(sample_type, str) or sample_type not in {"synthetic_demo", "model_output", "human_authored"}:
        raise ValueError("sample_type 必须是 synthetic_demo、model_output 或 human_authored")
    if sample_type == "model_output":
        required_objects = {
            "model": {"name", "version"},
            "run_metadata": {"generated_at", "prompt_version", "environment", "parameters", "seed"},
            "source_metadata": {"asset_id", "authorization_status", "sensitivity"},
            "review_metadata": {"annotator_id", "task_card_version", "confidence", "adjudication_state"},
        }
        for key, fields in required_objects.items():
            value = clip.get(key)
            if not isinstance(value, dict) or not fields.issubset(value):
                missing = ", ".join(sorted(fields - set(value or {}))) if isinstance(value, dict) else ", ".join(sorted(fields))
                raise ValueError(f"model_output 样本必须提供 {key} 对象及字段：{missing}；未知值可明确填 null")
    na = clip.get("not_applicable", [])
    known = {"consistency", "permanence", "physics", "camera", "instruction", "latency", "memory", "editability"}
    if not isinstance(na, list) or any(not isinstance(x, str) for x in na):
        raise ValueError("not_applicable 必须是维度 key 数组")
    unknown = set(na) - known
    if unknown:
        raise ValueError("未知 not_applicable 维度：" + ", ".join(sorted(unknown)))

    # Validate any supplied counts and internal relationships before scoring.
    for section, num_key, den_key in [
        ("revisit", "static_element_mismatch", "checked_elements"),
        ("object_permanence", "attr_changed_after_occlusion", "occluded_objects"),
        ("instruction_following", "n_failed", "n_instructions"),
        ("editability", "inconsistent_after_edit", "local_edits"),
        ("memory", "recall_correct", "n_checkpoints"),
    ]:
        data = _obj(clip, section)
        n = _count(data, num_key, f"{section}.{num_key}")
        d = _count(data, den_key, f"{section}.{den_key}")
        if n is not None and d is not None and n > d:
            raise ValueError(f"{section}.{num_key} 不能大于 {den_key}")
    physics = _obj(clip, "physics")
    _count(physics, "frames_checked", "physics.frames_checked")
    if "violations" in physics and not isinstance(physics["violations"], list):
        raise ValueError("physics.violations 必须是数组")
    for index, violation in enumerate(physics.get("violations", [])):
        if not isinstance(violation, dict) or not isinstance(violation.get("type"), str):
            raise ValueError(f"physics.violations[{index}] 必须是含 type 的对象")
        start = _number(violation.get("time_start_sec"), f"physics.violations[{index}].time_start_sec")
        end = _number(violation.get("time_end_sec"), f"physics.violations[{index}].time_end_sec")
        if end < start or end > float(clip["duration_sec"]):
            raise ValueError(f"physics.violations[{index}] 时间范围无效")
    camera = _obj(clip, "camera")
    for key in ("trajectory_error_px", "max_tolerated_px", "frame_width_px"):
        if key in camera:
            _number(camera[key], f"camera.{key}", minimum=0.001 if key != "trajectory_error_px" else 0)
    interaction = _obj(clip, "interaction")
    for key in ("p95_latency_ms", "latency_target_ms"):
        if key in interaction:
            _number(interaction[key], f"interaction.{key}", minimum=0.001 if key == "latency_target_ms" else 0)


def _band(rate: float, good: float, ok: float, fair: float, poor: float) -> float:
    """Exploratory 1–5 ordinal band; thresholds are not empirically calibrated."""
    if rate <= good:
        return 5.0
    if rate <= ok:
        return 4.0
    if rate <= fair:
        return 3.0
    if rate <= poor:
        return 2.0
    return 1.0


def _rate_dimension(section: str, numerator: str, denominator: str, name: str,
                    thresholds: Tuple[float, float, float, float], unit: str = "失败率"):
    def score(clip: dict):
        data = _obj(clip, section)
        n = _count(data, numerator, f"{section}.{numerator}")
        d = _count(data, denominator, f"{section}.{denominator}")
        if n is None or d is None or d == 0:
            return None
        rate = n / d
        return _band(rate, *thresholds), f"{n}/{d} {unit} = {rate:.1%}"
    return score


def _physics(clip: dict):
    data = _obj(clip, "physics")
    frames = _count(data, "frames_checked", "physics.frames_checked")
    violations = data.get("violations")
    if frames is None or frames == 0 or violations is None:
        return None
    rate = len(violations) / frames * 100
    return _band(rate, 0.0, 0.25, 0.6, 1.2), f"{len(violations)} 条违反 / {frames} 帧 = {rate:.2f} 条/100帧"


def _camera(clip: dict):
    data = _obj(clip, "camera")
    needed = ("trajectory_error_px", "max_tolerated_px", "frame_width_px")
    if any(k not in data for k in needed):
        return None
    error = float(data["trajectory_error_px"])
    tolerance = float(data["max_tolerated_px"])
    width = float(data["frame_width_px"])
    normalized_error = error / width
    normalized_tolerance = tolerance / width
    ratio = normalized_error / normalized_tolerance
    return _band(ratio, 0.25, 0.50, 0.85, 1.20), (
        f"轨迹误差 {error:g}px/{width:g}px 画幅，容差 {tolerance:g}px；"
        f"误差/容差={ratio:.2f}（探索性分档）")


def _latency(clip: dict):
    data = _obj(clip, "interaction")
    if "p95_latency_ms" not in data or "latency_target_ms" not in data:
        return None
    actual = float(data["p95_latency_ms"])
    target = float(data["latency_target_ms"])
    ratio = actual / target
    return _band(ratio, 0.6, 1.0, 1.6, 2.5), (
        f"P95 {actual:g}ms / 本任务目标 {target:g}ms = {ratio:.2f}；"
        "目标需按产品场景与运行环境设定")


def _memory(clip: dict):
    data = _obj(clip, "memory")
    steps = _count(data, "n_checkpoints", "memory.n_checkpoints")
    correct = _count(data, "recall_correct", "memory.recall_correct")
    if steps is None or correct is None or steps == 0:
        return None
    error = (steps - correct) / steps
    return _band(error, 0.0, 0.20, 0.40, 0.65), f"{correct}/{steps} 个状态回溯点正确；错误率 {error:.1%}"


DIMENSIONS = [
    Dimension("consistency", "时空一致性", "回访时静态元素是否保持", "房间回访后布局漂移", "L1", "补静态元素持久 ID 与属性快照", _rate_dimension("revisit", "static_element_mismatch", "checked_elements", "", (0.0, .03, .10, .25), "错配率")),
    Dimension("permanence", "物体恒存", "遮挡后物体身份与属性是否保持", "杯子重新出现后颜色改变", "L1", "补遮挡前后物体 ID 与属性标注", _rate_dimension("object_permanence", "attr_changed_after_occlusion", "occluded_objects", "", (0.0, .05, .15, .30), "漂移率")),
    Dimension("physics", "物理合理性", "碰撞、支撑等可观察违反", "物体穿透桌面", "L1 + 经复核后 L3", "记录违反类别和证据片段；复核后再决定学习用途", _physics),
    Dimension("camera", "相机控制精度", "运镜轨迹相对指令的偏差", "环绕指令被执行为平移", "L2", "记录指令、参考轨迹、实际轨迹和画幅", _camera),
    Dimension("instruction", "动作 / 指令跟随", "指令是否按任务条件执行", "打开门的动作未改变门状态", "L2", "补指令—结果配对及失败原因", _rate_dimension("instruction_following", "n_failed", "n_instructions", "", (0.0, .10, .25, .45), "失败率")),
    Dimension("latency", "交互延迟", "动作输入到响应的端到端时延", "响应时延超过场景目标", "L2", "记录硬件、负载、计时边界和时延分布", _latency),
    Dimension("memory", "长时记忆与因果", "多步操作后关键状态是否可回溯", "后续操作使用旧状态", "L2；经复核后可进入 L3", "保留完整动作链与关键状态检查点", _memory),
    Dimension("editability", "局部可编辑性", "局部修改后非编辑区域是否稳定", "修改沙发导致周边光影变化", "L1", "补编辑前后样本、遮罩与变化区域", _rate_dimension("editability", "inconsistent_after_edit", "local_edits", "", (0.0, .15, .35, .55), "周边不一致率")),
]
PASS_LINE = 3.5  # Exploratory triage threshold only; not a model release criterion.


@dataclass
class Row:
    dim: Dimension
    score: Optional[float]
    note: str

    @property
    def need_review(self) -> bool:
        return self.score is not None and self.score < PASS_LINE


def evaluate(clip: dict) -> Tuple[List[Row], dict]:
    validate_clip(clip)
    na = set(clip.get("not_applicable", []))
    rows = []
    for dim in DIMENSIONS:
        if dim.key in na:
            rows.append(Row(dim, None, "任务设计声明为 N/A"))
            continue
        result = dim.score_fn(clip)
        if result is None:
            rows.append(Row(dim, None, "未评：缺少足够的观测字段或有效分母"))
        else:
            score, note = result
            rows.append(Row(dim, max(1.0, min(5.0, float(score))), note))
    applicable = len(rows) - len(na)
    measured = sum(r.score is not None for r in rows)
    coverage = measured / applicable if applicable else 0.0
    return rows, {"measured": measured, "applicable": applicable,
                  "coverage": coverage,
                  "below_exploratory_review_line": sum(r.need_review for r in rows)}


def _sample_label(clip: dict) -> str:
    return {"synthetic_demo": "合成示意样本（非模型输出）",
            "model_output": "真实模型输出（须核对运行元信息）",
            "human_authored": "人工构造样本（非模型输出）"}[clip["sample_type"]]


def render_text(clip: dict, rows: List[Row], summary: dict) -> str:
    lines = ["=" * 78, f"评测记录 · {clip['clip_id']}",
             f"来源类型：{_sample_label(clip)}",
             f"场景：{clip['scenario']}    时长：{clip['duration_sec']}s",
             "分数用途：探索性分档，阈值未校准；不得用于模型排名或发布门禁。", "=" * 78,
             f"{'维度':<16}{'分数':<9}{'证据 / 状态'}", "-" * 78]
    for row in rows:
        score = "未评" if row.score is None else f"{row.score:.1f}" + (" · 复核" if row.need_review else "")
        lines.append(f"{row.dim.name:<16}{score:<9}{row.note}")
    pct = f"{summary['coverage']:.0%}" if summary["applicable"] else "N/A"
    lines += ["-" * 78, f"覆盖率：{summary['measured']}/{summary['applicable']} 个适用维度已评（{pct}）",
              f"低于探索性复核线的已测维度：{summary['below_exploratory_review_line']} 项",
              "说明：不计算跨维度总分或平均分。探索性复核线未校准，不是质量门禁。", "",
              "需复核项："]
    bad = [r for r in rows if r.need_review]
    if bad:
        for row in bad:
            lines.append(f"- {row.dim.name}（{row.score:.1f}）：{row.dim.failure_hint}；建议阶段：{row.dim.stage}；建议：{row.dim.action}")
    else:
        lines.append("- 无已测维度低于探索性复核线；这不等于样本通过质量门禁。")
    lines.append("=" * 78)
    return "\n".join(lines)


def render_json(clip: dict, rows: List[Row], summary: dict) -> str:
    payload = {
        "clip_id": clip["clip_id"], "scenario": clip["scenario"],
        "duration_sec": clip["duration_sec"], "not_applicable": clip.get("not_applicable", []),
        "sample_type": clip["sample_type"], "source_note": clip.get("source_note"),
        "model": clip.get("model"), "run_metadata": clip.get("run_metadata"),
        "source_metadata": clip.get("source_metadata"),
        "review_metadata": clip.get("review_metadata"),
        "summary": {**summary, "threshold_profile": "prototype-uncalibrated-v0",
                    "interpretation": "仅展示观测覆盖和待复核维度；不计算整体质量分"},
        "dimensions": [{"key": r.dim.key, "name": r.dim.name, "status": "not_applicable" if r.dim.key in clip.get("not_applicable", []) else ("unscored" if r.score is None else "scored"),
                        "score": r.score, "evidence": r.note, "needs_review": r.need_review,
                        "suggested_data_stage": r.dim.stage if r.need_review else None,
                        "suggested_action": r.dim.action if r.need_review else None} for r in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_markdown(clip: dict, rows: List[Row], summary: dict) -> str:
    lines = [f"## 评测记录 · {clip['clip_id']}", "", f"- 来源类型：{_sample_label(clip)}",
             f"- 场景：{clip['scenario']}；时长：{clip['duration_sec']}s",
             f"- 覆盖率：{summary['measured']}/{summary['applicable']} 个适用维度",
             "- 解释限制：探索性维度分档，尚未校准，不用于模型排名或发布门禁。", "",
             "| 维度 | 状态 | 分数 | 证据 | 建议阶段 | 建议动作 |", "|---|---|---:|---|---|---|"]
    for row in rows:
        status = "N/A" if row.dim.key in clip.get("not_applicable", []) else ("未评" if row.score is None else ("待复核" if row.need_review else "已评"))
        score = "" if row.score is None else f"{row.score:.1f}"
        lines.append(f"| {row.dim.name} | {status} | {score} | {row.note} | {row.dim.stage if row.need_review else ''} | {row.dim.action if row.need_review else ''} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="对人工观测的世界模型样本元数据做探索性分档")
    parser.add_argument("input", help="样本元信息 JSON 路径")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    parser.add_argument("--markdown", metavar="PATH", help="额外输出 Markdown 记录")
    args = parser.parse_args(argv)
    try:
        clip = json.loads(Path(args.input).read_text(encoding="utf-8"))
        rows, summary = evaluate(clip)
    except FileNotFoundError:
        print(f"[错误] 找不到输入文件：{args.input}", file=sys.stderr)
        return 2
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 2
    print(render_json(clip, rows, summary) if args.json else render_text(clip, rows, summary))
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(clip, rows, summary), encoding="utf-8")
        print(f"\n已写入 Markdown 评测记录：{args.markdown}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
