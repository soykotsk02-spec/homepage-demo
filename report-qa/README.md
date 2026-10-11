# 银行年报问答研究台

方向 A 作业：12 家上市银行的 **2025 年完整年报**，公开来源为巨潮资讯；全文与表格抽取、真实 BGE 向量 + BM25 检索、带出处的摘录式问答、10 题固定评测。

网站入口：[/report-qa/](https://homepage-demo1111.vercel.app/report-qa/)。该目录独立于现有科技简报 Agent，不读取本地私有学习资料、不调用邮件服务，也不使用其账号凭据。

主页顶部的「财报问答」可直接进入研究台。页面「附件下载」区提供[一页结论 PDF](https://homepage-demo1111.vercel.app/report-qa/downloads/conclusion.pdf)、[问答与出处截图](https://homepage-demo1111.vercel.app/report-qa/downloads/answer.jpg)、[表格与页码截图](https://homepage-demo1111.vercel.app/report-qa/downloads/table.jpg)及[逐题评测记录](https://homepage-demo1111.vercel.app/report-qa/downloads/evaluation-record.md)。附件无需等待向量模型加载即可下载；公开构建仅复制 `public-files.json` 中的文件。

## 数据与出处

报告清单为 `sources.json`：招商、平安、宁波、江苏、南京、杭州、成都、苏州、长沙、青岛、厦门、上海，共 **12 家，3,265 个物理 PDF 页**。所有来源均为 `static.cninfo.com.cn` 官方全文 PDF，排除摘要、业绩快报和英文版。下载脚本验证 PDF 文件头并记录 SHA-256；来源发现脚本可重新查询巨潮公告。

原 PDF 不重复上传到代码仓库。读者可以点击回答旁的 `PDF 第 N 页` 访问官方原文；`#page=N` 是从 1 开始的物理页，不是印刷页码。原文件、页面文字和完整二维表格保存在执行机器的 `artifacts/` 或自行指定目录。公开检索数据只来自这些公开年报。

只下载本次使用的财报，不重跑后续流程：

```sh
cd report-qa
python scripts/download_reports.py --sources sources.json --workdir artifacts
```

脚本读取固定的官方来源清单，下载 12 份全文到已忽略的 `artifacts/`，记录文件大小和 SHA-256。仓库中的 `conclusion.pdf` 是一页作业结论，不是财报原文件。

## 提交与复盘约定

- 2026-10-11 补充复核保留全部 30 组召回与实际答案，分别检查证据是否充分、展示答案是否完整正确。三种方法各 10 题；Q09/Q10 各逐家核对 12 家公司。完整协议见 [评价口径](reports/evaluation-protocol.md)，逐项结果见 [复核数据](reports/source-answer-review.json)。由 Agent 辅助核对原文，未经独立人工双评。
- 当前财报问答不需要 API key、密码或个人登录账号。以后接入外部模型时，key 只放本机 `.env`，由后端进程读取；不得放入源码、前端资源、日志、截图或提交附件。根目录与本目录的 `.gitignore` 均忽略 `.env` 和 `.env.*`。
- 截图只保留页面内容，避免包含浏览器账号区、邮箱、个人路径和登录凭据。复现示例使用相对路径；公开仓库及官方财报链接用于追溯来源。
- 原始财报与中间抽取文件留在 `artifacts/`，不提交。结论 PDF、问答截图、下载脚本、公开检索索引与评测记录属于交付物。

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
# 重新核对原 PDF 与各方法实际答案，更新 source-answer-review.json
node scripts/check-recorded-answers.mjs
python scripts/make_deliverables.py --validate-only
python scripts/make_deliverables.py
python scripts/package-index.py
```

`questions.json` 是检索前固定的 10 问金标准；`scripts/make_questions.py` 记录标准答案和原页。该文件不传入检索器或答案生成函数。两道全景题逐家查询全部 12 家银行。重新评测将清除人工判定，必须再次核验；不能沿用另一版本索引的正确率。

本次交付：[一页结论 PDF](reports/conclusion.pdf)、[逐题评测记录与实际答案快照](reports/evaluation-record.md)、[证据/答案分评](reports/source-answer-review.json)。三种方法都已复核。旧 [manual-review.json](reports/manual-review.json) 保留为历史，其中“correct”表示候选证据充分，不能当作答案正确率。换语料、排序或回答模块后必须重新审阅。`make_deliverables.py` 验证冻结评测与回答模块的 SHA-256、公司/方法覆盖、实际块引用和官方来源；`record_review.py` 仅保留历史格式。

补交代码目录：[GitHub / report-qa](https://github.com/soykotsk02-spec/homepage-demo/tree/main/report-qa)。BM25 规范页命中 **74.2%** 高于融合 **60.0%** 的原结果保留。Q09/Q10 原题未明定集团口径，本次比较采用集团/整体口径；合法母行口径保留敏感性，不能直接当作编造。复核不把金标准填入实际回答。

页面截图：[工作台](reports/screenshots/workbench.jpg)、[问答与出处](reports/screenshots/answer.jpg)、[三种方法分评](reports/screenshots/evaluation.jpg)、[全景逐公司核验](reports/screenshots/company-review.jpg)、[表格与页码](reports/screenshots/table.jpg)。交互和部署验证范围见 [运行验证记录](reports/acceptance.md)。

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

`site/data/evaluation.json` 保存每问、每种方法的实际块 ID、分数、原文、回答和自动指标。旧字段 `evidenceRecall` 明确显示为“规范页命中”，`displayedFactCoverage` 显示为“数字字符串覆盖”，均不作答案准确率。其他原页的有效重复披露可以支持正确回答。`site/data/source-answer-review.json` 单独保存证据与答案复核，不作为检索输入；版本哈希不符时页面不显示旧标签。完整判定检查数值、年度、单位、主体、脚注及实际展示内容。

模型与库的协议、许可、固定校验和见 `lib/README.md`、`lib/model-manifest.json` 及 `site/vendor/`。报告著作权归原披露主体，项目保留官方原文链接和数据来源。
