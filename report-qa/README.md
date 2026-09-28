# 银行年报问答研究台

方向 A 作业：12 家上市银行的 **2025 年完整年报**，公开来源为巨潮资讯；全文与表格抽取、真实 BGE 向量 + BM25 检索、带出处的摘录式问答、10 题固定评测。

网站入口：[/report-qa/](https://homepage-demo1111.vercel.app/report-qa/)。该目录独立于现有科技简报 Agent，不读取本地私有学习资料、不调用邮件服务，也不使用其账号凭据。

## 数据与出处

报告清单为 `sources.json`：招商、平安、宁波、江苏、南京、杭州、成都、苏州、长沙、青岛、厦门、上海，共 **12 家，3,265 个物理 PDF 页**。所有来源均为 `static.cninfo.com.cn` 官方全文 PDF，排除摘要、业绩快报和英文版。下载脚本验证 PDF 文件头并记录 SHA-256；来源发现脚本可重新查询巨潮公告。

原 PDF 不重复上传到代码仓库。读者可以点击回答旁的 `PDF 第 N 页` 访问官方原文；`#page=N` 是从 1 开始的物理页，不是印刷页码。原文件、页面文字和完整二维表格保存在执行机器的 `artifacts/` 或自行指定目录。公开检索数据只来自这些公开年报。

## 如何运行网站

根项目的 `npm run build` 会从仓库自带的 130 个分片恢复经过 SHA-256 校验的模型、浏览器运行文件、索引和评测记录，再把白名单文件复制到 `public/`。完整仓库可离线构建；已在禁止所有网络请求的干净副本中验证。部署时不重新抽取 PDF、不运行 Python、不重新编码一万多条向量。Node.js 20+ 可构建；现有 Vercel 项目使用 Node.js 22。

```sh
# 在仓库根目录
npm run build
python -m http.server 8873 --directory public
# 打开 http://localhost:8873/report-qa/
```

查询在浏览器 Web Worker 内完成，不需要付费 API、API key 或服务器数据库。首次访问需要下载全文索引、向量、约 24 MB 的量化模型及 WASM 运行文件；加载期间先开放 BM25。模型失败时界面明确标注仅可用关键词检索，不把降级结果标为混合检索。

## 从原报告重建

```sh
cd report-qa
python -m pip install -r requirements.txt
python scripts/discover_reports.py --year 2025 --as-of 2026-09-28
python scripts/download_reports.py --workdir artifacts
python scripts/extract_reports.py --workdir artifacts --workers 4 --force
pnpm install --ignore-scripts --lockfile-dir lib
node lib/download-model.mjs
node scripts/embed.mjs
node scripts/build-browser.mjs
node scripts/evaluate.mjs
# 重新人工核验，再生成报告及更新部署分片
python scripts/make_deliverables.py
python scripts/package-index.py
```

`questions.json` 是检索前固定的 10 问金标准；`scripts/make_questions.py` 记录标准答案和原页。该文件不传入检索器或答案生成函数。两道全景题逐家查询全部 12 家银行。重新评测将清除人工判定，必须再次核验；不能沿用另一版本索引的正确率。

本次交付：[一页结论 PDF](reports/conclusion.pdf)、[逐题评测记录](reports/evaluation-record.md)、[人工复核说明](reports/manual-review.json)。默认混合检索的 10 题人工复核为 7 题证据充分、3 题部分回答；BM25 与纯向量基线仅报告自动指标。`scripts/record_review.py` 只记录本次冻结版本的人工判定，不是通用自动评分器；换语料或排序后必须重新审阅。

## 提取与切块

- `scripts/table_extract.py` 先保留原生网格；针对只画横线、灰色表头、仅突出本年列的表，利用真实 PDF 行列坐标恢复完整单元格。数字按完整词分配，避免把 `20,759,566` 切开。
- 保存二维 `rawRows`、`headers`、`rows`、边界框、提取方法、质量标记。多层表头、重复表头和空单元格保留，不推断缺失金额。表上方单位另存 `unitContext`。
- 每块带 `company / code / reportYear / section / pdfPage / sourceUrl / id`；表块附原表 ID 和起止行。正文约 340 字、重叠 35 字；表块重复列头，尽量保持整行。编码超过 512 token 时完整分段编码后加权聚合，不静默丢掉尾部。
- 章节来源为经过核查的 PDF 目录和真实起始页；原有书签存在偏页、目录误识别及附录跳转，因此不直接盲信书签。
- 复杂跨页表未自动合并，无法确认的结构留质量标记。图片页不冒充已完成 OCR；低文本页写入报告清单。

## 真正的两路检索

语义路：`Xenova/bge-small-zh-v1.5`，固定 revision `75c43b069aac4d136ba6bc1122f995fedcfd2781`，ONNX q8、CLS pooling、L2 归一化、512 维，正文无查询前缀；问题加模型建议的中文查询前缀。Node 建库与网页查询使用同一模型和 tokenizer。

关键词路：中文二元组、英文词和完整数字构建真实倒排索引，BM25 `k1=1.2,b=0.75`。浏览器从已持久化文本重建关键词索引。语义路使用实际向量点积，不使用随机向量、哈希向量或预设答案。

混合排序：分别取两路前 50，以 RRF `k=60` 融合。单公司题显示 Top-10；全景题每家公司 Top-2，保留公司覆盖。页面可切换三路，并查看每块分数与排名。分数是排序依据，不是回答置信度。

## 回答和评测边界

本项目采用**实时摘录式问答**，不调用生成式大语言模型。每段回答取自本次实际检索原文，表格保持行列，引用指回公司、章节和物理页。它能辅助查数、定位描述和对比，但不会自动核实集团/母公司差异，也不代替跨页表格计算或因果推断。

`site/data/evaluation.json` 保存每问、每种方法的实际块 ID、分数、原文、回答和指标；其中规范原页证据召回、MRR 和关键事实覆盖分别统计。其他页重复披露可能使回答正确而规范原页召回失败；关键数字出现也不等同于口径正确。因此逐题结论须单独人工核验。详细结论见网页的一页结论及本次交付文档。

模型与库的协议、许可、固定校验和见 `lib/README.md`、`lib/model-manifest.json` 及 `site/vendor/`。报告著作权归原披露主体，项目保留官方原文链接和数据来源。
