from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import current_app


class MailDeliveryError(RuntimeError):
    pass


def verification_email_html(code: str, purpose: str = "register") -> str:
    digits = "".join(f'<span style="display:inline-block;min-width:28px">{digit}</span>' for digit in code)
    action = "重置账户密码" if purpose == "reset" else "完成平台注册"
    return f"""<!doctype html>
<html lang="zh-CN">
  <body style="margin:0;background:#f2f0ea;color:#262723;font-family:Arial,'Microsoft YaHei',sans-serif">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="padding:32px 16px">
      <tr><td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:560px;background:#fff;border-top:4px solid #c95d43">
          <tr><td style="padding:30px 34px 14px">
            <div style="font-size:12px;letter-spacing:2px;color:#6c6e67">UESTC AI</div>
            <h1 style="margin:18px 0 8px;font-family:Georgia,'Noto Serif SC',serif;font-size:27px;font-weight:400">验证你的邮箱</h1>
            <p style="margin:0;color:#686a64;font-size:14px;line-height:1.8">使用下面的验证码{action}。验证码在 10 分钟内有效。</p>
          </td></tr>
          <tr><td style="padding:18px 34px">
            <div style="border:1px solid #d7d3ca;background:#f5f3ee;padding:18px 16px;text-align:center;font-family:Consolas,monospace;font-size:30px;letter-spacing:6px;color:#262723">{digits}</div>
          </td></tr>
          <tr><td style="padding:8px 34px 30px">
            <p style="margin:0;color:#7b7d76;font-size:12px;line-height:1.8">如果不是你本人发起，请忽略这封邮件。请勿向任何人提供验证码，平台工作人员不会索要验证码。</p>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""


def send_verification_email(email: str, code: str, purpose: str = "register") -> None:
    if current_app.testing:
        return
    api_key = current_app.config.get("RESEND_API_KEY", "")
    if not api_key:
        raise MailDeliveryError("email delivery is not configured")
    payload = {
        "from": current_app.config["RESEND_FROM"],
        "to": [email],
        "subject": "UESTC AI 密码找回验证码" if purpose == "reset" else "UESTC AI 邮箱验证码",
        "text": f"你的 UESTC AI {'密码找回' if purpose == 'reset' else '注册'}验证码是：{code}\n\n验证码在 10 分钟内有效。请勿向任何人提供验证码。",
        "html": verification_email_html(code, purpose),
    }
    request = Request(
        "https://api.resend.com/emails",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "uestc-ai-platform/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=12) as response:
            if response.status < 200 or response.status >= 300:
                raise MailDeliveryError("email provider rejected the request")
    except HTTPError as error:
        raise MailDeliveryError("email provider rejected the request") from error
    except (URLError, TimeoutError) as error:
        raise MailDeliveryError("email provider is unavailable") from error
