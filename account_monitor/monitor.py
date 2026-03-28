"""
monitor.py - 账号自动监控主程序

定时检测 CPA 中的 Codex 账号：
- 封号（HTTP 401）→ 自动替换
- 额度不足（低于阈值）→ 自动替换

支持单次运行（--once）和守护模式（定时循环）。
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 确保父目录在 sys.path 中
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from account_monitor.quota_checker import QuotaChecker
from account_monitor.account_replacer import AccountReplacer
from account_monitor.quarantine_manager import recheck_quarantine


def _load_monitor_config(config_path: Optional[str] = None) -> dict:
    """
    加载监控配置。

    优先级：命令行参数 > 环境变量 > account_monitor/config.json > 默认值
    """
    defaults = {
        # CPA 设置
        "cpa_base_url": "http://localhost:8317",
        "cpa_token": "",
        # 检测设置
        "check_interval_seconds": 3600,   # 每小时检测一次
        "quota_threshold": None,          # None 表示不检测额度；设置如 10.0 则低于 10 时替换
        "target_type": "codex",
        "request_timeout": 20,
        # 替换设置
        "auto_replace": True,
        "max_replacements_per_run": None,  # None 表示替换全部异常账号
        "quarantine_dir": "",              # 隔离文件夹根目录；空字符串时使用默认路径
        "dry_run": False,
        # 日志设置
        "log_level": "INFO",
        "log_file": "",
    }

    # 读取 config.json 文件
    search_paths = []
    if config_path:
        search_paths.append(Path(config_path))
    # 默认路径：account_monitor/config.json
    search_paths.append(Path(__file__).parent / "config.json")

    for path in search_paths:
        if path.exists():
            try:
                file_cfg = json.loads(path.read_text(encoding="utf-8"))
                defaults.update(file_cfg)
                logger.debug("已加载监控配置: %s", path)
                break
            except Exception as e:
                logger.warning("加载配置文件 %s 失败: %s", path, e)

    # 环境变量覆盖（用于 Docker/CI 场景）
    env_map = {
        "CPA_BASE_URL": "cpa_base_url",
        "CPA_TOKEN": "cpa_token",
        "MONITOR_INTERVAL": "check_interval_seconds",
        "QUOTA_THRESHOLD": "quota_threshold",
        "TARGET_TYPE": "target_type",
        "REQUEST_TIMEOUT": "request_timeout",
        "AUTO_REPLACE": "auto_replace",
        "MAX_REPLACEMENTS": "max_replacements_per_run",
        "QUARANTINE_DIR": "quarantine_dir",
        "DRY_RUN": "dry_run",
        "LOG_LEVEL": "log_level",
        "LOG_FILE": "log_file",
    }
    for env_key, cfg_key in env_map.items():
        val = os.environ.get(env_key)
        if val is not None:
            if cfg_key in ("check_interval_seconds", "request_timeout", "max_replacements_per_run"):
                try:
                    defaults[cfg_key] = int(val) if val != "" else None
                except ValueError:
                    pass
            elif cfg_key in ("quota_threshold",):
                try:
                    defaults[cfg_key] = float(val) if val != "" else None
                except ValueError:
                    pass
            elif cfg_key in ("auto_replace", "dry_run"):
                defaults[cfg_key] = val.lower() in ("1", "true", "yes", "y", "on")
            else:
                defaults[cfg_key] = val

    return defaults


def _setup_logging(level_str: str, log_file: str):
    """配置日志输出"""
    level = getattr(logging, level_str.upper(), logging.INFO)
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers,
        force=True,
    )


def _load_main_config() -> dict:
    """加载主项目的 config.json（用于注册新账号）"""
    config_path = _REPO_ROOT / "config.json"
    defaults = {
        "proxy": "",
        "enable_oauth": True,
        "oauth_required": True,
        "oauth_issuer": "https://auth.openai.com",
        "oauth_client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "oauth_redirect_uri": "http://localhost:1455/auth/callback",
        "ak_file": "ak.txt",
        "rk_file": "rk.txt",
        "token_json_dir": "tokens",
        "output_file": "registered_accounts.txt",
        "use_imap": False,
        "imap_server": "imap.2925.com",
        "imap_port": 993,
        "imap_user": "",
        "imap_password": "",
        "email_prefix": "",
        "email_domain": "2925.com",
        "skymail_admin_email": "",
        "skymail_admin_password": "",
        "skymail_domains": [],
    }
    if config_path.exists():
        try:
            file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
            defaults.update(file_cfg)
        except Exception as e:
            logger.warning("加载主配置 config.json 失败: %s", e)
    return defaults


class AccountMonitor:
    """
    账号自动监控器。

    检测 CPA 中的 Codex 账号，对封号或额度不足的账号自动注册替换。
    """

    def __init__(self, monitor_config: dict, main_config: dict):
        self.cfg = monitor_config
        self.main_config = main_config

        # 隔离文件夹默认为 account_monitor/quarantine/
        quarantine_dir_cfg = self.cfg.get("quarantine_dir", "")
        if quarantine_dir_cfg:
            self.quarantine_dir = Path(quarantine_dir_cfg)
        else:
            self.quarantine_dir = Path(__file__).parent / "quarantine"

        checker = QuotaChecker(
            cpa_base_url=self.cfg["cpa_base_url"],
            cpa_token=self.cfg["cpa_token"],
            quota_threshold=self.cfg.get("quota_threshold"),
            target_type=self.cfg.get("target_type", "codex"),
            timeout=self.cfg.get("request_timeout", 20),
        )
        self.checker = checker
        self.replacer = AccountReplacer(
            main_config=self.main_config,
            quota_checker=checker,
            quarantine_dir=self.quarantine_dir,
            dry_run=bool(self.cfg.get("dry_run", False)),
        )

    def run_once(self) -> dict:
        """
        执行一次完整的检测+替换流程。

        Returns:
            dict: 本次运行结果摘要
        """
        logger.info("=" * 50)
        logger.info("开始账号状态检测...")

        # 1. 检测所有账号
        try:
            all_statuses = self.checker.check_all()
        except Exception as e:
            logger.error("获取账号列表失败: %s", e)
            return {"error": str(e), "checked": 0, "need_replace": 0, "replaced": 0, "failed": 0}

        total = len(all_statuses)
        bad_accounts = [s for s in all_statuses if s.needs_replacement]
        banned = [s for s in bad_accounts if s.is_banned]
        quota_low = [s for s in bad_accounts if s.is_quota_low and not s.is_banned]

        logger.info(
            "检测完成: 共 %d 个账号，正常 %d，异常 %d（封号 %d，额度不足 %d）",
            total, total - len(bad_accounts), len(bad_accounts), len(banned), len(quota_low),
        )

        result = {
            "checked": total,
            "normal": total - len(bad_accounts),
            "need_replace": len(bad_accounts),
            "banned": len(banned),
            "quota_low": len(quota_low),
            "replaced": 0,
            "failed": 0,
            "replaced_names": [],
            "failed_names": [],
        }

        if not bad_accounts:
            logger.info("所有账号状态正常，无需替换。")
            return result

        # 2. 替换异常账号
        auto_replace = bool(self.cfg.get("auto_replace", True))
        if not auto_replace:
            logger.info("auto_replace=False，跳过自动替换。异常账号: %s", [s.name for s in bad_accounts])
            return result

        max_replace = self.cfg.get("max_replacements_per_run")
        logger.info("开始替换 %d 个异常账号...", len(bad_accounts))

        replace_result = self.replacer.replace_accounts(bad_accounts, max_replacements=max_replace)
        result["replaced"] = len(replace_result["replaced"])
        result["failed"] = len(replace_result["failed"])
        result["replaced_names"] = replace_result["replaced"]
        result["failed_names"] = replace_result["failed"]

        logger.info(
            "替换完成: 成功 %d，失败 %d",
            result["replaced"], result["failed"],
        )
        logger.info("=" * 50)
        return result

    def check_quarantine(self, verbose: bool = False) -> dict:
        """
        批量重新检测隔离文件夹中的账号状态。

        扫描 quarantine/banned/ 和 quarantine/quota_low/ 中的所有文件，
        通过 CPA 重新探测每个账号的当前状态，返回统计信息和详细结果。

        Args:
            verbose: 是否输出每个账号的详细检测日志

        Returns:
            dict: {
                "quarantine_dir": str,
                "stats": {
                    "total": int,       # 扫描到的隔离文件总数
                    "rechecked": int,   # 实际完成检测的数量
                    "still_banned": int,
                    "still_quota_low": int,
                    "recovered": int,   # 已恢复正常
                    "check_error": int, # 检测失败
                    "no_token_data": int  # 无凭证，无法检测
                },
                "details": [
                    {
                        "file": str,
                        "cpa_name": str,
                        "quarantine_reason": str,
                        "quarantine_time": str,
                        "current_status": str,  # still_banned / still_quota_low /
                                                # recovered / check_error / no_token_data
                        "quota_remaining": float | None,
                        "error": str | None
                    },
                    ...
                ]
            }
        """
        logger.info("开始复查隔离账号: %s", self.quarantine_dir)
        result = recheck_quarantine(self.quarantine_dir, self.checker, verbose=verbose)
        result["quarantine_dir"] = str(self.quarantine_dir)
        return result

    def run_loop(self, interval_seconds: Optional[int] = None):
        """
        守护模式：定时循环执行检测。

        Args:
            interval_seconds: 检测间隔（秒），None 使用配置文件中的值
        """
        interval = interval_seconds or self.cfg.get("check_interval_seconds", 3600)
        logger.info("账号监控已启动，检测间隔: %d 秒", interval)

        while True:
            try:
                result = self.run_once()
                logger.info("本次检测摘要: %s", json.dumps(result, ensure_ascii=False))
            except KeyboardInterrupt:
                logger.info("收到中断信号，停止监控。")
                break
            except Exception as e:
                logger.error("监控循环异常: %s", e, exc_info=True)

            logger.info("等待 %d 秒后进行下次检测...", interval)
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                logger.info("收到中断信号，停止监控。")
                break


def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(
        description="账号自动监控工具 - 检测 CPA 中 Codex 账号的封号和额度状态，自动注册替换异常账号",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 单次检测（不替换）
  python -m account_monitor --once --no-replace

  # 单次检测并自动替换
  python -m account_monitor --once

  # 守护模式（每 30 分钟检测一次）
  python -m account_monitor --interval 1800

  # 指定 CPA 地址和 token
  python -m account_monitor --cpa-url http://localhost:8317 --cpa-token Bearer_xxx --once

  # 仅检测额度（低于 5.0 时替换）
  python -m account_monitor --quota-threshold 5.0 --once

  # 模拟运行（不实际注册和删除）
  python -m account_monitor --dry-run --once

  # 复查隔离文件夹中的账号状态
  python -m account_monitor --check-quarantine
""",
    )
    parser.add_argument("--config", default="", help="监控配置文件路径（默认: account_monitor/config.json）")
    parser.add_argument("--cpa-url", default="", help="CPA 服务地址（覆盖配置文件）")
    parser.add_argument("--cpa-token", default="", help="CPA 管理 token（覆盖配置文件）")
    parser.add_argument("--interval", type=int, default=0, help="守护模式检测间隔（秒，默认: 3600）")
    parser.add_argument("--quota-threshold", type=float, default=None, help="额度阈值，低于此值触发替换（默认: 不检测额度）")
    parser.add_argument("--once", action="store_true", help="仅执行一次检测后退出")
    parser.add_argument("--no-replace", action="store_true", help="只检测不替换（auto_replace=False）")
    parser.add_argument("--check-quarantine", action="store_true", help="复查隔离文件夹中的账号状态并打印统计和明细")
    parser.add_argument("--quarantine-dir", default="", help="隔离文件夹根目录（默认: account_monitor/quarantine/）")
    parser.add_argument("--dry-run", action="store_true", help="模拟运行，不实际注册或删除账号")
    parser.add_argument("--log-level", default="", help="日志级别（DEBUG/INFO/WARNING/ERROR）")
    parser.add_argument("--log-file", default="", help="日志文件路径")
    args = parser.parse_args()

    # 加载配置
    monitor_cfg = _load_monitor_config(args.config if args.config else None)

    # CLI 参数覆盖
    if args.cpa_url:
        monitor_cfg["cpa_base_url"] = args.cpa_url
    if args.cpa_token:
        monitor_cfg["cpa_token"] = args.cpa_token
    if args.interval > 0:
        monitor_cfg["check_interval_seconds"] = args.interval
    if args.quota_threshold is not None:
        monitor_cfg["quota_threshold"] = args.quota_threshold
    if args.no_replace:
        monitor_cfg["auto_replace"] = False
    if args.quarantine_dir:
        monitor_cfg["quarantine_dir"] = args.quarantine_dir
    if args.dry_run:
        monitor_cfg["dry_run"] = True
    if args.log_level:
        monitor_cfg["log_level"] = args.log_level
    if args.log_file:
        monitor_cfg["log_file"] = args.log_file

    # 初始化日志
    _setup_logging(monitor_cfg.get("log_level", "INFO"), monitor_cfg.get("log_file", ""))

    # 校验必填项
    if not monitor_cfg.get("cpa_token"):
        logger.error("未设置 CPA token，请在 account_monitor/config.json 中配置 cpa_token，或通过 --cpa-token 参数传入")
        sys.exit(1)

    # 加载主配置（注册所需）
    main_config = _load_main_config()

    monitor = AccountMonitor(monitor_cfg, main_config)

    if args.check_quarantine:
        verbose = monitor_cfg.get("log_level", "INFO").upper() == "DEBUG"
        result = monitor.check_quarantine(verbose=verbose)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.once:
        result = monitor.run_once()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        monitor.run_loop()


if __name__ == "__main__":
    main()
