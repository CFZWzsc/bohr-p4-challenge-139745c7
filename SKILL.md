---
name: research-file-retrieval-locator
description: Use when a research-library case asks which passage, section, page or version of local files answers a question under hard filters, and needs citation-grade back-links; runs a stdlib-only deterministic indexer over /input and writes a contract-complete /app/output/result.json.
---

# 科研文件检索与章节定位

## 用途

在「文件已经在库里，问题指向其中某一段」的场景中，完成一次可复核的交付：

1. 递归盘点输入文件，产出可核对的目录/文件清单与索引统计（含哈希、大小、抽取器、编码、置信度）。
2. 把每个问题转化为可检索的查询，跨文件召回相关段落（BM25-lite 词法打分，中文按单字 + 二元组）。
3. 把命中落到**章节标题 + 行号（文本）或页码（PDF）+ 原文片段**，而不是只返回整个文件。
4. 执行年份、作者、项目、批次、版本、文件类型、章节等硬过滤，无法满足时如实报告，不放宽条件。
5. 为每条命中生成可逐字验证的证据回链（文件路径、哈希、章节、定位、片段、片段哈希）。
6. 输出 `result.json`，覆盖输入 `output_contract` 的每一个字段，并给出 `status`、`uncertainty`、`run_log` 与未命中原因。

## 适用条件

- 输入目录包含 `case.json`（必需）与真实材料文件；可有 `task.md` 作为补充。
- 材料为本地文件：`.txt` `.md` `.markdown` `.json` `.jsonl` `.csv` `.tsv` `.xml` `.html/.htm`，以及带文本层的 `.pdf`。
- 任务是「找哪一段/哪一节/哪一版」并需要过滤条件与证据回链，而不是开放网络文献检索或全文精读。
- 需要确定性复跑：同一输入必须得到同一结果，不依赖时间、随机数或网络。

## 不适用场景

- 无文本层扫描件的 OCR、图片/表格版面理解、加密或损坏 PDF 的内容恢复。
- 开放网络的文献检索、论文全文精读、综述写作。
- 存储系统底层权限建设、跨账号权限审计。
- 依赖语义向量库或外部模型服务的相似度检索（本 Skill 只用标准库词法检索）。

## 输入形态

```text
/input/
  case.json            # 必需；case_id、queries/questions、filters、output_contract
  task.md              # 可选；补充检索范围、过滤条件、输出字段
  <materials>/...      # 任意层级的真实文件
```

`case.json` 中查询可能位于 `queries` / `questions` / `query` / `question` / `prompt` / `search`；过滤条件位于 `filters` 或每个查询的 `filters`；期望字段位于 `output_contract`（对象、字段名列表或 JSON Schema 形式均可）。材料路径可以由 `input_dir` / `materials` / `files` / `path` 指定；缺省时扫描 `case.json` 所在目录。

## 调用方式

在 Skill 安装目录（平台为 `/skills/weekly-skill`）执行：

```bash
python3 /skills/weekly-skill/scripts/sk_entry.py --input /input --work /app/work --out /app/output
```

等价封装（参数完全相同）：

```bash
python3 /skills/weekly-skill/scripts/run_skill.py --input /input --work /app/work --out /app/output
```

- `--input`：目录或单个 `case.json`，默认 `/input`。
- `--work`：工作目录，写 `index.json` 与 `run-review.md` 草稿，默认 `/app/work`。
- `--out`：目录（写 `result.json`）或 `.json` 文件路径，默认 `/app/output`。
- 兼容旧参数名 `--case`、`--output`。
- 无第三方依赖；不需要安装；不访问网络；无状态、可重复执行。

## 输出字段

`result.json` 顶层固定包含：

| 字段 | 含义 |
|-|-|
| `case_id` | 与输入 `case.json` 一致 |
| `status` | `success` / `partial` / `insufficient_input` / `failed` |
| `queries` | 解析出的查询（id、原文、来源、过滤条件） |
| `hits` | 排序后的命中：文件、章节、locator、quote、score、why、confidence、evidence_id |
| `evidence` | 每条命中的回链证据：文件哈希、抽取器、编码、定位、片段、片段哈希 |
| `coverage` | 逐查询覆盖、有命中/无命中、必需字段覆盖、有命中文件集合 |
| `misses` | 未命中与未满足的过滤条件（含原因，不伪造命中） |
| `index_stats` | 文件、段落、章节、字节、PDF、跳过、重复、过滤统计 |
| `uncertainty` | 启发式抽取、低置信编码、词法召回边界等不确定性 |
| `run_log` | 确定性运行步骤与状态判定原因（无时间戳） |
| `summary` | 一句话摘要（字符串） |
| `skipped` / `duplicates` | 跳过文件与重复内容记录 |
| `applied_filters` | 逐查询实际执行的过滤：filter、before、after、excluded、必要时 unsatisfied_reason |
| `conflict_groups` | 同一查询内同一量出现不同数值的冲突组（values/files/quotes），无冲突为 `[]` |
| `versions` | 识别出的版本文件：`version_label` 与被替代文件 `supersedes` |
| `contract_extras` | `output_contract` 每个字段的映射方式与未知字段清单 |

