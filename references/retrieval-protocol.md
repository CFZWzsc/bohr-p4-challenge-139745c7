# 检索与定位协议

本文件固定 `scripts/sk_entry.py` 的执行顺序与判定口径。任何实现改动都必须保持这些性质：只用 Python 标准库、不联网、不用时间戳/随机数、同一输入产生同一输出。

## 1. 顺序

1. 读取 `case.json`（必需）与 `task.md`（可选），得到 `case_id`、查询、过滤条件、`output_contract`、必需字段。
2. 递归遍历输入目录：按相对路径排序，不跟随符号链接，跳过 `__pycache__`、`.git` 等目录；`case.json` 与根目录 `task.md` 不作为检索材料。
3. 逐文件抽取文本，记录字节数、sha256、抽取器、编码、置信度、是否截断；二进制、空文件、未知扩展名进入 `skipped`。
4. 按内容 sha256 去重：后出现的完整副本标记 `duplicate_of`，不参与排名，但保留在索引清单中。
5. 切段并识别章节，生成 passage：文件、`section`、`section_path`、行号/页码、字符偏移、文本。
6. 对每个查询执行文件级硬过滤 → BM25-lite 打分 → 章节过滤 → 取 top-k → 生成 hit 与 evidence。
7. 计算 coverage 与 misses，判定 `status`，写 `result.json`、`index.json`、`run-review.md` 草稿。

## 2. 打分公式

对查询词项 `t` 与段落 `d`：

```text
idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
bm25(t,d) = idf(t) * tf(t,d) * (k1 + 1) / (tf(t,d) + k1 * (1 - b + b * |d| / avgdl))
score(d) = Σ_t w(t) * bm25(t,d) + boosts
```

- `N` 为参与排名的段落数，`|d|` 为段落词项数，`avgdl` 为平均长度。
- `w(t)`：中文二元组 1.8，中文单字 0.45，ASCII 词 1.0，纯数字 0.8。
- boosts：精确短语 +1.5；章节标题命中每个词 +0.8（最多 3 个）；文件名命中每个词 +0.3（最多 3 个）。
- `score < min_score`（默认 0.45）不返回。
- 排序键：`(-score, file, line_start, char_start, p_id)`，保证同分稳定。

参数位于 `assets/policy.json`，脚本加载失败时回退到内置默认值。

## 3. 章节识别

一行被判定为标题需满足长度限制，并匹配以下任一模式：

- Markdown：`#` 到 `######`。
- 编号：`1`、`1.2`、`1.2.3`，可带 `Section` 前缀。
- 中文序号：`一、`、`二.` 等。
- 常见章节名：Abstract、Introduction、Methods、Results、Discussion、Conclusion、References、摘要、引言、方法、结果、讨论、结论、参考文献、附录。

标题行本身不生成 passage，只更新 `section` 与 `section_path`；后续段落继承最近的标题。PDF 文本不伪造标题与行号，定位落在页码与字符偏移上。

## 4. 状态判定优先级

1. `case.json` 损坏 → `failed`。
2. `case.json` 缺失、输入路径不存在、无可读文本 → `insufficient_input`。
3. 无查询 → `partial`。
4. 任一查询无命中，或任一过滤条件无匹配，或存在读取/抽取失败 → `partial`。
5. 其余 → `success`。

`partial` 与 `insufficient_input` 必须在 `uncertainty` 与 `misses` 中说明原因，不能当作 `success` 交付。
