"""
IMAP 邮箱客户端模块（兼容不支持 SEARCH 指令的 IMAP 服务器，如 2925.com）
"""

import time
import email
import imaplib
import random
import re
import string


_MAX_RECENT_MESSAGES = 5  # 每次轮询最多检查的最新邮件数量


class ImapClient:
    """通用 IMAP 邮箱客户端，支持 2925.com 等不实现 SEARCH 指令的服务器"""

    def __init__(self, imap_user, imap_pass, imap_server="imap.2925.com", imap_port=993,
                 email_prefix=None, email_domain="2925.com"):
        """
        初始化 IMAP 客户端

        Args:
            imap_user:      IMAP 登录账号（主账号地址）
            imap_pass:      IMAP 登录密码或授权码
            imap_server:    IMAP 服务器地址（默认 imap.2925.com）
            imap_port:      IMAP 端口（默认 993，SSL）
            email_prefix:   固定前缀；为 None 时从 imap_user 中提取用户名部分
            email_domain:   收信域名（默认 2925.com）
        """
        self.imap_user = imap_user
        self.imap_pass = imap_pass
        self.imap_server = imap_server
        self.imap_port = imap_port
        self.email_domain = email_domain

        # 固定前缀：优先使用传入值，否则取 imap_user 的 @ 前面部分
        if email_prefix:
            self.email_prefix = email_prefix
        elif imap_user and "@" in imap_user:
            self.email_prefix = imap_user.split("@")[0]
        else:
            self.email_prefix = imap_user

        self._used_codes = set()

    def create_temp_email(self):
        """
        生成随机的 2925 无限邮箱地址。

        2925.com 支持"无限别名"：前缀固定，只要加上 +随机后缀 或换用其他子域，
        邮件都会投递到同一个账号。这里采用 <prefix>+<random6>@<domain> 格式。

        Returns:
            tuple: (email_address, email_address)
        """
        suffix = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        temp_email = f"{self.email_prefix}+{suffix}@{self.email_domain}"
        return temp_email, temp_email

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    def _connect(self):
        """建立 IMAP SSL 连接并登录，返回 mail 对象。"""
        mail = imaplib.IMAP4_SSL(self.imap_server, self.imap_port)
        mail.login(self.imap_user, self.imap_pass)
        return mail

    def _parse_email_body(self, raw_bytes):
        """解析原始邮件字节，返回合并后的文本内容。"""
        msg = email.message_from_bytes(raw_bytes)
        content = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() in ("text/plain", "text/html"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        content += payload.decode(errors="ignore")
        else:
            payload = msg.get_payload(decode=True)
            if payload:
                content = payload.decode(errors="ignore")
        return content

    # ------------------------------------------------------------------
    # 公共接口
    # ------------------------------------------------------------------

    def extract_verification_code(self, content):
        """从邮件内容提取 6 位验证码（复用 SkymailClient 的逻辑）。"""
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
                if code == "177010":  # 已知误判
                    continue
                return code
        return None

    def wait_for_verification_code(self, target_email, timeout=60, exclude_codes=None):
        """
        等待验证邮件并提取 6 位验证码。

        不使用 mail.search()（2925.com 不支持 SEARCH 指令）。
        改为：
          1. mail.select("inbox") 获取收件箱邮件总数
          2. 倒序 fetch 最新 5 封邮件
          3. 解析正文，提取验证码

        Args:
            target_email:   注册时使用的收件邮箱地址
            timeout:        最长等待秒数
            exclude_codes:  需排除的验证码集合（避免复用旧码）

        Returns:
            str: 6 位验证码，超时返回 None
        """
        if exclude_codes is None:
            exclude_codes = set()
        all_exclude = exclude_codes | self._used_codes

        print(f"  ⏳ 连接 IMAP ({self.imap_server}) 等待 {target_email} 的验证码...")
        start_time = time.time()

        while time.time() - start_time < timeout:
            try:
                mail = self._connect()

                # 选择收件箱，data[0] 是邮件总数（字节串）
                status, data = mail.select("inbox")

                if status == "OK" and data and data[0]:
                    try:
                        msg_count = int(data[0].decode("utf-8").strip())
                    except (ValueError, AttributeError):
                        mail.logout()
                        time.sleep(3)
                        continue

                    if msg_count > 0:
                        # 只检查最新的 _MAX_RECENT_MESSAGES 封，从最新往旧遍历
                        start_idx = max(1, msg_count - (_MAX_RECENT_MESSAGES - 1))
                        for i in range(msg_count, start_idx - 1, -1):
                            res_status, msg_data = mail.fetch(str(i), "(RFC822)")
                            if res_status != "OK":
                                continue
                            for response_part in msg_data:
                                if not isinstance(response_part, tuple):
                                    continue
                                content = self._parse_email_body(response_part[1])
                                code = self.extract_verification_code(content)
                                if code and code not in all_exclude:
                                    self._used_codes.add(code)
                                    print(f"  ✅ 成功获取验证码: {code}")
                                    mail.logout()
                                    return code

                mail.logout()

            except imaplib.IMAP4.error as e:
                print(f"  ⚠️ IMAP 协议错误: {e}")
            except Exception as e:
                print(f"  ⚠️ IMAP 拉取报错: {e}")

            # 等待 3 秒再次查询
            time.sleep(3)

        print("  ⏰ 获取验证码超时")
        return None


def init_imap_client(config):
    """
    从 config 字典初始化 ImapClient。

    必需字段：
        imap_user   - IMAP 登录账号
        imap_pass   - IMAP 密码或授权码

    可选字段：
        imap_server        - IMAP 服务器（默认 imap.2925.com）
        imap_port          - IMAP 端口（默认 993）
        imap_email_prefix  - 邮箱前缀（默认从 imap_user 提取）
        imap_email_domain  - 收信域名（默认 2925.com）

    Returns:
        ImapClient
    """
    imap_user = config.get("imap_user", "")
    imap_pass = config.get("imap_pass", "")

    if not imap_user or not imap_pass:
        raise ValueError("❌ 错误: 未配置 imap_user / imap_pass，请在 config.json 中设置")

    return ImapClient(
        imap_user=imap_user,
        imap_pass=imap_pass,
        imap_server=config.get("imap_server", "imap.2925.com"),
        imap_port=int(config.get("imap_port", 993)),
        email_prefix=config.get("imap_email_prefix", None),
        email_domain=config.get("imap_email_domain", "2925.com"),
    )
