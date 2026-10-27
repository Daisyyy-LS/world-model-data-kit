# 输入 Schema 与解释口径

`score.py` 只读取人工或规则整理的观测元数据，不读取视频、不调用模型，也不验证标注真值。当前评分阈值是探索性占位规则，尚未通过专家校准或多人标注实验；输出不能用作模型排名、发布门禁或“模型质量”结论。

## 必填字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `clip_id` | string | 唯一样本 ID |
| `scenario` | string | 本样本任务和测量目标 |
| `duration_sec` | number > 0 | 样本时长 |
| `sample_type` | enum | `synthetic_demo` / `model_output` / `human_authored` |

`model_output` 还必须提供 `model`、`run_metadata`、`source_metadata`、`review_metadata` 对象及下表指定的键。无法获得的值应显式填 `null` 并在 `source_note` 说明，不要猜填。这样评测记录能追溯来源、权属状态、运行环境和复核口径；它仍不代表脚本会验证这些元数据的真实性。

| 对象 | 必须包含的键 |
|---|---|
| `model` | `name`、`version` |
| `run_metadata` | `generated_at`、`prompt_version`、`environment`、`parameters`、`seed` |
| `source_metadata` | `asset_id`、`authorization_status`、`sensitivity` |
| `review_metadata` | `annotator_id`、`task_card_version`、`confidence`、`adjudication_state` |

示例元信息：

```json
{
  "clip_id": "demo-room-001",
  "scenario": "房间回访后检查静态元素错配比例",
  "duration_sec": 42,
  "sample_type": "synthetic_demo",
  "source_note": "人工构造的流程示例；不是模型生成结果",
  "model": null,
  "run_metadata": null,
  "review_metadata": {"annotator_id": "reviewer-a", "guide_version": "0.1", "confidence": "medium"}
}
```

## 状态必须区分

- **已评**：有明确观测和有效分母，才能计算探索性维度分。
- **未评**：字段缺失或没有可用观测；不计分，不推断为成功。
- **N/A**：任务设计明确不包含该能力，在 `not_applicable` 显式列出维度 key。
- **零失败**：已检查且分母大于 0，观察到失败数为 0。

缺字段与零失败不同。分母为 0、字段不齐或任务没有适用观测时，当前工具返回“未评”。

## 观测字段

| 维度 key | 需要的字段 | 解释 |
|---|---|---|
| `consistency` | `revisit.checked_elements`、`static_element_mismatch` | 被检查的静态元素及错配数 |
| `permanence` | `object_permanence.occluded_objects`、`attr_changed_after_occlusion` | 遮挡物体数及属性漂移数 |
| `physics` | `physics.frames_checked`、`violations[]` | 检查帧数与人工记录的违反事件对象；每项含 `type`、`time_start_sec`、`time_end_sec`。同一持续事件按一项计数；报告每 100 帧事件数，不等同于客观物理真值 |
| `camera` | `camera.trajectory_error_px`、`max_tolerated_px`、`frame_width_px` | 误差、任务容差和画幅宽度；采用误差/容差比，仍需统一轨迹定义与对齐算法 |
| `instruction` | `instruction_following.n_instructions`、`n_failed` | 可判定指令数及失败数 |
| `latency` | `interaction.p95_latency_ms`、`latency_target_ms` | 实测 P95 与本任务明确配置的目标；同时记录硬件、负载、计时边界和预热策略 |
| `memory` | `memory.n_checkpoints`、`recall_correct` | 预先定义的状态检查点总数及正确检查数；应保存各检查点明细，不能把动作步数直接当作记忆题数 |
| `editability` | `editability.local_edits`、`inconsistent_after_edit` | 编辑次数与非编辑区域不一致数 |

计数必须是非负整数，错误数不能超过检查数。`camera` 的误差、容差和画幅单位须一致。`latency_target_ms` 是场景配置而非行业通用常数。

## 分档与汇总

各维度返回 1–5 的**探索性序数分档**。当前分界点未经样本校准，不能解释为等距分数。输出给出覆盖率、未评状态和待复核维度；工具不计算跨维度总分或均值。阈值与复核线需由目标场景、专家口径和试标结果共同确定。

## 两种分类轴，不要混用

**L0–L3 表示数据加工/处理流程的建议阶段**，不是样本的质量等级，也不表示样本类型：

- **L0 原始素材**：来源、授权、脱敏、基础元信息与原始片段。
- **L1 结构化观测**：对象/相机/事件/状态等有口径的标注。
- **L2 交互轨迹**：明确的 `(state, action, observation/next_state)` 序列及时间信息。
- **L3 归因与学习用途**：经复核的失败链、偏好对或 hard negative；必须记录样本用途和证据，不是所有失败都自动进入 L3。

样本类型（如 `positive`、`negative`、`preference_pair`、`trajectory`）应单独记录；一条记录可同时有 L1 标注并被指定为 L3 失败样本。
