from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from schemas.lead import Lead
from services.email_service import EmailService
from services.sheets_service import SheetsService

logger = logging.getLogger(__name__)


class BackupStorage:
    """Append-only JSON array file, written atomically."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()

    def _append_sync(self, record: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data: list = []
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8") or "[]")
            except json.JSONDecodeError:
                broken = self._path.with_suffix(".broken.json")
                self._path.replace(broken)
                logger.error("Backup file was corrupted, moved to %s", broken)
        data.append(record)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self._path)

    async def append(self, record: dict) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append_sync, record)


def format_lead_log(lead: Lead, reasons: list[str]) -> str:
    lines = [
        ("Приоритет", lead.lead_priority),
        ("Категория", lead.category),
        ("Имя", lead.client_name),
        ("Контакты", lead.contact_info),
        ("Telegram", f"{lead.username_display} ({lead.telegram_id})"),
        ("Бюджет/сроки", lead.budget_or_timeline),
        ("Задача", lead.project_description),
        ("AI-саммари", lead.ai_summary),
    ]
    width = 72
    out = ["╔" + "═" * width, f"║ 📥 НОВЫЙ ЛИД (fallback: {', '.join(reasons)})", "╟" + "─" * width]
    out += [f"║ {k:<13}: {v}" for k, v in lines]
    out.append("╚" + "═" * width)
    return "\n".join(out)


@dataclass
class DispatchResult:
    delivered: list[str] = field(default_factory=list)
    fallback_reasons: list[str] = field(default_factory=list)
    backed_up: bool = False

    @property
    def saved(self) -> bool:
        return bool(self.delivered) or self.backed_up


class LeadDispatcher:
    def __init__(
        self,
        backup: BackupStorage,
        sheets: SheetsService | None = None,
        email: EmailService | None = None,
    ) -> None:
        self._backup = backup
        self._sheets = sheets
        self._email = email

    async def dispatch(self, lead: Lead) -> DispatchResult:
        result = DispatchResult()
        channels: dict[str, object] = {}
        if self._sheets:
            channels["sheets"] = self._sheets.append_lead(lead)
        else:
            result.fallback_reasons.append("sheets не настроен")
        if self._email:
            channels["email"] = self._email.send_lead(lead)
        else:
            result.fallback_reasons.append("email не настроен")

        outcomes = await asyncio.gather(*channels.values(), return_exceptions=True)
        for name, outcome in zip(channels, outcomes):
            if isinstance(outcome, BaseException):
                logger.error("Channel %s failed: %r", name, outcome)
                result.fallback_reasons.append(f"{name} ошибка: {type(outcome).__name__}")
            else:
                result.delivered.append(name)

        if result.fallback_reasons:
            logger.info("\n%s", format_lead_log(lead, result.fallback_reasons))
            try:
                await self._backup.append(
                    lead.model_dump(mode="json")
                    | {"fallback_reasons": result.fallback_reasons}
                )
                result.backed_up = True
            except Exception:  # noqa: BLE001
                logger.exception("Failed to write lead backup")
        return result
