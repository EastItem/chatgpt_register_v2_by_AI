"""
IMAP 邮箱客户端模块（支持 2925 无限别名邮箱）
"""

import time
import imaplib
import email
import random
import re
import string

# 轮询间隔（秒）：前 10 秒快速轮询，之后降速
_POLL_FAST_INTERVAL = 0.5
_POLL_SLOW_INTERVAL = 2
_POLL_FAST_DURATION = 10

# OpenAI 验证码邮件中已知的误判代码（非验证码的 6 位数字）
_FALSE_POSITIVE_CODES = {"177010"}

# SEARCH 命令不可用时，按序号最多获取最近几封邮件
_FALLBACK_FETCH_LIMIT = 50


class ImapClient:
    """通过 IMAP 协议收取验证码的邮箱客户端，支持 2925 无限别名邮箱"""

    def __init__(
        self,
        imap_user,
        imap_pass,
        imap_server="imap.2925.com",
        imap_port=993,
        email_prefix="",
        email_domain="2925.com",
    ):
        """
        初始化 IMAP 客户端

        Args:
            imap_user: IMAP 登录账号（主账号）
            imap_pass: IMAP 登录密码
            imap_server: IMAP 服务器地址（默认 imap.2925.com）
            imap_port: IMAP 端口（默认 993，SSL）
            email_prefix: 固定前缀（例如 "myprefix"），留空则使用 imap_user 的用户名部分
            email_domain: 邮件域名（默认 2925.com）
        """
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.imap_server = imap_server
        self.imap_port = imap_port
        self.email_domain = email_domain
        self._used_codes = set()

        # 如果未指定 email_prefix，则从 imap_user 中提取用户名部分
        if email_prefix:
            self.email_prefix = email_prefix
        elif imap_user and "@" in imap_user:
            self.email_prefix = imap_user.split("@")[0]
        else:
            self.email_prefix = imap_user or ""

        if not self.email_prefix:
            raise ValueError(
                "无法确定邮件前缀：请通过 email_prefix 参数或包含 '@' 的 imap_user 来指定"
            )

    def create_temp_email(self):
        """
        生成一个 2925 无限别名邮箱地址。
        格式：{prefix}+{random_suffix}@{domain}

        Returns:
            tuple: (email_address, email_address)
        """
        random_suffix = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=8)
        )
        new_email = f"{self.email_prefix}+{random_suffix}@{self.email_domain}"
        return new_email, new_email

    def _connect(self):
        """建立 IMAP SSL 连接并登录，返回 imaplib.IMAP4_SSL 实例"""
        mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
        mail.login(self.imap_user, self.imap_pass)
        return mail

    def _get_email_body(self, msg):
        """从 email.message.Message 对象中提取纯文本内容"""
        content = ""
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if content_type in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        content += payload.decode(charset, errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="ignore")
        return content

    def extract_verification_code(self, content):
        """从邮件内容中提取 6 位验证码（复用 skymail_client 的正则模式）"""
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
                if code in _FALSE_POSITIVE_CODES:
                    continue
                return code
        return None

    def _search_unseen_to(self, mail, safe_email, inbox_count=0):
        """
        尝试用 UNSEEN TO 搜索邮件。
        部分 IMAP 服务器（如 imap.2925.com）不支持 TO 搜索条件，此时回退到
        仅搜索 UNSEEN，再在 Python 侧按收件人过滤。
        若 SEARCH 命令完全不被服务器识别，则最终回退到按序号获取最近邮件。

        Args:
            mail: 已完成 SELECT 的 IMAP 连接实例
            safe_email: 已清理的目标邮箱地址
            inbox_count: 当前收件箱邮件总数（来自 SELECT 响应），用于 SEARCH 不可用时的回退

        Returns:
            tuple: (mail_ids_bytes_list, use_to_filter)
                mail_ids_bytes_list: 邮件 ID 列表（bytes）
                use_to_filter: True 表示需要在 Python 侧过滤收件人
        """
        try:
            status, data = mail.search(None, f'(UNSEEN TO "{safe_email}")')
            if status == "OK":
                return (data[0].split() if data and data[0] else []), False
        except imaplib.IMAP4.error as e:
            print(f"  ⚠️ IMAP SEARCH TO 不支持，回退到 UNSEEN 搜索: {e}")

        # 回退 1：服务器不支持 TO 搜索条件，改为只搜索 UNSEEN
        try:
            status, data = mail.search(None, "UNSEEN")
            if status == "OK" and data and data[0]:
                return data[0].split(), True
        except imaplib.IMAP4.error as e:
            print(f"  ⚠️ IMAP SEARCH UNSEEN 不支持，回退到 ALL 搜索: {e}")

        # 回退 2：尝试搜索全部邮件
        try:
            status, data = mail.search(None, "ALL")
            if status == "OK" and data and data[0]:
                return data[0].split(), True
        except imaplib.IMAP4.error as e:
            print(f"  ⚠️ IMAP SEARCH ALL 不支持，回退到按序号获取邮件: {e}")

        # 回退 3：SEARCH 命令完全不可用，按序号获取最近 _FALLBACK_FETCH_LIMIT 封邮件
        if inbox_count > 0:
            start = max(1, inbox_count - _FALLBACK_FETCH_LIMIT + 1)
            ids = [str(i).encode() for i in range(start, inbox_count + 1)]
            return ids, True

        return [], True

    def wait_for_verification_code(self, target_email, timeout=30, exclude_codes=None):
        """
        连接 IMAP 服务器，轮询收件箱，查找发送给 target_email 的未读邮件并提取验证码。

        Args:
            target_email: 目标别名邮箱地址（用于 TO 搜索）
                          注意：地址中的 '+' 是 RFC 5321 合法字符（子地址别名），
                          但部分 IMAP 服务器的 SEARCH TO 条件不支持含 '+' 的地址，
                          因此本方法在 SEARCH 失败时会自动回退到 Python 侧过滤。
            timeout: 超时时间（秒）
            exclude_codes: 要排除的验证码集合（避免重复使用旧验证码）

        Returns:
            str: 6 位验证码，超时或失败返回 None
        """
        if exclude_codes is None:
            exclude_codes = set()

        all_exclude = exclude_codes | self._used_codes

        print(f"  ⏳ 连接 IMAP ({self.imap_server}) 等待 {target_email} 的验证码...")

        start = time.time()
        seen_ids = set()

        while time.time() - start < timeout:
            try:
                mail = self._connect()
                select_status, select_data = mail.select("INBOX")
                inbox_count = 0
                if select_status == "OK" and select_data and select_data[0]:
                    try:
                        inbox_count = int(select_data[0])
                    except (ValueError, TypeError):
                        pass

                # 对 target_email 做基本清理，防止 IMAP 搜索字符串注入
                safe_email = target_email.replace('"', "").replace("\\", "")
                mail_ids, use_to_filter = self._search_unseen_to(mail, safe_email, inbox_count)

                for mid in mail_ids:
                    if mid in seen_ids:
                        continue
                    seen_ids.add(mid)

                    _, msg_data = mail.fetch(mid, "(RFC822)")
                    for response_part in msg_data:
                        if not isinstance(response_part, tuple):
                            continue
                        msg = email.message_from_bytes(response_part[1])

                        # 当服务器不支持 TO 搜索时，在 Python 侧过滤收件人
                        if use_to_filter:
                            to_headers = [
                                msg.get("To", ""),
                                msg.get("Delivered-To", ""),
                                msg.get("X-Original-To", ""),
                            ]
                            if not any(target_email.lower() in h.lower() for h in to_headers):
                                continue

                        content = self._get_email_body(msg)
                        code = self.extract_verification_code(content)
                        if code and code not in all_exclude:
                            self._used_codes.add(code)
                            print(f"  ✅ 验证码: {code}")
                            mail.logout()
                            return code

                mail.logout()
            except imaplib.IMAP4.error as e:
                print(f"  ⚠️ IMAP 协议错误: {e}")
            except OSError as e:
                print(f"  ⚠️ IMAP 网络连接错误: {e}")
            except Exception as e:
                print(f"  ⚠️ IMAP 检查异常 ({type(e).__name__}): {e}")

            elapsed = time.time() - start
            time.sleep(_POLL_FAST_INTERVAL if elapsed < _POLL_FAST_DURATION else _POLL_SLOW_INTERVAL)

        print("  ⏰ 获取验证码超时")
        return None
