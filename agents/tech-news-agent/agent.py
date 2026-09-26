"""Independent daily tech-news agent. Python standard library only."""
from __future__ import annotations

import argparse
import contextlib
import copy
from datetime import datetime, timezone, timedelta
from email.utils import make_msgid
import hashlib
import html
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
BEIJING = timezone(timedelta(hours=8))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


@contextlib.contextmanager
def exclusive_lock(path: Path):
    """OS lock is released on process exit, including a crash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("已有一个 agent 在运行，本次退出以避免重复发信。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_UN)


def log(run_dir: Path, phase: str, message: str):
    event = {"time": utc_now(), "phase": phase, "message": message}
    print(f"[{datetime.now(BEIJING):%H:%M:%S}] {message}", flush=True)
    with (run_dir / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def render_report(analysis: dict, recipient: str, *, public_only=False) -> tuple[str, str]:
    def e(value):
        return html.escape(str(value), quote=True)

    if not analysis.get("items") or len(analysis["items"]) > (20 if public_only else 15):
        raise ValueError("报告须包含 1–15 条新闻；新简报在来源充足时为 10–15 条。")
    if public_only:
        from public_news import validate_public_report
        validate_public_report(analysis)
    connections = [] if public_only else analysis.get("localConnections")
    if not isinstance(connections, list):
        raise ValueError("报告必须包含本次实际本地资料的关联分析字段。")
    rows, plain = [], [analysis["title"], analysis["generatedAt"], analysis["overview"]]
    for item in analysis["items"]:
        url = urlsplit(item["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError("原文链接必须是无凭据的 HTTPS 地址。")
        within_window = item["freshness"] == "recent" or (isinstance(item.get("ageHours"), (int, float)) and 0 <= item["ageHours"] <= 72)
        freshness = "近三天 / 72小时内" if within_window else "延伸阅读 / 非近三天"
        region = {"domestic": "国内", "international": "国际"}.get(item.get("region"), "")
        metadata = f"{item['source']} · {item['publishedAt']} · {region + ' · ' if region else ''}{freshness}"
        rows.append(f'''<tr><td style="padding:22px 26px;border-bottom:1px solid #e3eae3">
<p style="font-size:12px;color:#587163">{e(metadata)}</p>
<h2 style="font-size:20px;line-height:1.5;color:#214432">{e(item['title'])}</h2>
<p style="line-height:1.8">{e(item['summary'])}</p>
<p style="line-height:1.8;color:#58665d"><b>为什么值得看：</b>{e(item['whyItMatters'])}</p>
<a style="color:#226641" href="{e(item['url'])}">阅读原文 ↗</a></td></tr>''')
        plain.extend([item["title"], metadata,
                      item["summary"], item["whyItMatters"], item["url"], ""])
    local_rows = []
    if not public_only:
        plain.append("结合你的本地资料")
    if not public_only and not connections:
        local_rows.append('<p style="line-height:1.8">本次没有找到足够可靠的新闻与资料关联，具体范围与原因见下方说明。</p>')
        plain.append("本次没有找到足够可靠的新闻与资料关联，具体范围与原因见下方说明。")
    for connection in connections:
        local_rows.append(f'''<div data-item-id="{e(connection['itemId'])}" data-asset-id="{e(connection['assetId'])}" data-chunk-id="{e(connection['chunkId'])}" style="margin:18px 0;padding:18px;background:#fff;border:1px solid #d1dfce;border-left:4px solid #315f49">
<h3 style="margin:0 0 10px;font-size:18px;line-height:1.6;color:#214432">新闻：{e(connection['newsTitle'])}</h3>
<p style="font-size:13px;line-height:1.7;color:#587163"><b>你的资料：</b>{e(connection['sourceTitle'])} · {e(connection['anchor'])}</p>
<blockquote style="margin:14px 0;padding:12px 16px;background:#f6f8f1;line-height:1.8">“{e(connection['quote'])}”</blockquote>
<p style="line-height:1.8"><b>与这条新闻的联系：</b>{e(connection['relationship'])}</p>
<p style="line-height:1.8"><b>可以接着做：</b>{e(connection['nextStep'])}</p></div>''')
        plain.extend(["新闻：" + connection["newsTitle"],
                      "你的资料：" + connection["sourceTitle"] + " · " + connection["anchor"],
                      "原句：“" + connection["quote"] + "”",
                      "与这条新闻的联系：" + connection["relationship"],
                      "可以接着做：" + connection["nextStep"], ""])
    local_section = ""
    if not public_only:
        plain.extend(["资料范围与限制：" + analysis["localContext"], ""])
        local_section = f'''<tr><td style="padding:26px;background:#eaf2e6;border-top:3px solid #315f49"><h2 style="margin:0;font-size:23px;color:#214432">结合你的本地资料</h2>{''.join(local_rows)}<p style="font-size:13px;line-height:1.8;color:#5b6c61"><b>资料范围与限制：</b>{e(analysis['localContext'])}</p></td></tr>'''
    learning = "".join(f"<li>{e(x)}</li>" for x in analysis["learningSuggestions"])
    plain.extend(["今天动手做一件事", *analysis["learningSuggestions"],
                  analysis["sourceNote"], "运行编号：" + analysis["runId"]])
    body = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(analysis['title'])}</title></head>
<body style="margin:0;padding:20px 8px;background:#f1f5ef;color:#26392e;font-family:Arial,'Microsoft YaHei',sans-serif">
<table role="presentation" cellspacing="0" cellpadding="0" style="width:100%;max-width:680px;margin:auto;background:#fff;border:1px solid #dfe7dc">
<tr><td style="padding:28px 26px;background:#234b37;color:#fff"><p style="font-size:12px;letter-spacing:2px">DAILY TECH BRIEF</p><h1 style="font-size:27px;line-height:1.4">{e(analysis['title'])}</h1><p style="font-size:13px">{e(analysis['generatedAt'])} · 独立 agent 自动生成</p></td></tr>
<tr><td style="padding:24px 26px;line-height:1.8">{e(analysis['overview'])}</td></tr>
{''.join(rows)}
{local_section}
<tr><td style="padding:24px 26px;background:#f2f7ef"><h2 style="font-size:20px">今天动手做一件事</h2><ul style="padding-left:22px;line-height:1.8">{learning}</ul></td></tr>
<tr><td style="padding:22px 26px;font-size:12px;color:#657266;line-height:1.8">{e(analysis['sourceNote'])}<br>运行编号：{e(analysis['runId'])}<br>收件人：{e(recipient)}</td></tr></table></body></html>'''
    return body, "\n".join(plain)


def find_state(data_dir: Path, key: str):
    digest = hashlib.sha256(key.encode()).hexdigest()[:32]
    return data_dir / "deliveries" / (digest + ".json")


def run_pipeline(args, config: dict) -> int:
    from collector import collect
    from analyzer import analyze
    from gmail_delivery import GmailMailer, MailNotSent, MailOutcomeUnknown

    public_only = getattr(args, "public_only", False)
    item_count = getattr(args, "item_count", None)
    keywords = getattr(args, "keywords", "")
    override = getattr(args, "recipient", "")
    if (override or item_count is not None or keywords) and not public_only:
        raise ValueError("收件人覆盖仅适用于公开新闻模式。")
    if public_only:
        from public_news import normalize_recipient, normalize_preferences
        item_count, keywords = normalize_preferences(12 if item_count is None else item_count, keywords)
        if not override or not args.demo_id:
            raise ValueError("公开新闻模式须提供收件邮箱和唯一任务编号。")
        config = copy.deepcopy(config)
        config["mail"]["recipient"] = normalize_recipient(override)
    data_dir = ROOT / "data"
    date_bj = datetime.now(BEIJING).strftime("%Y-%m-%d")
    recipient = config["mail"]["recipient"]
    key = f"demo:{args.demo_id}:{recipient}" if args.demo_id else f"daily:{date_bj}:{recipient}"
    if public_only:
        key = "public:" + key
    state_path = find_state(data_dir, key)
    with exclusive_lock(data_dir / "agent.lock"):
        delivery = read_json(state_path) if args.send and state_path.exists() else None
        if delivery:
            receipt_path = data_dir / "runs" / delivery["runId"] / "delivery-receipt.json"
            if receipt_path.exists():
                receipt = read_json(receipt_path)
                if (receipt.get("status") == "sent" and receipt.get("key") == key
                        and receipt.get("messageId") == delivery.get("messageId")
                        and receipt.get("recipient") == recipient):
                    delivery.update(status="sent", sentAtUtc=receipt["acceptedAtUtc"])
                    write_json(state_path, delivery)
                    restored_status_path = receipt_path.parent / "status.json"
                    restored_status = read_json(restored_status_path) if restored_status_path.exists() else {"runId": delivery["runId"]}
                    restored_status.update(status="sent", sent=True, messageId=receipt["messageId"],
                                           mailAcceptedAtUtc=receipt["acceptedAtUtc"], recoveredFromReceiptAtUtc=utc_now())
                    write_json(restored_status_path, restored_status)
        if delivery and delivery["status"] == "sent":
            print("这个日期/演示编号已经发送成功，跳过重复发送。", flush=True)
            return 0
        if delivery and delivery["status"] in {"sending", "uncertain"}:
            print("上次发信结果尚待核实，已阻止自动重发。请检查已发箱及 delivery 记录。", flush=True)
            return 3
        if delivery:
            run_dir = data_dir / "runs" / delivery["runId"]
        else:
            run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            run_dir = data_dir / "runs" / run_id
            run_dir.mkdir(parents=True, exist_ok=False)
            if args.send:
                subject = (f"独立Agent演示 | {date_bj} | {args.demo_id}" if args.demo_id
                           else f"每日科技简报 | {date_bj}")
                if public_only:
                    subject = f"公开科技简报 | {date_bj} | {args.demo_id}"
                delivery = {"key": key, "status": "prepared", "runId": run_id,
                            "subject": subject, "recipient": recipient,
                            "messageId": make_msgid(domain="tech-news-agent.local"),
                            "createdAtUtc": utc_now()}
                write_json(state_path, delivery)
        run_id = run_dir.name
        status_path = run_dir / "status.json"
        status = read_json(status_path) if status_path.exists() else {
            "runId": run_id, "startedAtUtc": utc_now(), "pid": os.getpid(),
            "requestedTrigger": args.trigger, "mode": "send" if args.send else "preview",
            "demoId": args.demo_id, "sent": False, "recipientConfirmed": False}
        if public_only:
            status.update(publicOnly=True, audience="public", mode="public-send" if args.send else "public-preview")
        status["attemptStartedAtUtc"] = utc_now()
        write_json(status_path, status)
        log(run_dir, "start", f"独立 agent 已启动；运行编号 {run_id}。")
        try:
            if args.send:
                GmailMailer(config, run_dir).preflight()
            from article_delivery import ArticleLedger, selected_source_items, ensure_recent_articles
            article_ledger = ArticleLedger(data_dir)
            article_ledger.backfill(recipient)
            news_path = run_dir / "news.json"
            if news_path.exists() and (cached := read_json(news_path)).get("items"):
                news = cached
                log(run_dir, "collect", "恢复同一运行的已保存输入，保留原始抓取时间。")
            else:
                log(run_dir, "collect", "正在联网抓取国内外公开科技来源，优先采集近三天的新闻。")
                news = collect(run_dir)
            if not news.get("items"):
                from analyzer import InsufficientNewsError
                raise InsufficientNewsError("本次没有可用新闻，停止分析和发送。")
            news = {**news, "items": article_ledger.filter(recipient, news["items"], run_id)}
            if public_only:
                from public_news import filter_keywords
                news = filter_keywords(news, keywords)
                status.update(itemCount=item_count, keywords=keywords)
            write_json(run_dir / "eligible-news.json", news)
            status["collectedAtUtc"] = news["generatedAtUtc"]
            write_json(status_path, status)
            analysis_path = run_dir / ("public-analysis.json" if public_only else "analysis.json")
            if analysis_path.exists():
                analysis = read_json(analysis_path)
                if public_only:
                    from public_news import validate_public_report
                    validate_public_report(analysis)
                log(run_dir, "analyze", "恢复同一运行已经生成的分析。")
            else:
                if public_only:
                    status["localReadStatus"] = "not_used_public_news"
                    write_json(status_path, status)
                    log(run_dir, "analyze", "正在仅根据本次公开新闻，由独立模型生成访客简报。")
                else:
                    from local_library import collect_local_context

                    profile = read_json(ROOT / "profile-context.json")
                    status["localReadStatus"] = "reading"
                    write_json(status_path, status)
                    log(run_dir, "local-read", "正在重新读取允许使用的本地资料正文。")
                    local_context = collect_local_context(ROOT / "local-sources.json", run_dir, news)
                    if (not isinstance(local_context, dict) or not local_context.get("assets")
                            or not local_context.get("chunks")):
                        raise ValueError("本次未读取到可用本地资料，停止分析，不能用背景概括代替正文。")
                    profile["localLibrary"] = local_context
                    status.update(localReadStatus="completed", localReadAtUtc=utc_now(),
                                  localSourceCount=len(local_context["assets"]),
                                  localChunkCount=len(local_context["chunks"]))
                    write_json(status_path, status)
                    log(run_dir, "local-read", f"实际读取 {len(local_context['assets'])} 份本地资料、"
                        f"{len(local_context['chunks'])} 个正文段落，已提供给本次分析。")
                    log(run_dir, "analyze", "正在结合新闻与实际本地资料，由独立模型生成中文简报。")
                attempts_dir = run_dir / "analysis-attempts"
                attempts_dir.mkdir(exist_ok=True)
                attempt_dir = attempts_dir / f"{len(list(attempts_dir.iterdir())) + 1:03d}"
                attempt_dir.mkdir(exist_ok=False)
                if public_only:
                    from public_news import analyze_public
                    analysis = analyze_public(news, attempt_dir, config["codexExecutable"], item_count=item_count, keywords=keywords)
                else:
                    analysis = analyze(news, profile, attempt_dir, config["codexExecutable"])
                write_json(analysis_path, analysis)
            if public_only and (len(analysis["items"]) != item_count or {item.get("region") for item in analysis["items"]} != {"domestic", "international"}):
                from analyzer import InsufficientNewsError
                raise InsufficientNewsError("本次新闻数量或国内外覆盖不满足要求，停止发送。")
            selected_articles = selected_source_items(analysis, news)
            status["analyzedAtUtc"] = utc_now()
            log(run_dir, "render", "正在排版邮件，核对来源链接与发布时间。")
            body, plain = render_report(analysis, recipient, public_only=public_only)
            (run_dir / "report.html").write_text(body, encoding="utf-8")
            (run_dir / "report.txt").write_text(plain, encoding="utf-8")
            if not public_only:
                (ROOT / "最新简报.html").write_text(body, encoding="utf-8")
            status["renderedAtUtc"] = utc_now()
            status["status"] = "preview_ready"
            write_json(status_path, status)
            if not args.send:
                log(run_dir, "complete", "抓取、模型分析和排版完成。本次为预览，没有发送邮件。")
                return 0
            mailer = GmailMailer(config, run_dir)
            ensure_recent_articles(selected_articles)
            article_ledger.reserve(recipient, selected_articles, run_id)
            delivery["status"] = "sending"
            delivery["sendAttemptAtUtc"] = utc_now()
            write_json(state_path, delivery)  # crash after this is deliberately uncertain
            log(run_dir, "send", "正在由独立程序使用现有 Gmail 授权发送至配置的收件邮箱。")
            try:
                receipt = mailer.send(body, plain, delivery["subject"], delivery["messageId"])
            except MailNotSent:
                delivery["status"] = "not_sent"
                write_json(state_path, delivery)
                article_ledger.finish(recipient, run_id, "not_sent")
                raise
            except Exception:
                delivery["status"] = "uncertain"
                write_json(state_path, delivery)
                article_ledger.finish(recipient, run_id, "uncertain")
                raise
            delivery.update(status="sent", sentAtUtc=receipt["acceptedAtUtc"])
            # Save independent receipt before ledger: either one prevents accidental resend.
            receipt.update(runId=run_id, subject=delivery["subject"], key=key, demoId=args.demo_id)
            write_json(run_dir / "delivery-receipt.json", receipt)
            write_json(state_path, delivery)
            status.update(status="sent", sent=True, messageId=delivery["messageId"],
                          mailAcceptedAtUtc=receipt["acceptedAtUtc"], gmailMessageId=receipt.get("gmailMessageId"))
            write_json(status_path, status)
            try:
                article_ledger.finish(recipient, run_id, "sent")
            except Exception:
                # Receipt and original delivery ledger are authoritative. The
                # reservation remains blocking; next backfill repairs the index.
                status["articleIndexRepairPending"] = True
                write_json(status_path, status)
            log(run_dir, "complete", "Gmail 已确认发送成功。请在配置的收件邮箱确认本次邮件，再保存录屏。")
            return 0
        except Exception as exc:
            # Do not print connector responses or credential-bearing raw exceptions.
            if status.get("localReadStatus") == "reading":
                status["localReadStatus"] = "failed"
            status.update(status="failed", failureType=type(exc).__name__, failedAtUtc=utc_now())
            write_json(status_path, status)
            log(run_dir, "failed", f"本次停止：{type(exc).__name__}。输入和中间报告已保留；未将失败记作成功。")
            return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="独立科技简报 agent")
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="抓取、分析、排版；--send 才真正发信")
    run.add_argument("--send", action="store_true")
    run.add_argument("--demo-id", default="")
    run.add_argument("--public-only", action="store_true", help="仅使用本次公开新闻，不读取个人资料")
    run.add_argument("--recipient", default="", help="公开新闻模式的唯一收件邮箱")
    run.add_argument("--item-count", type=int, default=None, help="公开模式精确条数，5–20，默认12")
    run.add_argument("--keywords", default="", help="公开新闻筛选词，不作为模型指令")
    run.add_argument("--trigger", choices=["manual", "windows_task"], default="manual")
    commands.add_parser("check", help="检查模型程序、地图和邮件配置，不发信")
    args = parser.parse_args(argv)
    if args.command == "run":
        if (args.recipient or args.item_count is not None or args.keywords) and not args.public_only:
            parser.error("--recipient 仅适用于 --public-only；原有私人收件配置不能被覆盖。")
        if args.public_only:
            from public_news import normalize_recipient, normalize_preferences
            try:
                args.recipient = normalize_recipient(args.recipient)
                args.item_count, args.keywords = normalize_preferences(12 if args.item_count is None else args.item_count, args.keywords)
            except ValueError:
                parser.error("公开新闻模式需要一个有效的纯文本收件邮箱。")
            if not args.demo_id:
                parser.error("公开新闻模式需要 --demo-id，避免与每日私人简报混用。")
    config = read_json(ROOT / "config.json")
    if args.command == "check":
        from gmail_delivery import GmailMailer
        available = Path(config["codexExecutable"]).is_file() and (ROOT / "profile-context.json").is_file()
        local_manifest_ready = (ROOT / "local-sources.json").is_file()
        try:
            GmailMailer(config).preflight()
            mail_ready = True
        except Exception:
            mail_ready = False
        print(json.dumps({"modelAndProfileReady": available, "localSourcesManifestReady": local_manifest_ready,
                          "gmailProgramConfigurationReady": mail_ready}, ensure_ascii=False))
        return 0 if available and local_manifest_ready and mail_ready else 2
    if len(args.demo_id) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in args.demo_id):
        parser.error("演示编号只能包含字母、数字、下划线或连字符，最长80字符。")
    return run_pipeline(args, config)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("agent 已停止。若正处于发信阶段，请先核对结果再重试。")
        raise SystemExit(130)
    except Exception as exc:
        print(f"启动失败：{type(exc).__name__}。请检查配置。", file=sys.stderr)
        raise SystemExit(1)
