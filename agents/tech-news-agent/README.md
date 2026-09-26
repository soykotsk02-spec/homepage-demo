# 独立科技简报 Agent

这是网站仓库中的公开源码副本。程序在用户自己的电脑上运行：采集科技新闻、读取明确允许使用的研究文档、调用模型生成带出处的关联分析，再排版并发送邮件。网页通过 HTTPS 下发固定任务，电脑不开放入站端口。

这里不包含真实账号配置、邮箱连接记录、个人文档、报告、运行日志或发信凭据。本源码副本不会自行迁移电脑上的运行目录或已安装任务；若要从本副本运行，请先单独完成配置。

## 组成

| 文件 | 用途 |
| --- | --- |
| `agent.py` | 顺序运行采集、资料读取、分析、排版及可选发送，保存防重复状态 |
| `collector.py` | 采集国内外公开科技 RSS，优先近三天的新闻 |
| `local_library.py` | 每次重新读取允许使用的 DOCX 正文，保留文件哈希与段落定位 |
| `analyzer.py` | 通过独立 Codex CLI 进程生成中文分析，并校验来源和逐字引文 |
| `public_news.py` | 仅使用本次公开新闻生成访客简报，校验单个收件邮箱，不读取个人资料 |
| `article_delivery.py` | 按邮箱记录文章指纹，回填历史发信并防止后续重复投递 |
| `gmail_delivery.py` | 使用已连接的 Gmail 工具，核对实际调用结果后记录发送状态 |
| `web_worker.py` | 向网站轮询任务、回传状态和脱敏报告，同步电脑生成的日常简报 |
| `tests/` | 使用模拟服务和临时样例文件的测试，不发送真实邮件 |

## 配置

需要 Python 3.12 或更新版本、PowerShell 7，以及已登录并能使用所需模型和 Gmail 连接的 Codex CLI。运行时需要联网。

先在本目录复制示例配置：

```powershell
Copy-Item config.example.json config.json
Copy-Item local-sources.example.json local-sources.json
# 需要网页启动功能时再复制：
Copy-Item web-worker-config.example.json web-worker-config.json
```

- 在 `config.json` 填写本机 Python 和 Codex 可执行文件位置，设置实际发件账号和收件邮箱；`sender@example.com`、`you@example.com` 都是占位示例。`networkProxy` 留空表示不另外指定代理。
- 在 `local-sources.json` 列出你允许模型读取的研究 DOCX。示例使用相对于配置文件的 `private-materials/` 位置；该目录已加入忽略规则。材料不能读取或没有有效段落时，程序停止，不用兴趣标签替代正文。
- `profile-context.json` 只有通用主题偏好，可以修改；它不声称你具备某种技能。
- 网页功能还需对应后端服务。设置 `web-worker-config.json` 中的 HTTPS 接口、worker 标识和随机令牌；令牌必须与网站后端配置一致，不能放进前端代码。
- Windows 登录启动的网页连接程序优先使用同目录 `pythonw.exe` 在后台运行，避免依赖隐藏终端窗口；执行单次 Agent 时仍使用 `python.exe` 并隐藏窗口。控制台不可用时，安全诊断写入本机 `data/web-worker/diagnostics.log.jsonl`。
- `approvalMode` 示例为 `never`。如需使用当前环境支持的自动审核，应在明确同意后改为 `auto_review`；这个设置仅传给程序自己的发信子进程。审核或授权失败时程序停止，不把失败当作已发送。

实际配置、私有材料、运行记录和生成报告都在 `.gitignore` 中。不要提交这些文件，也不要把真实密码、令牌或个人文档加入示例配置。

公开网页任务可指定 5–20 条新闻与最多 200 字的关键词（空格或逗号分隔，任一词匹配）。关键词只筛选本次抓取的公开新闻，不提供执行指令。程序要求近 72 小时、国内与国际兼有、白名单媒体或机构来源，并排除发给同一邮箱的既有新闻；候选不足就停止，不发送缩水或重复的简报。站主日常任务仍为 10–15 条。

文章去重索引保存在本机数据目录，按规范邮箱、规范链接和标题指纹长期记录，并迁移历史已发送记录。它补充原来的任务防重台账，不替换或清空旧台账。未知的发送结果会保留占用，需核实后再处理，不能删除索引来强制重发。

