# 快速开始（示例）

以下命令仅演示接口，输出是**示例**，不是任何平台案例的正式结果。

## 1. 运行示例

在提交目录（含 `scripts/`、`assets/`、`examples/`）下执行：

```bash
python3 scripts/sk_entry.py \
  --input examples/demo-case \
  --work /tmp/demo-work \
  --out /tmp/demo-out
```

Windows PowerShell 可用：

```powershell
python scripts/sk_entry.py --input examples/demo-case --work .\demo-work --out .\demo-out
```

退出码应为 0。生成：

```text
/tmp/demo-out/result.json
/tmp/demo-out/run-review.md          # 草稿，需人工核对后改写
/tmp/demo-work/index.json            # 索引快照
/tmp/demo-work/run-review.md         # 草稿副本
```

## 2. 示例输入

- `01_project/2021-buffer-notes.md`：含 `Materials`、`Buffer recipe` 章节与 2021 配方。
- `01_project/2022-revision.md`：2022 修订版配方。
- `02_protocols/protocol-A.txt`：流式细胞仪校准流程。
- `03_data/measurements.csv`：两年测量值。
- `04_paper/manuscript.html`：带 `Methods`、`Results` 章节的稿件。
- `case.json`：两个查询、年份/章节过滤、9 个 `output_contract` 字段。

示例中的第二个查询使用 `section: "Appendix Z"` 这一不存在的章节，因此预期**无命中**并给出 `section_filter_excluded_all_hits`；这是刻意展示“过滤无匹配不编造”的行为。

## 3. 示例输出要点（示意）

```json
{
  "case_id": "demo-buffer-recipe",
  "status": "partial",
  "hits": [
    {
      "hit_id": "q1-h1",
      "file": "01_project/2021-buffer-notes.md",
      "section": "Buffer recipe",
      "locator": {"kind": "line", "line_start": 10, "line_end": 11},
      "quote": "The final buffer recipe was 50 mM Tris-HCl, 150 mM NaCl, 1 mM EDTA at pH 7.4."
    }
  ],
  "misses": [
    {"query_id": "q2", "reason": "section_filter_excluded_all_hits"}
  ]
}
```

行号与分数以实际运行结果为准；上面只是字段示意。

## 4. 核对

1. 打开 `/tmp/demo-out/result.json`，确认 `results`、`citations`、`sources`、`missed`、`index`、`notes`、`summary`、`status` 都存在。
2. 打开 `hits[0]` 指向的 Markdown 文件，跳到 `line_start`，逐字比较 `quote`。
3. 打开 `/tmp/demo-work/index.json`，核对 5 个材料文件与 `files_skipped` 计数。
4. 用相同命令再跑一次，确认 `result.json` 内容一致。

## 5. 常见问题

| 现象 | 原因 | 处理 |
|-|-|-|
| `status=insufficient_input` | 输入目录没有可读文本或缺少 `case.json` | 检查 `--input` 是否指向案例根目录 |
| 某个查询 `hits` 为空 | 过滤条件无匹配或分数低于阈值 | 查看 `misses` 的 `reason`，不要放宽过滤条件 |
| PDF 命中 `page` 为空 | PDF 没有可解析的页面对象 | 使用 `char_start/char_end` 复核，并在复核记录中说明 |
| `skipped` 含 `no_text_layer` | 扫描件无文本层 | 需要 OCR，本 Skill 不伪造文本 |
| `run-review.md` 未更新 | 已存在人工版本，脚本不覆盖 | 手动编辑或删除后重跑 |
