"""
quarantine_manager.py - 隔离账号管理

当账号被判定为封号或额度不足时，不直接删除，而是将其凭证
保存到隔离文件夹中，以便后续复查或处理：

  account_monitor/quarantine/banned/       - 封号（HTTP 401）账号
  account_monitor/quarantine/quota_low/    - 额度不足账号

隔离文件格式（JSON）：
{
    "quarantine_reason": "banned" | "quota_low",
    "quarantine_time": "2025-01-01T12:00:00",
    "cpa_name": "account@example.com.json",
    "quota_remaining": null,
    "token_data": { ... }   # 本地 token JSON，若找不到则为 null
}

提供 recheck_quarantine() 函数，可对隔离文件夹中的账号批量
重新检测（上传到 CPA → 探测 → 删除），返回统计和明细。
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from account_monitor.quota_checker import AccountStatus

logger = logging.getLogger(__name__)

SUBDIR_BANNED = "banned"
SUBDIR_QUOTA_LOW = "quota_low"


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.error("写入文件失败 %s: %s", path, e)
        return False


def save_to_quarantine(
    status,
    quarantine_dir: Path,
    token_dir: Optional[Path] = None,
) -> bool:
    """
    将异常账号的凭证信息保存到对应隔离子目录。

    Args:
        status: AccountStatus 对象（来自 quota_checker）
        quarantine_dir: 隔离根目录（将自动创建 banned/ 或 quota_low/ 子目录）
        token_dir: 本地 token JSON 目录，用于补充 token_data；None 时跳过

    Returns:
        bool: 是否成功保存
    """
    if status.is_banned:
        subdir = SUBDIR_BANNED
    elif status.is_quota_low:
        subdir = SUBDIR_QUOTA_LOW
    else:
        logger.debug("账号状态正常，无需隔离: %s", status.name)
        return False

    # 尝试读取本地 token JSON
    token_data = None
    if token_dir is not None:
        token_file = Path(token_dir) / status.name
        if token_file.exists():
            token_data = _read_json(token_file)
            if token_data:
                logger.debug("已读取本地 token 文件: %s", token_file)
            else:
                logger.warning("本地 token 文件解析失败: %s", token_file)
        else:
            logger.debug("未找到本地 token 文件: %s", token_file)

    quarantine_data = {
        "quarantine_reason": "banned" if status.is_banned else "quota_low",
        "quarantine_time": datetime.now(timezone.utc).isoformat(),
        "cpa_name": status.name,
        "quota_remaining": status.quota_remaining,
        "token_data": token_data,
    }

    save_path = quarantine_dir / subdir / status.name
    ok = _write_json(save_path, quarantine_data)
    if ok:
        logger.info(
            "已将账号保存到隔离区 [%s]: %s",
            subdir, status.name,
        )
    return ok


def recheck_quarantine(
    quarantine_dir: Path,
    checker,
    verbose: bool = False,
) -> dict:
    """
    批量重新检测隔离文件夹中的账号当前状态。

    流程（对每个有 token_data 的隔离文件）：
      1. 将 token_data 临时上传到 CPA
      2. 从 CPA 列表中找到该文件并获取 auth_index
      3. 通过 CPA api-call 探测账号状态（封号/额度）
      4. 从 CPA 删除临时上传的文件

    Args:
        quarantine_dir: 隔离根目录（含 banned/ 和 quota_low/ 子目录）
        checker: QuotaChecker 实例（用于 CPA API 操作）
        verbose: 是否输出详细日志

    Returns:
        dict:
        {
            "stats": {
                "total": int,       # 扫描到的隔离文件总数
                "rechecked": int,   # 实际完成检测的数量（有 token_data）
                "still_banned": int,
                "still_quota_low": int,
                "recovered": int,   # 已恢复正常的账号
                "check_error": int, # 检测出错
                "no_token_data": int  # 无 token_data，无法重新检测
            },
            "details": [
                {
                    "file": str,               # 隔离文件路径
                    "cpa_name": str,
                    "quarantine_reason": str,  # 原始隔离原因
                    "quarantine_time": str,
                    "current_status": str,     # still_banned / still_quota_low /
                                               # recovered / check_error / no_token_data
                    "quota_remaining": float|None,
                    "error": str|None
                },
                ...
            ]
        }
    """
    quarantine_dir = Path(quarantine_dir)
    stats = {
        "total": 0,
        "rechecked": 0,
        "still_banned": 0,
        "still_quota_low": 0,
        "recovered": 0,
        "check_error": 0,
        "no_token_data": 0,
    }
    details = []

    # 扫描两个子目录
    for subdir_name in (SUBDIR_BANNED, SUBDIR_QUOTA_LOW):
        subdir = quarantine_dir / subdir_name
        if not subdir.exists():
            continue
        for json_file in sorted(subdir.glob("*.json")):
            stats["total"] += 1
            detail = _recheck_one(json_file, subdir_name, checker, verbose)
            details.append(detail)

            current = detail.get("current_status", "check_error")
            if current == "still_banned":
                stats["still_banned"] += 1
                stats["rechecked"] += 1
            elif current == "still_quota_low":
                stats["still_quota_low"] += 1
                stats["rechecked"] += 1
            elif current == "recovered":
                stats["recovered"] += 1
                stats["rechecked"] += 1
            elif current == "no_token_data":
                stats["no_token_data"] += 1
            else:
                stats["check_error"] += 1
                stats["rechecked"] += 1

    logger.info(
        "隔离账号复查完成: 共 %d 个，检测 %d 个，"
        "仍封号 %d，仍额度不足 %d，已恢复 %d，检测失败 %d，无凭证 %d",
        stats["total"], stats["rechecked"],
        stats["still_banned"], stats["still_quota_low"],
        stats["recovered"], stats["check_error"], stats["no_token_data"],
    )

    return {"stats": stats, "details": details}


def _recheck_one(json_file: Path, original_reason: str, checker, verbose: bool) -> dict:
    """
    对单个隔离文件执行重新检测。

    Returns:
        dict: 该账号的检测明细
    """
    quarantine_data = _read_json(json_file)
    if quarantine_data is None:
        return {
            "file": str(json_file),
            "cpa_name": json_file.name,
            "quarantine_reason": original_reason,
            "quarantine_time": None,
            "current_status": "check_error",
            "quota_remaining": None,
            "error": "隔离文件读取失败",
        }

    cpa_name = quarantine_data.get("cpa_name") or json_file.name
    token_data = quarantine_data.get("token_data")
    base_detail = {
        "file": str(json_file),
        "cpa_name": cpa_name,
        "quarantine_reason": quarantine_data.get("quarantine_reason", original_reason),
        "quarantine_time": quarantine_data.get("quarantine_time"),
        "quota_remaining": None,
        "error": None,
    }

    if not token_data:
        logger.info("账号 %s 无 token_data，跳过重新检测", cpa_name)
        return {**base_detail, "current_status": "no_token_data"}

    if verbose:
        logger.debug("开始重新检测隔离账号: %s", cpa_name)

    # 1. 上传 token_data 到 CPA
    upload_ok = checker.upload_to_cpa(token_data, cpa_name)
    if not upload_ok:
        logger.warning("上传隔离账号 %s 到 CPA 失败，跳过检测", cpa_name)
        return {**base_detail, "current_status": "check_error", "error": "上传 CPA 失败"}

    current_status = "check_error"
    quota_remaining = None
    error_msg = None

    try:
        # 2. 从 CPA 列表中找到刚上传的文件，取得 auth_index
        files = checker.fetch_auth_files()
        cpa_item = next((f for f in files if f.get("name") == cpa_name), None)
        if cpa_item is None:
            error_msg = "上传后在 CPA 列表中未找到该文件"
            logger.warning("重新检测失败: %s - %s", cpa_name, error_msg)
        else:
            # 3. 探测账号状态
            status = checker.check_account(cpa_item)
            quota_remaining = status.quota_remaining

            if status.is_banned:
                current_status = "still_banned"
                if verbose:
                    logger.info("账号仍封号: %s", cpa_name)
            elif status.is_quota_low:
                current_status = "still_quota_low"
                if verbose:
                    logger.info(
                        "账号仍额度不足: %s (remaining=%s, threshold=%s)",
                        cpa_name, quota_remaining, checker.quota_threshold,
                    )
            elif status.error:
                current_status = "check_error"
                error_msg = status.error
            else:
                current_status = "recovered"
                if verbose:
                    logger.info("账号已恢复正常: %s", cpa_name)

    except Exception as e:
        error_msg = str(e)
        logger.error("重新检测账号 %s 时异常: %s", cpa_name, e)
    finally:
        # 4. 无论检测结果如何，清理 CPA 中的临时上传
        checker.delete_from_cpa(cpa_name)

    return {
        **base_detail,
        "current_status": current_status,
        "quota_remaining": quota_remaining,
        "error": error_msg,
    }
