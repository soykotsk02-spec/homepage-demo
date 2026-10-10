"""Create a private local .env without displaying its dedicated coursework key."""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import re
import secrets
import sys


ROOT = Path(__file__).resolve().parent


def main():
    destination = ROOT / ".env"
    if destination.exists():
        print("本目录已有 .env，未覆盖。请在本机编辑该文件；不要提交或截图其中的 key。")
        return 1
    if not sys.stdin.isatty():
        print("请在交互式终端运行配置助手；不从重定向输入中读取 key。")
        return 1
    raw_path = input("请输入已获课堂授权的 judgments.jsonl 本机文件路径：").strip().strip('"')
    if not raw_path or any(char in raw_path for char in "\r\n"):
        print("文件路径无效，未写入配置。")
        return 1
    source = Path(raw_path).expanduser().resolve()
    if source.suffix.lower() != ".jsonl" or not source.is_file():
        print("未找到指定的 JSONL 文件，未读取或上传任何内容。")
        return 1
    key = getpass.getpass("新课程专用 key（隐藏输入；直接回车则生成随机 key）：")
    if not key:
        key = secrets.token_urlsafe(32)
    if not re.fullmatch(r"[A-Za-z0-9_-]{20,256}", key):
        print("key 须为 20 至 256 位字母、数字、下划线或连字符；未写入配置。")
        return 1
    content = f"JUDGMENTS_PATH={source.as_posix()}\nJUDGE_API_KEY={key}\n"
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        print("配置已被其他进程创建，未覆盖。")
        return 1
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)
    print("已保存本机 .env；key 未显示。提交课程时只在本机打开此文件复制 key 到老师指定的私密文本框。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
