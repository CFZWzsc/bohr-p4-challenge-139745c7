# 失败模式与处置

本 Skill 的原则：**未完成不能写成完成，缺少证据不能补造证据**。下表列出可预期失败及其在 `result.json` 中的落点。

## 1. 输入层

| 现象 | 检测 | 落点 | status |
|-|-|-|-|
| 输入目录为空 | 遍历结果为零 | `misses: no_indexable_files` | `insufficient_input` |
| 目录不存在 | 路径判断失败 | `errors` + `misses: no_indexable_files` | `insufficient_input` |
| `case.json` 缺失 | 文件不存在 | `misses: case_file_missing` | `insufficient_input` |
| `case.json` JSON 损坏/非对象 | 解析异常 | `errors` + `status_reason` | `failed` |
| 无查询字段 | 查询列表为空 | `misses: no_query` | `partial` |
| 路径失效、符号链接 | 不跟随链接 | `skipped: symlink_not_followed` | 不降级 |

## 2. 文件层

| 现象 | 检测 | 落点 | status |
|-|-|-|-|
| 无读取权限 | `OSError` | `skipped: unreadable` / `stat_error` | `partial` |
| 空文件 | `st_size == 0` | `skipped: empty_file` | 不降级 |
| 二进制内容 | NUL 字节或控制字符比例过高 | `skipped: binary_content` | 不降级 |
| 不支持扩展名 | 不在文本/PDF 白名单 | `skipped: unsupported_extension` | 不降级 |
| 超大文件 | 超过字节上限 | `truncated=true` + `uncertainty` | `partial` |
| 编码异常 | UTF-8/GB18030/Big5 均失败 | `latin-1` 或替换字符，`confidence=low` | `partial` |
| 文件名重复但内容不同 | 按 sha256 区分 | 都进入索引，各自参与排名 | 不降级 |
| 内容重复 | sha256 相同 | `duplicates`，只排名首份 | 不降级 |
| 超过文件数上限 | 排序后截断 | `skipped: file_cap_reached` | `partial` |

## 3. PDF 层

| 现象 | 检测 | 落点 | status |
|-|-|-|-|
| 无文本层（扫描件） | 解压后无 `Tj`/`TJ` 字符串 | `skipped: no_text_layer` | `partial` |
| 内容流解压失败 | `zlib.error` | 尝试原始流，仍无文本则跳过 | `partial` |
| 双栏/跨页 | 不解析版面顺序 | 命中 `page` 近似，`confidence=low` | `partial` |
| 加密 PDF | 无法解压 | 跳过，不输出伪造文本 | `partial` |

## 4. 检索与过滤层

| 现象 | 检测 | 落点 | status |
|-|-|-|-|
| 查询无有效词项 | 分词后为空 | `misses: no_passage_above_threshold` | `partial` |
| 所有段落低于阈值 | 最高分 < `min_score` | `hits: []` + `misses` | `partial` |
| 文件级过滤后为空 | 过滤后文档数 = 0 | `misses: filters_excluded_all_files` / `filter_unmatched` | `partial` |
| 章节过滤后为空 | 过滤后命中数 = 0 | `misses: section_filter_excluded_all_hits` | `partial` |
| 过滤键未知 | 不在白名单 | 按文件路径/元数据/正文做最佳尽力子串匹配，`mode=best_effort_substring` | 视结果 |
| 查询词只有停用词 | 分词为空 | `misses: no_passage_above_threshold` | `partial` |

过滤条件永远不放宽。不能因为“放宽后能命中”而修改用户条件。

## 5. 运行层

| 现象 | 处置 |
|-|-|
| 输出目录不存在 | 自动创建；无法创建时仍尝试写工作目录并返回 0 |
| 工作目录不可写 | `result.json` 照常输出，`errors` 记录工作产物失败 |
| 未捕获异常 | 兜底写出完整契约键的 `result.json`，`status=failed`，退出码 0 |
| 重复运行 | 结果字节级一致；不覆盖已存在的人工 `run-review.md` |
| 参数错误 | 兜底写出 `failed` 结果，退出码 0 |

## 6. 诚实性约束

- `success` 只在所有查询有命中、所有过滤满足、没有索引失败时使用。
- 空结果必须带原因，不能返回空数组却不解释。
- PDF 页码、低置信编码、词法召回漏召回必须写入 `uncertainty`。
- 不生成平台分数、不复述隐藏案例、不把示例输出冒充正式结果。
