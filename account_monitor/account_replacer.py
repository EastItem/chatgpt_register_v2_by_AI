"""
account_replacer.py - 注册新账号并替换 CPA 中的异常账号

流程：
1. 调用现有注册逻辑（chatgpt_register_v2.py 的 register_one_account）
2. 将新账号 Token JSON 上传到 CPA
3. 将旧账号凭证保存到隔离文件夹（quarantine/banned/ 或 quarantine/quota_low/）
4. 从 CPA 删除旧账号凭证
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 确保父目录在 sys.path 中，以便导入 lib 模块
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from account_monitor.quarantine_manager import save_to_quarantine


def _import_register_deps(config: dict):
    """导入注册所需的依赖模块，返回 (skymail_client, token_manager, oauth_client) 或抛出异常"""
    from lib.config import as_bool
    from lib.token_manager import TokenManager
    from lib.oauth_client import OAuthClient

    use_imap = as_bool(config.get("use_imap", False))
    if use_imap:
        from lib.imap_client import ImapClient
        skymail_client = ImapClient(
            imap_user=config.get("imap_user", ""),
            imap_pass=config.get("imap_password", ""),
            imap_server=config.get("imap_server", "imap.2925.com"),
            imap_port=config.get("imap_port", 993),
            email_prefix=config.get("email_prefix", ""),
            email_domain=config.get("email_domain", "2925.com"),
        )
    else:
        from lib.skymail_client import init_skymail_client
        skymail_client = init_skymail_client(config)

    token_manager = TokenManager(config)
    oauth_client = OAuthClient(config, proxy=config.get("proxy", ""), verbose=False)
    return skymail_client, token_manager, oauth_client


class AccountReplacer:
    """
    注册新账号并替换 CPA 中的异常账号。

    Args:
        main_config: 注册配置（来自 config.json）
        quota_checker: QuotaChecker 实例（用于上传/删除 CPA 凭证）
        quarantine_dir: 隔离文件夹根目录；None 表示不隔离（直接删除）
        dry_run: True 时只模拟，不实际注册或删除
    """

    def __init__(self, main_config: dict, quota_checker, quarantine_dir: Optional[Path] = None, dry_run: bool = False):
        self.config = main_config
        self.quota_checker = quota_checker
        self.quarantine_dir = Path(quarantine_dir) if quarantine_dir else None
        self.dry_run = dry_run

    def register_new_account(self) -> Optional[dict]:
        """
        注册一个新账号，返回 token 数据字典（包含 email, access_token 等）。
        失败返回 None。
        """
        if self.dry_run:
            logger.info("[DryRun] 模拟注册新账号（跳过实际请求）")
            return {
                "type": "codex",
                "email": "dryrun@example.com",
                "access_token": "dry_run_token",
                "refresh_token": "dry_run_refresh",
            }

        from chatgpt_register_v2 import register_one_account

        try:
            skymail_client, token_manager, oauth_client = _import_register_deps(self.config)
        except Exception as e:
            logger.error("初始化注册依赖失败: %s", e)
            return None

        logger.info("开始注册新账号...")
        success, email, password, msg = register_one_account(
            idx=1,
            total=1,
            skymail_client=skymail_client,
            token_manager=token_manager,
            oauth_client=oauth_client,
            config=self.config,
            max_retries=3,
        )

        if not success:
            logger.error("注册新账号失败: %s", msg)
            return None

        # 从 token_manager 保存的文件中读取 token JSON
        token_dir = Path(self.config.get("token_json_dir", "tokens"))
        if not token_dir.is_absolute():
            token_dir = _REPO_ROOT / token_dir
        token_file = token_dir / f"{email}.json"

        if token_file.exists():
            try:
                data = json.loads(token_file.read_text(encoding="utf-8"))
                logger.info("注册成功，账号: %s", email)
                return data
            except Exception as e:
                logger.error("读取 token 文件失败 %s: %s", token_file, e)
                return None
        else:
            # token 文件不存在时，尝试从 token_manager 内存构造
            logger.warning("未找到 token 文件: %s，尝试构造基本结构", token_file)
            return {"type": "codex", "email": email, "access_token": "", "refresh_token": ""}

    def replace_account(self, status) -> bool:
        """
        注册新账号并替换 CPA 中的旧账号。

        流程：
        1. 注册新账号
        2. 上传新账号凭证到 CPA
        3. 将旧账号保存到隔离文件夹（banned/ 或 quota_low/）
        4. 从 CPA 删除旧账号凭证

        Args:
            status: AccountStatus 对象（包含 name, is_banned, is_quota_low 等）

        Returns:
            bool: 是否成功完成替换
        """
        old_name = status.name
        logger.info("开始替换账号: %s", old_name)

        # 1. 注册新账号
        new_token_data = self.register_new_account()
        if not new_token_data:
            logger.error("替换失败（注册新账号失败）: %s", old_name)
            return False

        new_email = new_token_data.get("email", "unknown")
        new_file_name = f"{new_email}.json"

        if self.dry_run:
            logger.info("[DryRun] 跳过上传、隔离和删除操作")
            return True

        # 2. 上传新账号凭证到 CPA
        upload_ok = self.quota_checker.upload_to_cpa(new_token_data, new_file_name)
        if not upload_ok:
            logger.error("上传新账号 %s 到 CPA 失败，不处理旧账号", new_email)
            return False

        # 3. 将旧账号保存到隔离文件夹（替代直接删除）
        if self.quarantine_dir is not None:
            token_dir = Path(self.config.get("token_json_dir", "tokens"))
            if not token_dir.is_absolute():
                token_dir = _REPO_ROOT / token_dir
            save_to_quarantine(status, self.quarantine_dir, token_dir=token_dir)
        else:
            logger.debug("未配置 quarantine_dir，跳过隔离保存")

        # 4. 从 CPA 删除旧账号凭证
        delete_ok = self.quota_checker.delete_from_cpa(old_name)
        if not delete_ok:
            logger.warning("删除旧账号 %s 失败（新账号已上传）", old_name)
            # 不算完全失败，新账号已就绪
            return True

        logger.info("账号替换完成: %s → %s", old_name, new_email)
        return True

    def replace_accounts(self, statuses: list, max_replacements: Optional[int] = None) -> dict:
        """
        批量替换异常账号。

        Args:
            statuses: AccountStatus 对象列表
            max_replacements: 单次最多替换数量（None 表示全部替换）

        Returns:
            dict: {"replaced": [...], "failed": [...]}
        """
        if max_replacements is not None:
            statuses = statuses[:max_replacements]

        replaced = []
        failed = []

        for idx, status in enumerate(statuses):
            name = status.name
            try:
                ok = self.replace_account(status)
                if ok:
                    replaced.append(name)
                else:
                    failed.append(name)
            except Exception as e:
                logger.error("替换账号 %s 时异常: %s", name, e)
                failed.append(name)

            # 注册之间稍作等待，避免过于频繁
            if idx < len(statuses) - 1:
                time.sleep(2)

        return {"replaced": replaced, "failed": failed}