`output_contract` 的每个键都会出现在顶层。别名映射：`results` / `matches` / `citations` / `retrievals` → `hits`；`sources` / `refs` → `evidence`；`missed` / `uncovered` → `misses`；`state` / `index` → `index_stats`；`notes` → `uncertainty`；`summary` → 短字符串。无法识别的键按字段名做最佳尽力投影（结构化对象 + `contract_extras` 记录），不会留空或写 `pending`。

## 检索与章节定位规则

1. **盘点（确定性）**：按相对路径排序遍历；不跟随符号链接；跳过 `__pycache__`、`.git` 等目录；文件数、单文件字节数、文本字符数有上限（见 `assets/policy.json`），超出则截断并标注。
2. **文本抽取**：文本类按 UTF-8 → GB18030 → Big5 → Latin-1 解码；HTML/XML 去标签并保留 `h1-h6` 为 Markdown 标题；PDF 仅解压 FlateDecode 内容流并抽取 `Tj`/`TJ` 字符串（含 UTF-16BE 十六进制串），抽不到文本就记为 `no_text_layer`，**绝不编造文本**。
3. **分块与章节**：按空行与标题切段；标题识别支持 Markdown `#`、编号标题 `1.2`、中文序号 `一、`、常见章节名（Abstract/Methods/Results/…/摘要/方法/结果）；每段记录 `section`、`section_path`、行号区间、字符偏移；PDF 记录页码（启发式）与字符偏移，不伪造行号。
4. **查询模型**：从 `case.json` 与 `task.md` 提取查询、查询级过滤器、全局过滤器、必需字段。相同文本去重，最多 `max_queries` 条。
5. **打分**：BM25-lite，`k1=1.2`、`b=0.75`；中文二元组权重高于单字；加入精确短语、章节标题、文件名三类小幅加权；低于 `min_score` 不返回。
6. **排序**：按分数降序，再按文件路径、行号/页码、字符偏移、段落 id 升序，保证同分时顺序稳定。

## 证据回链格式

每条 hit 提供：

```json
{
  "hit_id": "q1-h1",
  "query_id": "q1",
  "file": "01_project/2021-buffer-notes.md",
  "section": "Buffer recipe",
  "section_path": ["Materials", "Buffer recipe"],
  "locator": {"kind": "line", "line_start": 12, "line_end": 18, "char_start": 210, "char_end": 388},
  "page": null,
  "quote": "……原文片段……",
  "quote_sha256": "<片段 UTF-8 的 sha256>",
  "score": 3.214,
  "matched_terms": ["buffer", "配方", "tris"],
  "why": "terms: ...; exact phrase; section heading match",
  "confidence": "high",
  "evidence_id": "e0001"
}
```

- 文本文件：`locator.kind="line"`，`line_start/line_end` 为 1 起算的原始行号，`char_start/char_end` 为抽取文本中的字符偏移。
- PDF：`locator.kind="page"`，`page` 为启发式物理页，`char_start/char_end` 为抽取文本偏移；`confidence="low"`，不作为精确印刷页码声明。
- 每条 hit 在 `evidence` 中有同 `evidence_id` 的记录，含文件 `sha256`、抽取器、编码、`quote_sha256`，便于他人复跑核对。
- 年份过滤生效时，hit 带 `year_status`（`known`/`unknown`）与 `year`；版本文件 hit 带 `version_label` 与 `supersedes`。
- 复核方法：打开 `file`，跳到 `line_start`（或 `page`）逐字比对 `quote`；不一致即视为该命中不可用，必须在 `run-review.md` 中如实记录。

## 过滤与排序口径

- 过滤器是硬约束，先作用于文件（`ext`/`file_type`/`format`、`year`、`author`、`project`/`project_id`、`batch`、`version`、`path`/`glob`），再对命中应用 `section` 过滤。
- `year` 支持列表与 `{"gte","lte","eq"}` 区间；年份仅取自路径/文件名、front matter/meta 的 `year`/`date`、HTML `<meta>` date/time 与 CSV 逐行 date 列，正文里散落的年份不作为文档年份。
- `year` 是硬约束：已知年份不符的文件整篇排除，CSV 按行 date 过滤；无年份信息的文件保留召回，但每条 hit 标记 `year_status: "unknown"`，结果写入 `applied_filters`（before/after/excluded/unsatisfied_reason）。
- 文件类型别名：`pdf`、`markdown`、`text`、`data`、`json`、`csv`、`xml`、`html` 等映射到扩展名集合；未知类型按扩展名精确匹配。
- 任一过滤步骤把候选集变为空时：返回空命中，并在 `misses` 写入 `filter_unmatched` / `filters_excluded_all_files` / `section_filter_excluded_all_hits`，**不放宽条件、不猜测命中**。
- 排序只使用分数与确定性键；不做跨查询合并去重，保留每个查询各自的 top-k；同内容文件按哈希识别，重复副本不重复计入排名。

