from __future__ import annotations

from email.message import EmailMessage
from html import escape

import aiosmtplib

from schemas.lead import Lead

PRIORITY_STYLE = {
    "High": ("🔥", "#dc2626", "#fef2f2"),
    "Medium": ("⚡", "#d97706", "#fffbeb"),
    "Low": ("💤", "#2563eb", "#eff6ff"),
}


def build_subject(lead: Lead) -> str:
    icon = PRIORITY_STYLE[lead.lead_priority][0]
    return (
        f"{icon} [{lead.lead_priority} Priority] Новая заявка от "
        f"{lead.client_name} ({lead.category})"
    )


def build_text(lead: Lead) -> str:
    return "\n".join(
        [
            f"Новая заявка — приоритет {lead.lead_priority}",
            "",
            f"Имя: {lead.client_name}",
            f"Контакты: {lead.contact_info}",
            f"Telegram: {lead.username_display} (ID {lead.telegram_id})",
            f"Категория: {lead.category}",
            f"Бюджет/сроки: {lead.budget_or_timeline}",
            f"Задача: {lead.project_description}",
            "",
            f"AI-саммари: {lead.ai_summary}",
            f"Дата: {lead.created_at:%Y-%m-%d %H:%M %Z}",
        ]
    )


def build_html(lead: Lead) -> str:
    icon, color, bg = PRIORITY_STYLE[lead.lead_priority]
    e = escape
    rows = [
        ("Имя", lead.client_name),
        ("Контакты", lead.contact_info),
        ("Telegram", f"{lead.username_display} (ID {lead.telegram_id})"),
        ("Категория", lead.category),
        ("Бюджет / сроки", lead.budget_or_timeline),
        ("Задача", lead.project_description),
        ("Дата", f"{lead.created_at:%Y-%m-%d %H:%M %Z}"),
    ]
    rows_html = "".join(
        f'<tr><td style="padding:8px 12px;color:#6b7280;width:140px;vertical-align:top;">'
        f"{e(k)}</td>"
        f'<td style="padding:8px 12px;color:#111827;">{e(v)}</td></tr>'
        for k, v in rows
    )
    return f"""\
<!doctype html>
<html><body style="margin:0;padding:24px;background:#f3f4f6;font-family:Arial,Helvetica,sans-serif;">
<table role="presentation" width="100%" style="max-width:600px;margin:0 auto;background:#ffffff;border-radius:12px;overflow:hidden;border:1px solid #e5e7eb;">
  <tr><td style="padding:20px 24px;background:{color};color:#ffffff;">
    <div style="font-size:13px;opacity:.9;">AI Lead Intake Bot</div>
    <div style="font-size:20px;font-weight:bold;margin-top:4px;">{icon} Новая заявка — {e(lead.lead_priority)} priority</div>
  </td></tr>
  <tr><td style="padding:20px 24px;">
    <div style="background:{bg};border-left:4px solid {color};padding:12px 16px;border-radius:6px;color:#111827;">
      <div style="font-size:12px;color:#6b7280;margin-bottom:4px;">AI-саммари</div>
      {e(lead.ai_summary)}
    </div>
    <table role="presentation" width="100%" style="margin-top:16px;border-collapse:collapse;font-size:14px;">
      {rows_html}
    </table>
  </td></tr>
  <tr><td style="padding:12px 24px;background:#f9fafb;color:#9ca3af;font-size:12px;">
    Статус: {e(lead.status)} · отправлено автоматически
  </td></tr>
</table>
</body></html>"""


def build_message(lead: Lead, sender: str, recipient: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = build_subject(lead)
    msg["From"] = sender
    msg["To"] = recipient
    msg.set_content(build_text(lead))
    msg.add_alternative(build_html(lead), subtype="html")
    return msg


class EmailService:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        sender: str,
        recipient: str,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._recipient = recipient

    async def send_lead(self, lead: Lead) -> None:
        implicit_tls = self._port == 465
        await aiosmtplib.send(
            build_message(lead, self._sender, self._recipient),
            hostname=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            use_tls=implicit_tls,
            start_tls=not implicit_tls,
            timeout=30,
        )
