# 输入 Schema

`score.py` 读入一段生成结果的**元信息**（不是视频本身）。设计原则：字段应当是**评测时能被人工或自动稳定观测到的量**，而不是主观感受。

## 顶层字段

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `clip_id` | string | 是 | 样本唯一 ID，建议编码为 `任务类型-序号-场景名` |
| `scenario` | string | 是 | 一句话描述这个 clip 在测什么 |
| `duration_sec` | number | 是 | 时长（秒），用于把物理量归一化为「每 10 秒违反数」 |
| `not_applicable` | string[] | 否 | 本任务类型不适用的维度 key，如影视镜头填 `["latency","memory"]`。这些维度计为 N/A，不进加权 |
| `revisit` | object | 否 | 时空一致性证据 |
| `object_permanence` | object | 否 | 物体恒存证据 |
| `physics` | object | 否 | 物理合理性证据 |
| `camera` | object | 否 | 相机控制精度证据 |
| `instruction_following` | object | 否 | 指令跟随证据 |
| `interaction` | object | 否 | 交互延迟证据 |
| `memory` | object | 否 | 长时记忆证据 |
| `editability` | object | 否 | 局部可编辑性证据 |

缺失的字段按「零失败」处理，并在证据栏如实显示——**不会因为漏填而虚高打分**。

## 各维度字段

| 维度 key | 子字段 | 含义 |
|---|---|---|
| `consistency` | `revisit.n_revisits` / `checked_elements` / `static_element_mismatch` | 回访次数、被检查的静态元素数、其中发生错配的数量 |
| `permanence` | `object_permanence.occluded_objects` / `attr_changed_after_occlusion` | 被遮挡物体数、遮挡后属性发生漂移的数量 |
| `physics` | `physics.frames_checked` / `violations[]` | 检查帧数、违反项列表（每条一句话） |
| `camera` | `camera.instruction` / `trajectory_error_px` / `max_tolerated_px` | 运镜指令、实际轨迹与指令的偏差像素、可接受上限 |
| `instruction` | `instruction_following.n_instructions` / `n_failed` | 指令总数、未被执行的数量 |
| `latency` | `interaction.p95_latency_ms` | 端到端响应时延 P95（毫秒），基准 300ms |
| `memory` | `memory.n_steps` / `recall_correct` | 任务链步数、能被正确回溯的步数 |
| `editability` | `editability.local_edits` / `inconsistent_after_edit` | 局部编辑次数、编辑后周边穿帮次数 |

## 打分口径

- 每个维度先算**失败率**，再映射到 0–5 分（见 `score.py` 里的 `_band`），阈值按「什么样的失败率会让用户出戏」来定，而不是均分。
- 权重不相等：**一致性、恒存、记忆**权重最高（1.4 / 1.3 / 1.3）——这三项决定「世界是否可信」；可编辑性最低（0.8），它更多是加分项。
- 低于 **3.5** 分视为需要回流，按 `缺口 × 权重` 排优先级。

## 已知的局限（想清楚边界，别装作完备）

1. 失败率目前靠人工观测填写，**规模化后必须做自动检测**（跨帧特征比对、光流一致性、目标重识别），否则评测成本会反过来卡住数据迭代速度。
2. 8 个维度不是正交的：一致性差往往连带恒存差，排优先级时会被重复计算，后续应做相关性分析再降维。
3. 这套 schema 针对**可交互世界**。纯视频生成（如影视镜头预演）请填 `not_applicable`，否则延迟和记忆会被误判为缺陷。