## 幂等与恢复

- 输出不含时间戳、随机数、绝对机器名；同一输入重复执行得到字节级一致的结果（工作目录中的 `run-review.md` 若已存在则不被覆盖）。
- 结果先写 `result.json`，再写 `work/index.json` 与草稿 `run-review.md`；工作目录不可写不影响 `result.json`。
- 已存在的人工 `run-review.md` 不会被自动覆盖；删除该文件即可让脚本重新生成草稿。
- 任何未捕获异常都会触发兜底：写出含全部契约键的 `result.json`（`status="failed"`）并以退出码 0 结束。
- `index.json` 是索引快照，可用来确认上一轮索引了哪些文件、跳过了什么、哈希是否变化。

## 异常处理

| 情况 | 处置 | status |
|-|-|-|
| 输入目录为空 / 无可读文本 | 空索引 + `no_indexable_files`，`uncertainty` 说明 | `insufficient_input` |
| `case.json` 缺失 | 仍尝试索引与 `task.md` 查询，记 `case_file_missing` | `insufficient_input` |
| `case.json` 损坏/非对象 | 记 `failed`，仍写完整契约键 | `failed` |
| 无查询字段 | 只返回索引，`no_query` | `partial` |
| 文件无权限 / 读取失败 | 记入 `skipped`（`unreadable`/`stat_error`），其余文件继续 | `partial` |
| 编码异常 | 逐级回退解码；`latin-1`/替换字符标记 `low` 置信 | `partial` |
| 超大文件 | 按字节与字符上限截断，`truncated=true` | `partial` |
| 二进制 / 不支持扩展名 | 安全跳过，记 `binary_content`/`unsupported_extension` | 不降级 |
| PDF 无文本层/解压失败 | 记 `no_text_layer`，不伪造文本 | `partial` |
| 过滤条件无匹配 | 空命中 + `filter_unmatched`，不放宽 | `partial` |
| 全部查询都无命中 | 空命中 + `no_passage_above_threshold` | `partial` |
| 重复内容文件 | 按 sha256 识别，只排名首份，记 `duplicates` | 不降级 |

## 交付前自检清单

- [ ] `python3 scripts/sk_entry.py --input <案例目录> --work <工作目录> --out <输出目录>` 退出码为 0。
- [ ] `result.json` 的 `case_id` 与输入一致。
- [ ] `output_contract` 的每个键都存在于顶层，且值不是 `pending`/`TODO`。
- [ ] `status` 属于 `success` / `partial` / `insufficient_input` / `failed`，且与 `misses`、`uncertainty` 自洽。
- [ ] 每条 hit 都有 `file`、`section`（或显式为 `null`）、`locator`、`quote`、`score`、`why`、`evidence_id`。
- [ ] 逐条打开 hit 的 locator 核对 quote，确认不是文件名命中冒充正文命中。
- [ ] 过滤条件无匹配时，`hits` 为空且有原因，没有编造命中。
- [ ] 空目录 / 无权限 / 编码异常 / 超大文件 / 无命中都有对应记录，没有把未完成写成完成。
- [ ] 重复运行两次，`result.json` 内容一致。
- [ ] 提交目录无 `__pycache__`、无符号链接、无令牌或密钥。

## 执行 Agent 的强制交付步骤

1. **先运行入口**：`python3 /skills/weekly-skill/scripts/sk_entry.py --input /input --work /app/work --out /app/output`（或 `scripts/run_skill.py`，参数相同）。
2. **读取结果**：打开 `/app/output/result.json`，确认 `case_id`、`status`、`contract_extras` 与 `misses`；确认每个 `output_contract` 键都存在。
3. **逐条核对命中**：对每个 `hit`，按 `file` 与 `locator`（`line_start` 或 `page`）打开原文，逐字比较 `quote` 与 `evidence.quote_sha256`。核对失败或定位漂移的命中必须降级或剔除，并在复核记录中说明；不得保留无法回链的命中。
4. **诚实写复核**：把运行命令、`status`、命中核对结果（通过/失败及原因）、未命中项、过滤未满足项、限制与不确定性写入 `/app/output/run-review.md`。该文件由执行 Agent 负责，不能只保留脚本生成的 `work/run-review.md` 草稿，也不能把 `partial`/`insufficient_input` 写成 `success`。
5. **交付**：保留 `/app/output/result.json` 与 `/app/output/run-review.md`；`/app/work/index.json` 作为可复核的索引快照一并保留。不要修改输入文件。

## 最小调用示例（示例，非正式案例结果）

```bash
python3 scripts/sk_entry.py --input examples/demo-case --work /tmp/demo-work --out /tmp/demo-out
```

示例输入见 `examples/demo-case/case.json`，说明见 `examples/quickstart.md`；示例命中仅用于演示接口与定位字段，不代表任何平台案例结果。
