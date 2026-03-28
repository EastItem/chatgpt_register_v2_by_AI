"""
quota_checker.py - 通过 CPA 接口检测账号状态和额度

检测逻辑：
1. 通过 /v0/management/auth-files 获取所有凭证列表
2. 对每个 codex 类型账号，调用 /v0/management/api-call 代理请求 wham/usage 接口
3. HTTP 401 → 账号封号/失效
4. 解析 usage 响应体，若剩余额度低于阈值 → 额度不足
"""

import json
import logging
import urllib.parse
from typing import Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_MGMT_UA = "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"


def _mgmt_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _safe_json(text: str):
    try:
        return json.loads(text)
    except Exception:
        return {}


def _extract_account_id(item: dict) -> Optional[str]:
    for key in ("chatgpt_account_id", "chatgptAccountId", "account_id", "accountId"):
        val = item.get(key)
        if val:
            return str(val)
    return None


def _get_item_type(item: dict) -> str:
    # "typo" is a known CPA API field name (misspelling of "type") preserved for compatibility
    return str(item.get("type") or item.get("typo") or "")


def _parse_quota_remaining(body_text: str) -> Optional[float]:
    """
    解析 wham/usage 响应体，提取剩余额度。
    
    返回值：
    - float: 剩余额度（如 100.0 表示剩余 100 美元等价额度）
    - None: 无法解析（不影响判断，视为额度充足）

    CPA proxy 返回格式通常为：
    {
      "status_code": 200,
      "body": "{\"quota\": ...}",   # 或 "response_body": ...
      ...
    }
    实际 wham/usage 返回格式示例：
    {
      "remaining_credits": 12.34,   # 剩余点数
      "total_credits": 100.0,
      ...
    }
    字段名因 OpenAI 版本而异，本函数尝试多种常见字段名。
    """
    if not body_text:
        return None
    data = _safe_json(body_text)
    if not data:
        return None

    # 尝试常见字段名
    for key in (
        "remaining_credits",
        "credits_remaining",
        "remaining",
        "remaining_quota",
        "quota_remaining",
        "balance",
        "credits",
    ):
        val = data.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # 嵌套字段：usage.remaining / limits.remaining 等
    for parent_key in ("usage", "quota", "limits", "credit"):
        parent = data.get(parent_key)
        if isinstance(parent, dict):
            for key in ("remaining", "remaining_credits", "balance"):
                val = parent.get(key)
                if val is not None:
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        pass

    return None


class AccountStatus:
    """单个账号的检测结果"""

    def __init__(self, name: str, auth_index: str, item: dict):
        self.name = name
        self.auth_index = auth_index
        self.item = item
        self.is_banned: bool = False          # 封号（401）
        self.is_quota_low: bool = False       # 额度不足
        self.quota_remaining: Optional[float] = None
        self.error: Optional[str] = None

    @property
    def needs_replacement(self) -> bool:
        return self.is_banned or self.is_quota_low

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "auth_index": self.auth_index,
            "is_banned": self.is_banned,
            "is_quota_low": self.is_quota_low,
            "quota_remaining": self.quota_remaining,
            "needs_replacement": self.needs_replacement,
            "error": self.error,
        }

    def __repr__(self):
        flags = []
        if self.is_banned:
            flags.append("封号")
        if self.is_quota_low:
            flags.append(f"额度不足(remaining={self.quota_remaining})")
        if self.error:
            flags.append(f"error={self.error}")
        flag_str = ", ".join(flags) if flags else "正常"
        return f"<AccountStatus name={self.name!r} status={flag_str}>"


