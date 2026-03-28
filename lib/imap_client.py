"""
IMAP 邮箱客户端模块
支持通过 IMAP 协议接收验证码，兼容不支持 SEARCH 命令的简易 IMAP 服务器（如 2925.com）
"""

import email
import imaplib
import re
import secrets
import string
import sys
import time


# 轮询时间配置
_INITIAL_RETRY_INTERVAL = 0.5   # 前 10 秒内每次等待时间（秒）
_EXTENDED_RETRY_INTERVAL = 2    # 10 秒后每次等待时间（秒）
_EXTENDED_RETRY_THRESHOLD = 10  # 切换为慢速轮询的时间阈值（秒）

# 每次检查最新邮件的最大封数
_MAX_RECENT_EMAILS = 5

# 已知误判的验证码，需跳过
_EXCLUDED_CODES = {"177010"}


class ImapClient:
    """IMAP 邮箱客户端，兼容 2925.com 等不支持 SEARCH 命令的 IMAP 服务器"""

    def __init__(self, imap_user, imap_pass, imap_server="imap.2925.com", imap_port=993,
                 email_prefix=None):
        """
        初始化 IMAP 客户端

        Args:
            imap_user: IMAP 登录账号（主账号邮箱）
            imap_pass: IMAP 登录密码或授权码
            imap_server: IMAP 服务器地址（默认 imap.2925.com）
            imap_port: IMAP 端口（默认 993，SSL）
            email_prefix: 邮箱前缀（对于 2925 等无限邮箱，固定前缀 + 随机后缀）
                          如果不提供，则从 imap_user 提取（@ 前部分）
        """
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.imap_server = imap_server
        self.imap_port = imap_port

        # 提取邮箱域名（用于生成临时邮箱地址）
        if "@" in imap_user:
            self._domain = imap_user.split("@", 1)[1]
            default_prefix = imap_user.split("@", 1)[0]
        else:
            self._domain = "2925.com"
            default_prefix = imap_user

        self.email_prefix = email_prefix if email_prefix else default_prefix

        # 记录已使用的验证码，防止重复使用
        self._used_codes = set()

    def create_temp_email(self):
        """
        生成临时邮箱地址

        对于 2925 等无限邮箱，格式为：{固定前缀}+{随机后缀}@{域名}
        所有发往该地址的邮件都会投递到主账号收件箱。

        Returns:
            tuple: (email_address, email_address)
        """
        alphabet = string.ascii_lowercase + string.digits
        random_suffix = "".join(secrets.choice(alphabet) for _ in range(6))
        temp_email = f"{self.email_prefix}+{random_suffix}@{self._domain}"
        return temp_email, temp_email

    def extract_verification_code(self, content):
        """从邮件内容中提取 6 位数字验证码"""
        if not content:
            return None

        patterns = [
            r"Verification code:?\s*(\d{6})",
            r"code is\s*(\d{6})",
            r"代码为[:：]?\s*(\d{6})",
            r"验证码[:：]?\s*(\d{6})",
            r">\s*(\d{6})\s*<",
            r"(?<![#&])\b(\d{6})\b",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for code in matches:
                if code in _EXCLUDED_CODES:
                    continue
                return code
        return None

    def wait_for_verification_code(self, target_email, timeout=30, exclude_codes=None):
        """
        登录 IMAP 收件箱，倒序抓取最新邮件提取验证码。
        不使用 SEARCH 命令，兼容 2925.com 等简易 IMAP 服务器。

        Args:
            target_email: 目标邮箱（用于日志，实际登录的是主账号）
            timeout: 最长等待时间（秒）
            exclude_codes: 要排除的验证码集合（避免重复使用）

        Returns:
            str: 6 位验证码，超时则返回 None
        """
        if exclude_codes is None:
            exclude_codes = set()

        all_exclude = exclude_codes | self._used_codes

        print(f"  ⏳ 连接 IMAP ({self.imap_server}) 等待 {target_email} 的验证码...")

        start_time = time.time()

        while time.time() - start_time < timeout:
            mail = None
            try:
                mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
                mail.login(self.imap_user, self.imap_pass)

                # 选择收件箱，data[0] 包含邮件总数
                status, data = mail.select("inbox")

                if status == "OK" and data[0]:
                    try:
                        msg_count = int(data[0].decode("utf-8"))
                    except (ValueError, UnicodeDecodeError) as e:
                        print(f"  ⚠️ 无法解析收件箱邮件数量: {e}")
                        mail.logout()
                        mail = None
                        time.sleep(_EXTENDED_RETRY_INTERVAL)
                        continue

                    if msg_count > 0:
                        # 只检查最新的若干封邮件，从最新一封开始倒序遍历
                        start_idx = max(1, msg_count - (_MAX_RECENT_EMAILS - 1))
                        for i in range(msg_count, start_idx - 1, -1):
                            res_status, msg_data = mail.fetch(str(i), "(RFC822)")
                            if res_status != "OK":
                                continue

                            for response_part in msg_data:
                                if not isinstance(response_part, tuple):
                                    continue

                                msg = email.message_from_bytes(response_part[1])

                                # 解析邮件正文
                                content = ""
                                if msg.is_multipart():
                                    for part in msg.walk():
                                        if part.get_content_type() in (
                                            "text/plain",
                                            "text/html",
                                        ):
                                            payload = part.get_payload(decode=True)
                                            if payload:
                                                content += payload.decode(
                                                    errors="ignore"
                                                )
                                else:
                                    payload = msg.get_payload(decode=True)
                                    if payload:
                                        content = payload.decode(errors="ignore")

                                code = self.extract_verification_code(content)
                                if code and code not in all_exclude:
                                    self._used_codes.add(code)
                                    all_exclude.add(code)
                                    print(f"  ✅ 成功获取验证码: {code}")
                                    mail.logout()
                                    return code

                mail.logout()
                mail = None

            except imaplib.IMAP4.error as e:
                print(f"  ⚠️ IMAP 协议错误: {e}")
                if mail:
                    try:
                        mail.logout()
                    except Exception:
                        pass
                    mail = None
            except Exception as e:
                print(f"  ⚠️ IMAP 连接异常: {e}")
                if mail:
                    try:
                        mail.logout()
                    except Exception:
                        pass
                    mail = None

            # 等待后重试
            elapsed = time.time() - start_time
            sleep_time = (
                _INITIAL_RETRY_INTERVAL
                if elapsed < _EXTENDED_RETRY_THRESHOLD
                else _EXTENDED_RETRY_INTERVAL
            )
            time.sleep(sleep_time)

        print("  ⏰ 获取验证码超时")
        return None


def init_imap_client(config):
    """
    从配置字典初始化 ImapClient

    Args:
        config: 配置字典，需包含 imap_user 和 imap_pass

    Returns:
        ImapClient: 初始化好的客户端实例
    """
    imap_user = config.get("imap_user", "")
    imap_pass = config.get("imap_pass", "")
    imap_server = config.get("imap_server", "imap.2925.com")
    imap_port = int(config.get("imap_port", 993))
    email_prefix = config.get("imap_email_prefix", None)

    if not imap_user or not imap_pass:
        print("❌ 错误: 未配置 IMAP 账号")
        print("   请在 config.json 中设置 imap_user 和 imap_pass")
        sys.exit(1)

    client = ImapClient(
        imap_user=imap_user,
        imap_pass=imap_pass,
        imap_server=imap_server,
        imap_port=imap_port,
        email_prefix=email_prefix,
    )
    print(f"📬 IMAP 客户端已初始化 (服务器: {imap_server}:{imap_port}, 账号: {imap_user})")
    return client

