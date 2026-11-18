#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 world-model-playground 导出的记录桥接成 eval_kit/score.py 的输入骨架。

用途：打通「测评台标注 → 数据套件评测」这条链路。测评台导出的记录带
source_type（real_model / schematic）、模型版本、端点、提示词、参数、产物清单、
故障标签等元信息，但不包含 score.py 所需的逐维观测计数（如 revisit.checked_elements）。
因此本脚本只做「元信息桥接 + 故障标签映射」，输出的 clip JSON 各维度保持未评，
需人工补齐观测字段后才能由 score.py 打分——绝不凭故障标签反推虚假的观测计数。

重要：schematic（CSS 示意）记录会被单独剔除并计数，不进入 model_output 评测，
避免把演示流程误当成真实模型证据。
"""

import argparse
import json
import sys
from pathlib import Path

# 测评台故障标签 tag key → score.py 维度 key（仅作导航提示，不构成打分）
TAG_TO_DIM = {
    "view_inconsistency": "consistency",
    "crossseg_drift": "crossseg",
    "memory_loss": "memory",
    "state_reset": "memory",
    "physics_break": "physics",
    "instruction_miss": "instruction",
    "camera_error": "camera",
    "edit_bleed": "editability",
    "latency_high": "latency",
    "object_drift": "permanence",
}
# 已知但暂未映射到维度的标签（保留在 annotation 中供人工判断）
KNOWN_UNMAPPED = {"topology_error", "interaction_missing"}


def build_clip(rec: dict) -> dict:
    """把一条 real_model 记录转成 score.py 的 clip 骨架（维度未评）。"""
    tags = rec.get("failure_tags") or []
    ann = [{
        "tag": t.get("tag"), "zh": t.get("zh"),
        "suggested_stage": t.get("layer"),
        "suggested_action": t.get("action"),
        "hint_dimension": TAG_TO_DIM.get(t.get("tag")),
    } for t in tags if isinstance(t, dict)]

    clip = {
        "clip_id": rec.get("id") or rec.get("case_id") or "PLAYGROUND-UNKNOWN",
        "scenario": (rec.get("title", "") + " · " + rec.get("source_mode", "")),
        "duration_sec": 1.0,  # 占位：测评台记录不含时长，打分前需人工填写真实值
        "sample_type": "model_output",
        "source_note": (
            "由 world-model-playground 导出的 real_model 记录桥接而来。"
            "模型/端点/提示/参数/产物/故障标签来自测评台真实标注，"
            "但不含逐维观测计数，各维度保持未评；需人工补齐观测字段后才能打分。"
            "duration_sec=1.0 为占位，必须改为真实值。"
        ),
        "model": {"name": rec.get("model") or "unknown", "version": rec.get("model") or "unknown"},
        "run_metadata": {
            "generated_at": rec.get("run_date"),
            "prompt_version": rec.get("prompt") or None,
            "environment": rec.get("source_mode") or "playground",
            "parameters": rec.get("params") or None,
            "seed": rec.get("seed") or None,
        },
        "source_metadata": {
            "asset_id": ", ".join(rec.get("files") or []) or None,
            "authorization_status": "to_verify",
            "sensitivity": "to_verify",
        },
        "review_metadata": {
            "annotator_id": "playground_annotation",
            "task_card_version": None,
            "confidence": "not_assessed",
            "adjudication_state": "not_reviewed",
        },
        "endpoint": rec.get("endpoint"),
        "meta_path": rec.get("meta_path"),
        "playground_annotations": ann,
        "playground_scores": {"A": rec.get("scoreA"), "B": rec.get("scoreB"),
                              "note": "测评台的人工练习评分，非 score.py 维度分，不参与分档"},
        "not_applicable": [],  # 由使用者按任务卡显式填写；桥接脚本不代为声明
    }
    return clip


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="把测评台导出记录桥接成 score.py 输入骨架")
    ap.add_argument("input", help="world-model-playground 导出的 wmfl-records.json 路径")
    ap.add_argument("-o", "--out-dir", default=".", help="输出目录（默认当前目录）")
    ap.add_argument("--include-schematic", action="store_true",
                    help="是否也转换 schematic 示意记录（默认剔除，避免污染真实评测）")
    args = ap.parse_args(argv)

    try:
        data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[输入错误] {exc}", file=sys.stderr)
        return 2

    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        print("[输入错误] 未找到 records 数组", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    real = [r for r in records if r.get("source_type") == "real_model"]
    schem = [r for r in records if r.get("source_type") == "schematic"
             and not r.get("source_type", "").startswith("real")]
    if args.include_schematic:
        # 示意记录也转，但 sample_type 明确标成 human_authored，不冒充模型输出
        for r in schem:
            c = build_clip(r)
            c["sample_type"] = "human_authored"
            c["source_note"] = "CSS 示意记录，非模型输出。" + c["source_note"]
            real.append(r | {"_schematic": True, "_clip": c})

    written = 0
    for rec in real:
        clip = rec.get("_clip") if rec.get("_clip") else build_clip(rec)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in clip["clip_id"])
        out = out_dir / f"{safe}.json"
        out.write_text(json.dumps(clip, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已写出 {out}（{len(clip['playground_annotations'])} 个故障标签 → 建议维度未评）")
        written += 1

    print(f"\n共处理 {len(records)} 条记录：real_model {sum(1 for r in records if r.get('source_type') == 'real_model')} 条，"
          f"schematic {len(schem)} 条（默认剔除）。")
    print(f"实际写出 {written} 个 clip 骨架。注意：各维度仍未评，需人工补齐观测字段后运行 score.py。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
