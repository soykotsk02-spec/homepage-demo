# 常浩东的个人主页

个人主页与私人 Agent 控制台。主页继续公开展示学习内容；`/agents` 提供登录入口。登录后可以查看最新科技简报、本地资料关联、电脑连接状态和运行历史，或请求电脑执行一次任务。

## 运行方式

网页只负责提出固定任务和展示结果。独立 Python agent 在个人电脑上读取本地资料、调用模型、排版及发信。电脑通过 HTTPS 主动轮询任务，不开放入站端口。电脑开机、联网且连接程序运行时，任务通常在 30 秒内被接收。每天北京时间 09:00 的 Windows 定时任务继续使用同一个 agent。

“生成预览”不发信；“运行并发邮件”发送至电脑端预先配置的本人邮箱。网页不能指定其他收件人、文件路径、自由提示词或命令。

## 网站文件

- `index.html`：个人主页，新增 Agent 控制台入口。
- `agents.html`、`agents.js`、`agents.css`：中文响应式控制台。
- `api/agent.js`、`server/agent-service.mjs`：登录、持久任务队列和私有报告接口。
- `scripts/build.mjs`：只将四个明确列出的前端文件复制到公开目录。
- `tests/`：鉴权、任务状态、防重复执行和报告数据边界测试。

本仓库不保存电脑端配置、登录密钥、源文档、真实报告或运行日志。报告经字段筛选后存入私有 Redis，仅由鉴权接口返回给已登录用户。原始正文证据、完整本地路径和 Gmail 回执留在电脑中。

## Vercel 配置

使用 Node.js 22，构建命令 `npm run build`，输出目录 `public`。Vercel 同时部署根目录 `api/agent.js` 为服务端函数。无需在 Vercel 运行模型或安装本地 agent。

服务器需要以下环境变量，全部保留在服务器设置中，不添加前端前缀、不提交到仓库：

| 名称 | 用途 |
| --- | --- |
| `SITE_ORIGIN` | 本站 HTTPS 域名，用于验证网页操作来源 |
| `AGENT_ADMIN_PASSWORD` | 控制台的强随机登录口令 |
| `AGENT_SESSION_SECRET` | 签名短期登录会话 |
| `AGENT_WORKER_TOKEN` | 仅电脑连接程序使用的独立凭据 |
| `AGENT_WORKER_ID` | 绑定的电脑连接程序标识 |
| `UPSTASH_REDIS_REST_URL` | 私有 Redis 的 REST 地址 |
| `UPSTASH_REDIS_REST_TOKEN` | 仅服务器使用的 Redis 凭据 |

也支持 Vercel 集成提供的 `KV_REST_API_URL` / `KV_REST_API_TOKEN`。应使用持久数据库，不使用会在几天后到期的临时试用数据库。没有配置时接口明确返回“尚未配置”，不伪造在线状态或执行结果。

## 电脑端

完整电脑端源码位于 [`agents/tech-news-agent/`](agents/tech-news-agent/)，包含新闻抓取、本地资料读取、分析排版、邮件发送、每日定时启动和网页任务连接程序。该目录附带配置模板和安装说明。

`web_worker.py` 使用本机 `web-worker-config.json` 中的 HTTPS 接口地址、workerId 和 workerToken。真实配置、邮箱连接、个人资料和运行记录只留在电脑上；仓库只保存程序及示例配置。

任务以唯一编号存入电脑台账。同一编号重复接收只回传已有状态，不重复发送邮件；无法确认中断结果时保留核查状态。同步报告失败与发信分开处理，不能因为网页暂时断网而再次发信。

## 检查

```text
npm test
npm run build
```

上线验收需另外完成：匿名请求被拒绝、登录后电脑在线、从网页发起预览、电脑实际运行、网页出现对应新结果，以及手机布局检查。通过本机测试不能代替线上验收。