class QuotaChecker:
    """
    通过 CPA API 批量检测账号状态。

    Args:
        cpa_base_url: CPA 服务地址，例如 http://localhost:8317
        cpa_token: CPA 管理 Bearer token
        quota_threshold: 额度低于此值视为不足（None 则不检测额度）
        target_type: 目标账号类型（默认 "codex"）
        timeout: 请求超时（秒）
        user_agent: 请求 User-Agent
    """

    def __init__(
        self,
        cpa_base_url: str,
        cpa_token: str,
        quota_threshold: Optional[float] = None,
        target_type: str = "codex",
        timeout: int = 20,
        user_agent: str = DEFAULT_MGMT_UA,
    ):
        self.base_url = (cpa_base_url or "").rstrip("/")
        self.token = cpa_token
        self.quota_threshold = quota_threshold
        self.target_type = target_type
        self.timeout = timeout
        self.user_agent = user_agent

    def fetch_auth_files(self) -> list:
        """从 CPA 获取所有凭证列表"""
        resp = requests.get(
            f"{self.base_url}/v0/management/auth-files",
            headers=_mgmt_headers(self.token),
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        return (data.get("files") if isinstance(data, dict) else []) or []

    def check_account(self, item: dict) -> AccountStatus:
        """检测单个账号的状态（封号 + 额度）"""
        name = item.get("name") or item.get("id") or ""
        auth_index = item.get("auth_index") or ""
        status = AccountStatus(name=name, auth_index=auth_index, item=item)

        if not auth_index:
            logger.debug("跳过无 auth_index 的账号: %s", name)
            return status

        account_id = _extract_account_id(item)
        header = {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": self.user_agent,
        }
        if account_id:
            header["Chatgpt-Account-Id"] = account_id

        payload = {
            "authIndex": auth_index,
            "method": "GET",
            "url": USAGE_URL,
            "header": header,
        }

        try:
            resp = requests.post(
                f"{self.base_url}/v0/management/api-call",
                headers={**_mgmt_headers(self.token), "Content-Type": "application/json"},
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            sc = data.get("status_code")

            # 封号检测
            if sc == 401:
                status.is_banned = True
                logger.info("账号封号(401): %s", name)
                return status

            if sc == 200:
                # 额度检测
                body_text = data.get("body") or data.get("response_body") or ""
                if isinstance(body_text, dict):
                    # 有时 body 直接返回 dict
                    body_text = json.dumps(body_text)
                quota = _parse_quota_remaining(body_text)
                status.quota_remaining = quota
                if quota is not None and self.quota_threshold is not None:
                    if quota < self.quota_threshold:
                        status.is_quota_low = True
                        logger.info(
                            "账号额度不足: %s (remaining=%.2f, threshold=%.2f)",
                            name, quota, self.quota_threshold,
                        )
            else:
                logger.debug("账号 %s 返回 status_code=%s", name, sc)

        except requests.RequestException as e:
            status.error = str(e)
            logger.warning("检测账号 %s 时网络错误: %s", name, e)
        except Exception as e:
            status.error = str(e)
            logger.warning("检测账号 %s 时异常: %s", name, e)

        return status

    def check_all(self) -> list:
        """
        检测所有 codex 类型账号。

        Returns:
            list[AccountStatus]: 所有账号的检测结果列表
        """
        files = self.fetch_auth_files()
        candidates = [f for f in files if _get_item_type(f).lower() == self.target_type.lower()]
        logger.info("共找到 %d 个 %s 账号，开始检测...", len(candidates), self.target_type)

        results = []
        for idx, item in enumerate(candidates, 1):
            name = item.get("name") or item.get("id") or ""
            logger.debug("[%d/%d] 检测账号: %s", idx, len(candidates), name)
            status = self.check_account(item)
            results.append(status)
            if status.needs_replacement:
                logger.info(
                    "[%d/%d] 账号需要替换: %s %s",
                    idx, len(candidates), name,
                    "(封号)" if status.is_banned else "(额度不足)",
                )
            else:
                logger.debug("[%d/%d] 账号正常: %s", idx, len(candidates), name)

        return results

    def get_accounts_needing_replacement(self) -> list:
        """返回需要替换的账号列表（封号 + 额度不足）"""
        all_results = self.check_all()
        return [s for s in all_results if s.needs_replacement]

    def delete_from_cpa(self, name: str) -> bool:
        """从 CPA 删除指定账号凭证"""
        if not name:
            return False
        encoded = urllib.parse.quote(name, safe="")
        try:
            resp = requests.delete(
                f"{self.base_url}/v0/management/auth-files?name={encoded}",
                headers=_mgmt_headers(self.token),
                timeout=self.timeout,
            )
            data = _safe_json(resp.text)
            success = resp.status_code == 200 and data.get("status") == "ok"
            if success:
                logger.info("已从 CPA 删除账号: %s", name)
            else:
                logger.warning("删除账号失败: %s (HTTP %s)", name, resp.status_code)
            return success
        except Exception as e:
            logger.error("删除账号 %s 时异常: %s", name, e)
            return False

    def upload_to_cpa(self, token_data: dict, file_name: str) -> bool:
        """上传新账号凭证到 CPA"""
        content = json.dumps(token_data, ensure_ascii=False).encode("utf-8")
        files = {"file": (file_name, content, "application/json")}
        headers = {"Authorization": f"Bearer {self.token}"}
        try:
            resp = requests.post(
                f"{self.base_url}/v0/management/auth-files",
                files=files,
                headers=headers,
                timeout=self.timeout,
            )
            success = resp.status_code in (200, 201, 204)
            if success:
                logger.info("已上传新账号凭证到 CPA: %s", file_name)
            else:
                logger.warning("上传账号凭证失败: %s (HTTP %s)", file_name, resp.status_code)
            return success
        except Exception as e:
            logger.error("上传账号凭证 %s 时异常: %s", file_name, e)
            return False
