# 输出示例说明

仓库中的 `clip_A_interactive.json` 和 `clip_B_cinematic.json` 是**人工构造的示意输入**，不是任何模型的真实输出或真实评测结果。运行 `score.py` 会据这些占位计数生成探索性分档；这只能证明脚本处理了输入，不能证明模型能力或阈值有效。

当前输出会逐维呈现以下状态：

| 状态 | 含义 | 是否进入维度均值 |
|---|---|---|
| `scored` | 观测字段齐全且分母有效 | 是，仅探索用途 |
| `unscored` | 未提供观测或分母无效 | 否；保留为未评 |
| `not_applicable` | 任务卡显式声明不适用 | 否 |

输出提供已评覆盖率、每个维度的证据与建议动作，不计算跨维度总分或均值。真实模型评测记录还应补齐模型及版本、提示词与参数、运行环境、素材来源/授权、任务卡版本、标注员与复核信息。

手动查看输出：

```bash
python eval_kit/score.py eval_kit/samples/clip_A_interactive.json
python eval_kit/score.py eval_kit/samples/clip_B_cinematic.json --json
```

阈值需通过场景专家校准、小规模双人试标和分歧复盘后再调整。本仓库目前没有多人一致性数据，也没有真实模型基准结果。
