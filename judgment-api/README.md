# 民事一审模拟判案 API

[网站项目页](https://homepage-demo1111.vercel.app/judgment-api/) · [GitHub 源码](https://github.com/soykotsk02-spec/homepage-demo/tree/main/judgment-api)

这是课程作业的本地检索与规则基线，提供两个 HTTP 接口和一个可见的本机运行页面。网站用于展示方法、成绩和复现步骤；推理在运行 Python 的电脑上进行。静态网站与 Vercel 不承载判案服务，也不保存课程判决书。

公开目录包含通用程序、虚构输入的测试、安装说明和配置模板，不包含真实 key、运行中的隧道网址、个人账号配置、课程语料、提取结果、索引或逐题评测编号。课程 JSONL 须从老师授权渠道自行取得；本仓库不提供下载，也不向外传输这些文书。

## 老师测试结果

以下为 **2026-10-10 15:55（北京时间）老师测试反馈**，是已完成的该次测试记录，不代表当前服务仍在线。

| 指标 | 结果 |
| --- | --- |
| 完成请求 | 10 / 10 |
| 案由准确率 | 1.00 |
| 裁判方向准确率 | 0.50 |
| 法律名称 F1 | 0.70 |
| 法条编号 F1 | 0.36 |
| 平均响应时间 | 约 1 秒 |
| 未带 key（老师检查） | HTTP 401 |

10/10 表示请求完成，不能解释为 10 题裁判全部正确。方向准确率 0.50 和法条编号 F1 0.36 表明，接口可用性优于实质判断能力。这里没有公开测试案情、逐题 ID、key 或含私密信息的反馈附件。

## 方法与已知限制

程序从授权文书中提取案由、引用法条、裁判方向和主文；只用“本院认为”之前的案情建立中文字符 TF-IDF 索引，并以 LinearSVC 预测方向。再将相似案件标签与新输入的请求、证据和金额规则结合，构造 `cause / laws / direction / verdict`。没有微调生成模型，也不调用外部模型 API。若没有安装 scikit-learn，会明确采用字符 BM25 回退；它不等同于本次老师测试使用的 TF-IDF/LinearSVC 配置。

主文不复制相似案件当事人、金额和正文；给付金额仅取自当前输入，无法确定时保留限制说明。法条是历史文书中的引用，未外部核验其现行效力。复杂请求、事实争议、利息计算、给付范围及支持比例可能出错；返回四项完整字段不等于判决正确。

另做过 **18 例本地留出评测**：训练前排除留出案及近重复，案由匹配 18/18、最终方向匹配 14/18，法条宏平均精确率 40.56%、召回率 55.83%；其中 6/18 例仍无法生成具体可执行的主文。参照标签由本地文书自动提取，并非独立人工金标准；一些输入开头已经包含案由，结果偏乐观。这与上表老师的 10 例测试具有不同样本与评分口径，不能混用。完整主文正确率没有得到独立评估，逐题留出记录不随仓库发布。

## 本机安装

建议使用 **Python 3.12（64 位）**。取得本仓库后进入 `judgment-api` 目录：

```text
python -m pip install --target .deps -r requirements-lock.txt
python configure.py
```

`requirements-lock.txt` 是本次 Windows / Python 3.12 环境的版本记录；`requirements.txt` 是最小直接依赖清单。依赖只安装到被忽略的 `.deps/`。如其他平台没有对应版本的包，可使用最小清单重新解析依赖，但应重新做评测，不沿用历史成绩。

配置助手在交互终端中询问已获授权的 `judgments.jsonl` 路径。专用 key 可隐藏输入，也可直接回车生成新的随机值；它只写入本机 `.env`，不会显示 key，也不会读取或上传 JSONL 正文。已有 `.env` 时不会覆盖。不要使用个人账号密码或第三方平台的 API key。

若手动配置，参考 `.env.example` 的两个字段即可。模板中的短占位 key 故意不能通过校验；不要直接当作有效配置。只有向老师指定的私密文本框提交时，才在本机打开 `.env` 复制本作业专用 key；不要放进代码、网页、截图或录屏。

Windows 的四个 `.cmd` 入口依次尝试 `JUDGE_PYTHON` 指定的完整解释器路径、项目 `.venv`、当前用户已有的 Codex Python、系统 `py -3` 和 `python`。没有写入任何特定用户名；也可以一直使用自己已配置好的 Python 命令运行下列步骤。

## 安装 Cloudflare Tunnel

按 [Cloudflare 官方下载说明](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/) 获取对应平台的 `cloudflared`。Windows 64 位官方可执行文件来自 [Cloudflare GitHub 发布页](https://github.com/cloudflare/cloudflared/releases/latest)，文件名为 `cloudflared-windows-amd64.exe`。

在本项目内创建 `bin/`，将下载的程序重命名并放在 `bin/cloudflared.exe`。当前管理脚本按这个位置启动；其他系统可把对应平台二进制放到同一路径并赋予执行权限，或在本机调整 `manage.py` 中该路径。`bin/` 被忽略，仓库不复制分发此程序。当前脚本使用临时 Quick Tunnel，不安装系统服务，不修改防火墙，也不需要把 Cloudflare 账号凭据填入源码。

## 启动、查看和停止

```text
python manage.py start
python manage.py status
python dashboard.py --open
```

也可在 Windows 双击 `启动服务.cmd`、`查看状态.cmd`、`打开运行页面.cmd`。首次启动需在本机读取授权语料并建立索引；启动指令完成不等于服务已就绪，应以状态页的实际健康检查为准。

- 判案 API：`http://127.0.0.1:8000`。Cloudflare 只代理此端口。
- 本机运行页面：`http://127.0.0.1:8001/`。页面使用 Host、Origin 和随机页面令牌检查，key 由本地后端读取，不发送给浏览器。
- 当前临时公网地址只保存在被忽略的 `runtime/status.json` 中，不写入网站或仓库。

本机页面可查看进程、健康状态和判案记录，按需检查公网连接，或输入**虚构新案情**进行本机测试。页面只请求固定的本机 API，不提供任意文件访问。它不是公开网站上的远程控制台；关闭页面窗口不会停止已有 API 和隧道。

完成老师验收后，停止本项目创建的服务：

```text
python manage.py stop
```

或双击 `停止服务.cmd`。启动锁避免重复运行，停止逻辑只清理自身创建的子进程。8000 被其他程序占用时不会停止该程序。新启动通常产生新的临时网址，应重新提交；关机、休眠或断网都会中断服务。Quick Tunnel 的用途和限制见 [Cloudflare 官方说明](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)。

## 接口契约

`GET /health` 无需 key，成功返回 HTTP 200 和 `{"ok":true}`。

`POST /judge` 使用请求头 `Authorization: Bearer <本作业专用key>`，请求体为：

```json
{"case_text":"原告依约交货，被告确认收货但尚未支付货款。原告请求支付已经明确的欠款。"}
```

成功返回且仅返回四个字段：

```json
{
  "cause": "案由字符串",
  "laws": ["《法律名称》第某条"],
  "direction": "支持、部分支持、驳回三者之一",
  "verdict": "模拟判决主文字符串"
}
```

上述响应只是字段示意，不是实际测试答案。服务不提供流式响应。

| 情况 | HTTP 状态 |
| --- | --- |
| 缺失或错误 Bearer key | 401 |
| 无效 JSON、案情不足 8 字或超过 50,000 字 | 400 |
| 请求体超过 1 MiB | 413 |
| 两个并发计算名额已占用 | 503 |
| 计算超过 90 秒 | 504 |
| 引擎错误或结果字段不完整 | 500 |
| `/docs`、`/.env`、语料或其他文件路径 | 404 |

错误不会用固定判决伪装成功。日志只保留完成时间、状态和耗时，不记录请求案情、Authorization、访问者身份、健康检查或 401。因此新增日志不能直接证明某位老师访问过。

## 本地验证与隐私范围

不需要课程 JSONL、真实 key 或外网，就能运行接口、规则和页面的隔离测试：

```text
python -m unittest discover -s tests -v
```

测试只使用虚构案情、临时端口和临时 key；不会调用已经运行的 API 或公网。`tests/test_dashboard.py` 会自动建立忽略的临时运行目录。

在本机获得授权语料后，可另行检查抽取与留出评测：

```text
python scripts/inspect_corpus.py path/to/judgments.jsonl
python tests/evaluate_engine.py path/to/judgments.jsonl --output artifacts/engine-evaluation.json
```

第一条生成本机聚合诊断，第二条会重新划分固定种子的留出集并训练。留出结果及终端输出含私有记录 ID，只能留在本机；不要上传 `artifacts/`、逐题 JSON 或输出截图。它不是老师测试的复跑，也不会自动得到同一成绩。

`.gitignore` 排除了 `.env`、依赖、二进制、运行状态、日志、文书、JSONL、索引、模型和逐题评测；唯一可公开的配置文件是无有效 key 的 `.env.example`。只通过本目录的源码清单发布，不把本机运行目录加入站点静态资源。
