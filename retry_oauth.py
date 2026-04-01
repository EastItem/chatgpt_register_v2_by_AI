"""
根据 registered_accounts.txt 中的 oauth 状态重试 OAuth。
"""

import argparse
import os
import sys
import time
from pathlib import Path

from lib.chatgpt_client import ChatGPTClient
from lib.config import as_bool, load_config
from lib.imap_client import ImapClient
from lib.oauth_client import OAuthClient
from lib.skymail_client import init_skymail_client
from lib.token_manager import TokenManager


STATUS_OK = "oauth=ok"
STATUS_FAILED = "oauth=failed"


def parse_account_line(line, index):
    """解析账号文件中的一行。"""
    raw = line.rstrip("\n")
    stripped = raw.strip()

    if not stripped:
        return {
            "index": index,
            "raw": raw,
            "valid": False,
            "blank": True,
        }

    parts = [part.strip() for part in stripped.split("----")]
    if len(parts) < 2:
        return {
            "index": index,
            "raw": raw,
            "valid": False,
            "blank": False,
        }

    email = parts[0]
    password = parts[1]
    extras = parts[2:]
    status = ""
    other_extras = []

    for extra in extras:
        if extra.startswith("oauth="):
            status = extra
        elif extra:
            other_extras.append(extra)

    return {
        "index": index,
        "raw": raw,
        "valid": True,
        "blank": False,
        "email": email,
        "password": password,
        "status": status,
        "other_extras": other_extras,
    }


def format_account_record(record):
    """将记录格式化回文件行。"""
    if not record.get("valid"):
        return record.get("raw", "")

    parts = [record["email"], record["password"]]
    parts.extend(record.get("other_extras", []))

    status = record.get("status", "")
    if status:
        parts.append(status)

    return "----".join(parts)


def load_account_records(accounts_file):
    """加载账号记录。"""
    with open(accounts_file, "r", encoding="utf-8") as f:
        lines = f.readlines()

    records = [parse_account_line(line, idx) for idx, line in enumerate(lines)]
    if not lines:
        records = []
    return records


def write_account_records(records, output_path):
    """写回账号记录。"""
    output_parent = Path(output_path).parent
    output_parent.mkdir(parents=True, exist_ok=True)

    temp_path = f"{output_path}.tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(f"{format_account_record(record)}\n")
    os.replace(temp_path, output_path)


def latest_retry_targets(records, mode):
    """根据模式选出需要处理的账号。"""
    latest_by_key = {}
    for record in records:
        if not record.get("valid"):
            continue
        latest_by_key[(record["email"], record["password"])] = record["index"]

    target_indexes = []
    for idx in latest_by_key.values():
        record = records[idx]
        status = record.get("status", "")

        if mode == "all":
            target_indexes.append(idx)
        elif mode == "failed":
            if status == STATUS_FAILED:
                target_indexes.append(idx)
        else:
            if status != STATUS_OK:
                target_indexes.append(idx)

    return [records[idx] for idx in sorted(target_indexes)]


def update_matching_status(records, email, password, status):
    """同步更新同一账号密码的所有状态。"""
    for record in records:
        if not record.get("valid"):
            continue
        if record["email"] == email and record["password"] == password:
            record["status"] = status


def init_mail_client(config):
    """初始化邮箱客户端。"""
    if as_bool(config.get("use_imap", False)):
        imap_user = config.get("imap_user", "")
        imap_pass = config.get("imap_password", "")
        if not imap_user or not imap_pass:
            raise ValueError("启用了 use_imap 但未配置 imap_user / imap_password")

        return ImapClient(
            imap_user=imap_user,
            imap_pass=imap_pass,
            imap_server=config.get("imap_server", "imap.2925.com"),
            imap_port=config.get("imap_port", 993),
            email_prefix=config.get("email_prefix", ""),
            email_domain=config.get("email_domain", "2925.com"),
        )

    return init_skymail_client(config)


def build_runtime_config(base_config, output_dir):
    """构造本次运行使用的配置。"""
    config = dict(base_config)

    if output_dir:
        base_output_dir = Path(output_dir).expanduser().resolve()
        base_output_dir.mkdir(parents=True, exist_ok=True)
        config["ak_file"] = str(base_output_dir / Path(config.get("ak_file", "ak.txt")).name)
        config["rk_file"] = str(base_output_dir / Path(config.get("rk_file", "rk.txt")).name)
        config["token_json_dir"] = str(base_output_dir / "tokens")

    return config


