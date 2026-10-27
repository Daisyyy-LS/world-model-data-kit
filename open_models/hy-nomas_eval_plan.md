# Hy-Nomas 实测与评测计划（腾讯混元开源交互式世界模型）

> 撰写日期：2026-10-28｜作者：梁爽
> 状态：**计划，不是结果**。我手上没有 GPU，尚未实跑；下面是基于官方 README 与公开报道整理的上手路径与评测设计，所有结论需在实跑后回填。
> 配套：[world-model-playground](https://daisyyy-ls.github.io/world-model-playground/)（评测台原型）· `eval_kit/score.py`（8 维打分）· `taskcards/eval_task_cards.md`（3 张任务卡）· [cinematic-shot-spec](https://github.com/Daisyyy-LS/cinematic-shot-spec)（分镜/越轴规则）

---

## 1. 为什么值得专门测它

| 维度 | Hy-Nomas（腾讯混元，2026-10-23） | Genie 3（Google DeepMind） |
|---|---|---|
| 开源 | 代码 + 权重 + 训练与数据标注流程全开，**Apache 2.0** 可商用 | 闭源，仅 Project Genie 限量网页原型 |
| 硬件门槛 | 官方配置支持**单张 RTX 4090**（INT8 量化 + 滑动窗口 + CPU offload） | 云端推理，官方因算力成本限制体验时长 |
| 控制方式 | 文本 prompt + **相机轨迹**双模态，在段边界切换 | 导航动作 + promptable world events，24fps 逐帧 |
| 一致性机制 | **lifelong KV cache**，跨段保持长程一致 | 约 1 分钟视觉记忆，重返旧场景仍一致 |
| 定制 | 支持 LoRA 微调（官方给了 cinematic / robotics 两个 LoRA） | 无微调入口 |

**对「世界模型训练数据」岗位的意义**：过去做评测闭环只能对着封闭 API 的网页原型做主观判断；Hy-Nomas 让「生成 → 打分 → 定位缺陷 → 回流标注」这条闭环第一次可以在本地复现、可版本化、可回归。这正好是这个岗位要干的活。

## 2. 上手路径 A：Docker（官方推荐）

```bash
export HY_NOMAS_IMAGE="hynomasoss/hynomasoss:cu130"
docker pull "$HY_NOMAS_IMAGE"
docker run --rm -it --gpus all --ipc=host --network=host \
  -v "$PWD:/workspace/Hy-Nomas" -w /workspace/Hy-Nomas "$HY_NOMAS_IMAGE" bash

# 容器内下载权重（HF，保持目录结构）
export Hy_Nomas_Model_Path=/path/to/Hy-Nomas
hf download tencent/Hy-Nomas --local-dir "${Hy_Nomas_Model_Path}"
```

权重构成：DiT（base / distilled / INT8 量化）+ cinematic LoRA + robotics LoRA（RealSource-World 真实机械臂数据）+ DCAE-V VAE + 微调版 PixelUMM 解码器（含 FP8 版）。

## 3. 上手路径 B：单卡 4090（低显存）

官方 `configs/experiment/single_gpu_low_mem_inference.yaml` 的四项优化：INT8 DiT 权重与 KV cache（计算仍 BF16）、滑动窗口历史（限制保留上下文）、CPU offload（文本编码器/DiT/VAE 分阶段上卡）、单卡 kernel（FlexAttention + CUTLASS FP8，`sp_size=1`）。

```bash
bash scripts/inference/inference_from_config.sh configs/experiment/single_gpu_low_mem_inference.yaml \
  dit_weight="${Hy_Nomas_Model_Path}/DiT/int8/…" \
  inference.prompt=/path/to/prompts.json \
  inference.save_path=./outputs/lowmem
```

> ⚠️ 待核对：第三方报道称单卡版把卡上保留历史限制在约 18 latent frames、其余归档到系统内存。这个数字我未在 README 中直接读到，实跑时需确认——**它直接决定「房间回访」类任务能测多长**。

## 4. 交互使用

**方式一：官方在线 demo**（需注册账号）
https://hy-nomas.hy.tencent.com/demo ｜ 项目主页 https://hy-nomas.hy.tencent.com/
（该入口来自媒体报道，我未注册实测，体验流程待本人验证。）

**方式二：本地 Reactor runtime（官方 README 的 Interactive Demo 章节）**

```bash
cd reactor && reactor build
reactor run --gpus '"device=0,1,2,3,4,5,6,7"' --shm-size 8g --port 8080
# 等 curl http://localhost:8080/health 返回 available
cd reactor/demo && npm ci && npm run dev -- --port 5173
```

浏览器打开 `http://localhost:5173`，Runtime 端点填 `http://localhost:8080` → Connect → 输入 prompt → Start world。能力：**多轮实时改 prompt + WASD/方向键控制相机 + 上传相机轨迹 + 保留世界历史**。官方测试配置为 8×B200。

**消费级现实**：单卡 4090 跑不了实时交互（官方低显存配置是离线分段推理）。所以我设计的评测用**「分段注入」**方式模拟多轮——每段一个 prompt / 一条相机轨迹，段边界处注入新指令，这恰好是它的原生交互模型（segment-based autoregressive）。

## 5. 我的评测设计（重点）

四条任务，每条产出一个 clip JSON，直接喂给 `eval_kit/score.py`：

| 任务 | 分段指令 | 主要测什么（8 维中的子集） | 对应我已有的资产 |
|---|---|---|---|
| **T1 影视分镜** | 段1 中景双人对话 → 段2 推近 → 段3 环绕 120° | 相机控制精度、跨视角一致性、主体 ID 稳定 | cinematic-shot-spec 的景别/越轴规则：环绕后窗户与视线方向是否仍在同侧 |
| **T2 事件持久化** | 段1「下起雨」→ 段2、3 只给相机指令，不提天气 | 状态持续性（雨是否还在） | playground 的世界事件注入 chips |
| **T3 房间回访** | 段1 布置房间 → 段2–4 离场穿行 → 段5 返回原房间 | 空间记忆、拓扑一致性 | taskcards 任务卡 1「房间回访」 |
| **T4 双主体协作** | 段1 A 递物给 B → 段2 B 放置 | 多智能体交互、接触时序、因果链 | 对应 Genie 3 自曝的「多智能体交互难」 |

**需要新增的一个维度**：现有 8 维是按「一段生成结果」设计的；Hy-Nomas 是分段生成且自带 lifelong KV cache，应增加 **跨段一致性**（段间主体外观 / 光照 / 布局的漂移量），并把「滑动窗口长度」作为显式变量记录在 clip 里——它是官方自认的核心权衡点。

**预期最容易掉分的两项**（赌，待实跑验证）：T2 事件持久化、T3 房间回访。理由：官方 README 明确说明标准配置会「随生成持续消耗越来越多缓存」，记忆是这类模型的共同瓶颈。

## 6. 输出与回流

1. 每段结果填一个 clip JSON（字段见 `eval_kit/schema.md`）
2. `python eval_kit/score.py samples/clip_xxx.json` → 8 维打分 + 数据回流建议
3. 汇总成一张表：任务 × 维度 × 得分 × 回流层（L0–L3）× 建议采集动作
4. 把失败样本按 L3 整条入库（不是截单帧——记忆类缺陷必须是序列级样本）

## 7. 诚实边界（面试时我会主动说清）

- **这是计划不是结果**：我目前没有 GPU 环境，上述能力描述全部来自官方 README 与公开报道，尚未实跑验证
- **Hy-Nomas-Bench**（媒体报道提到的 280 条测试用例、含 spatial-memory 子集）：我未能在 Hugging Face 上直接打开该数据集页面，链接与规模待核实
- 技术报告官方标注为 **Coming Soon**，模型分数尚未由官方公布，第三方复现结果也还没有
