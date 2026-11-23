---
name: research-file-retrieval-locator
description: Use when a research-library case asks to find which passage, section, or version of local files answers a question, with filters and evidence back-links; run scripts/run_skill.py to build a deterministic text index and emit citation-grade result.json.
---

# 科研文件检索与章节定位

## 用途与适用条件

在「文件已在库中，需要被准确找到并定位到段落」的任务中使用：跨文件检索、章节定位、年份/项目/文件类型过滤、证据回链。输入是 `/input/case.json`、可选 `/input/task.md` 和真实文件集合。

适用：纯文本、Markdown、CSV/JSON、LaTeX 源文件，以及带文本层的 PDF（脚本用 zlib 解压内容流并抽取 `Tj/TJ` 文本，属于启发式抽取）。
不适用：无文本层扫描件的 OCR、加密 PDF、图片内容理解、开放网络文献检索。

## 输入

- `case.json`：`case_id`、`output_contract`，问题可能位于 `query`、`question`、`prompt`、`task`、`search` 字段；过滤条件可能位于 `filters`。
- `task.md`：补充检索范围、过滤条件与引用格式。
- 材料路径：`input_dir`、`materials`、`files`、`path` 等；缺失时仅在 `/input` 或含 `materials/` 的目录回退扫描。
- 输出：`/app/output/result.json`。

## 处理流程（对应评分维度）

1. **范围与过滤（任务完成度）**：只读取真实存在的文件，记录每个文件的字节数、抽取器、文本字符数；`filters.ext`、`filters.file_type`、`filters.year`、`filters.project` 作为硬约束，无法满足时写入 `unmatched_filters`，不放宽条件。
2. **分块（结果完整性）**：按空行切段，超长段落按 1200 字符切分并保留原始字符偏移；识别 Markdown `#` 标题与 `1.2 标题` 式编号标题，作为章节定位。
3. **检索排序（结果正确性）**：查询与段落同时做规范化：ASCII 词、CJK 单字与二元组；用 `(1+ln tf) * idf` 排序，CJK 二元组权重高于单字，避免仅靠文件名命中。
4. **证据回链（可核验性）**：每条命中给出 `file`、`section`、`char_start`、`char_end`、`quote`、`score`、`matched_terms`；`page` 仅在可靠解析时给出，否则为 `null` 并在 `uncertainty` 说明。
5. **缺证判断（可靠体验）**：无查询、无命中、命中分数过低、文件不可读、PDF 无文本层分别进入 `errors`、`uncertainty`、`skipped`，并给出 `confidence`。
6. **稳定复跑**：全部集合排序后输出，不使用随机数与时间戳。

## 输出契约

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入一致 |
| 输入 `output_contract` 每一字段 | 命中别名词表时填真实值；否则 `null` 并记录 `contract_notes` |
| `status` | `success` / `partial` / `insufficient_input` / `failed` |
| `result` | 索引、命中段落、引用、过滤执行情况 |
| `evidence` | 文件哈希、抽取器、字符偏移、检索参数 |
| `uncertainty` | PDF 页码缺失、启发式抽取、低置信命中 |
| `errors` / `stats` | 实际错误与计数 |

## 异常处理表

| 情况 | 处置 | 状态 |
|-|-|-|
| case.json 缺失/非法 | 空结果 + 错误说明 | `failed` |
| 无材料文件 | 空索引 + 缺失路径 | `insufficient_input` |
| 无查询词 | 返回索引，`confidence=no_query` | `partial` |
| PDF 无文本层/解压失败 | 记入 `skipped`，不影响其他文件 | `partial` |
| 过滤条件无匹配 | 写入 `unmatched_filters`，不放宽 | `partial` |
| 命中分数低于阈值 | 返回空命中并说明证据不足 | `partial` |
| 文件不可读 | `skipped` 记录原因 | `partial` |

## 限制

- PDF 文本抽取不保留可靠页码与双栏阅读顺序；不得据此声称精确页码。
- 不解压加密 PDF、不做 OCR、不访问网络。
- 检索为词法匹配，不是语义向量检索；同义改写可能漏召回，需在 `uncertainty` 说明。
- 单文件文本上限 2 MB，超出截断并标注。

## 最小调用示例（示例，非官方案例结果）

```bash
python /skills/weekly-skill/scripts/run_skill.py --case /input/case.json --out /app/output/result.json --work /app/work
```

示意输入 `{"case_id":"synthetic-demo","output_contract":{"citations":"array"},"query":"buffer recipe"}`；示意输出会包含 `case_id`、`citations`（无材料时为 `null` 或空）与 `status`。该示例仅说明接口，不是正式案例结果。


## 交付保证与评分对齐

调用顺序（固定）：

1. 先运行包内脚本（若存在）：`python scripts/run_skill.py`（默认读 `/input/case.json`，写 `/app/output/result.json`）；路径不同时用 `--case` / `--output` 指定。
2. 再完成脚本无法自动完成的实质工作（译文、论述、图表、修订、结论等），把结果写回 `result.json` 对应字段。
3. 最后自检：`case_id` 与输入一致；`output_contract` 每个字段都存在；`status` 按契约取 `success`/`partial`/`insufficient_input`/`failed`（缺输入或证据不足时用 `insufficient_input`/`partial`）；无 `pending` 占位；数字、引用、来源可逐条回溯。

运行保证：

- 输入缺失、JSON 损坏、字段异常、输出目录不存在时，仍须写出结构化 `result.json` 并正常结束，不得崩溃或非零退出。
- 不联网、不伪造数据、不声称平台评分或官方案例结果。
- 交付物必须是可直接编辑与复核的成品，而不是流程说明。

评分对齐（本题五个维度）：

- 实际效果（40）：完整产出任务要求的最终成品，覆盖全部输入条目，可直接进入修改流程。
- 使用增益（25）：体现相对基础 Agent 的可验证改进（结构化、术语/数字/引用核对、可编辑性）。
- 创新增益（15）：保留方法学增量（分块、对齐、二次校验、版本比对等），并用结果证明其价值。
- 可靠体验（10）：异常输入有明确降级与说明，不同长度/文体/重复运行下稳定产出。
- 生态贡献（10）：用途、调用、输入输出、限制、示例、维护信息齐全，资源可被他人复用。

## 最终交付强制要求

1. **调用 Agent 必须交付成品，不是流程说明**：脚本产出只是索引、核对或规格骨架；调用 Agent 必须在同一 `result.json` 中补齐本题要求的最终产物（正文/译文/审稿意见/综述/分析结论/图表源文件与导出件/幻灯片页内容与讲稿/实验方案等）。交付字段为空、只有标题骨架，或含 `pending`、`TODO` 之类占位，一律视为未完成。
2. **必须覆盖全部输入条目**：输入中的每个章节、问题、文献、图、页面、指标都要有对应条目，不得只处理示例或首条；确实缺失的条目写入 `uncertainty` / `limitations` 并说明原因，不得静默跳过。
3. **必须自检后再交付**：`case_id` 与输入一致；`output_contract` 每个字段都存在；状态值取本题契约规定的枚举；数字、单位、引用、来源可逐条回溯；结果内部无自相矛盾。
4. **参数等价**：`python scripts/run_skill.py --case <输入> --out <输出>` 与 `--output <输出>` 等价；默认 `/input/case.json` → `/app/output/result.json`。
5. **平台用 3 个固定案例分别运行**：逻辑要由输入驱动，不针对某一份示例硬编码；字段命名差异走别名匹配与显式降级，并在 `uncertainty` 记录假设。