## 运行与定时

```powershell
# 本地配置检查，不发信
./run-agent.ps1 -Mode check

# 采集、读取研究资料、分析和排版，不发信
./run-agent.ps1 -Mode preview

# 明确执行一次真实发信；同一演示编号用于防重复
./run-agent.ps1 -Mode demo -DemoId first-demo

# 安装每天北京时间 09:00 的 Windows 任务
./install-schedule.ps1 -Mode Daily

# 安排一次三分钟后的演示任务
./install-schedule.ps1 -Mode Demo -DelayMinutes 3
```

也可以双击 `preview.cmd` 运行预览。电脑需要开机、联网且 Windows 用户处于登录状态。安装器会检查配置；每日任务与演示任务分别名为 `TechNewsAgent-Daily`、`TechNewsAgent-Demo`。移除它们使用 `./install-schedule.ps1 -Mode Remove`。

## 网页启动

```powershell
# 仅检查 worker 本地配置
./run-web-worker.ps1 -Check

# 轮询一次；若领到任务，等待该任务结束
./run-web-worker.ps1 -Once

# 安装登录后隐藏启动的常驻 worker，并立即启动
./install-web-worker.ps1 -StartNow
```

worker 接受 UUID 任务编号和固定模式。管理员的 `preview` / `send` 使用本机原有收件配置和研究资料，网页不能覆盖私人模式的收件人。公开访客的 `public-send` 额外接受一个经过严格校验的邮箱，只运行 `--public-only --recipient` 对应的公开新闻流程。worker 不接受网页传来的文件路径、任意命令或自由提示词。

公开流程不读取 `profile-context.json` 或 `local-sources.json`，不调用本地资料读取器，不复用私人分析，也不改写 `config.json` 或“最新简报.html”。它重新抓取公开 RSS，以一般科技读者为对象独立分析，并保存独立的 `public-analysis.json`；结果不会同步成私人日常报告。访客邮箱只在这一次调用的内存配置中替换，公开发送的防重复键与原有每日任务分开。每天 09:00 的私人任务仍使用原有收件人。

任务执行期间持续回传状态，结束后回传字段白名单内的报告。私人报告保留研究标题和短引文，但省略完整本地路径、邮箱、凭据和原始资料上下文，只能由已登录管理员读取。公开接口只提供访客任务状态，不返回私人报告；公开运行不会进入读取本地资料阶段。网站后端还需提供访问频率控制和 worker 认证。

worker 通过本地锁和持久记录保护重复任务。网络失败只重试结果回传，不重新执行同一个任务；发送中断或结果不确定时，需要核查本地记录。它还会同步已完成的日常报告，按运行编号和内容哈希去重。网页 worker 的任务名为 `TechNewsAgent-WebWorker`；使用 `./install-web-worker.ps1 -Remove` 可移除登录启动任务。

## 来源与运行证据

新闻摘要以本次 RSS 标题和摘要为依据，保留原文链接，不声称已核对全文。研究文档中的观点只作为分析框架，不作为当天新闻的新事实。

私人日常简报每期 10–15 条，公开简报按访客选择精确生成 5–20 条；都要求近 72 小时、国内和国际科技新闻兼有，各条附中文摘要与原文链接。地域按报道的主要事件或主体判断，不能仅凭媒体语言归类。有效候选不足所需数量时明确失败，停止生成和发送，不编造新闻补数；数量不足或单一区域的新分析也会被校验拒绝。历史较短报告仍能读取，不改动已发送记录。

每次运行的本地 `data/runs/<runId>/` 保存输入、研究材料读取证据、分析、报告及阶段状态。短引文必须真实出现在本次读取的段落中；找不到可靠关联时要如实说明。完整文档路径仅保存在本机证据文件中。

`data/deliveries/` 保存防重复台账。只有实际 Gmail 工具返回成功消息编号才能记为发送成功，这不表示收件人已读。遇到 `sending` 或 `uncertain` 时，先核对已发箱与收件箱，不要删除台账后重跑。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试使用临时目录、虚构邮箱、构造的路径和模拟消息编号检查隐私过滤、真实段落定位、头注入防护、调用回执、防重复与断网恢复，不需要真实账号配置或个人研究文档。