def retry_one_account(record, config, mail_client, verbose):
    """对单个账号重试 OAuth。"""
    email = record["email"]
    password = record["password"]

    chatgpt_client = ChatGPTClient(proxy=config.get("proxy", ""), verbose=verbose)
    try:
        chatgpt_client.visit_homepage()
    except Exception:
        pass

    oauth_client = OAuthClient(config, proxy=config.get("proxy", ""), verbose=verbose)
    oauth_client.session = chatgpt_client.session

    tokens = oauth_client.login_and_get_tokens(
        email,
        password,
        chatgpt_client.device_id,
        chatgpt_client.ua,
        chatgpt_client.sec_ch_ua,
        chatgpt_client.impersonate,
        mail_client,
    )

    if tokens and tokens.get("access_token"):
        token_manager = TokenManager(config)
        token_manager.save_tokens(email, tokens)
        return True

    return False


def build_parser():
    """构建命令行参数。"""
    parser = argparse.ArgumentParser(description="根据 registered_accounts.txt 重试 OAuth")
    parser.add_argument(
        "--accounts-file",
        default="",
        help="账号状态文件路径，默认读取 config.json 中的 output_file",
    )
    parser.add_argument(
        "--status-output",
        default="",
        help="更新后的账号状态输出路径，默认直接覆盖 accounts-file",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="OAuth 成功后的输出目录，里面会写 ak.txt / rk.txt / tokens/",
    )
    parser.add_argument(
        "--mode",
        choices=("pending", "failed", "all"),
        default="pending",
        help="处理哪些账号：pending=非 oauth=ok，failed=仅 oauth=failed，all=全部",
    )
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个账号，0 表示不限制")
    parser.add_argument("--delay", type=float, default=1.0, help="每个账号之间的延迟秒数")
    parser.add_argument("--quiet", action="store_true", help="减少详细日志")
    return parser


def main():
    """入口函数。"""
    parser = build_parser()
    args = parser.parse_args()

    base_config = load_config()
    runtime_config = build_runtime_config(base_config, args.output_dir)

    accounts_file = args.accounts_file or runtime_config.get("output_file", "registered_accounts.txt")
    accounts_file = str(Path(accounts_file).expanduser().resolve())
    status_output = args.status_output or accounts_file
    status_output = str(Path(status_output).expanduser().resolve())

    if not os.path.exists(accounts_file):
        print(f"❌ 未找到账号文件: {accounts_file}")
        return 1

    records = load_account_records(accounts_file)
    targets = latest_retry_targets(records, args.mode)

    if args.limit > 0:
        targets = targets[:args.limit]

    if not targets:
        print("没有需要重试 OAuth 的账号。")
        if status_output != accounts_file:
            write_account_records(records, status_output)
        return 0

    try:
        mail_client = init_mail_client(runtime_config)
    except Exception as e:
        print(f"❌ 初始化邮箱客户端失败: {e}")
        return 1

    print("=" * 60)
    print("  OAuth 重试工具")
    print("=" * 60)
    print(f"账号文件: {accounts_file}")
    print(f"状态输出: {status_output}")
    if args.output_dir:
        print(f"Token 输出目录: {Path(args.output_dir).expanduser().resolve()}")
    else:
        print(f"Token 输出目录: {runtime_config.get('token_json_dir', 'tokens')}")
    print(f"处理模式: {args.mode}")
    print(f"待处理数量: {len(targets)}")
    print()

    success_count = 0
    failed_count = 0
    verbose = not args.quiet

    for idx, record in enumerate(targets, start=1):
        email = record["email"]
        print(f"[{idx}/{len(targets)}] 开始 OAuth: {email}")

        try:
            ok = retry_one_account(record, runtime_config, mail_client, verbose)
        except Exception as e:
            print(f"[{idx}/{len(targets)}] ❌ OAuth 异常: {e}")
            ok = False

        new_status = STATUS_OK if ok else STATUS_FAILED
        update_matching_status(records, record["email"], record["password"], new_status)
        write_account_records(records, status_output)

        if ok:
            success_count += 1
            print(f"[{idx}/{len(targets)}] ✅ OAuth 成功，已更新状态为 {STATUS_OK}")
        else:
            failed_count += 1
            print(f"[{idx}/{len(targets)}] ⚠️ OAuth 失败，状态保持为 {STATUS_FAILED}")

        if idx < len(targets) and args.delay > 0:
            time.sleep(args.delay)

    print()
    print("=" * 60)
    print(f"完成: 成功 {success_count}，失败 {failed_count}")
    print("=" * 60)
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
