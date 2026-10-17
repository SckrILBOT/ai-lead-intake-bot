import json
from datetime import datetime, timezone

from schemas.lead import Lead
from services.email_service import build_html, build_message, build_subject
from services.lead_dispatcher import BackupStorage, LeadDispatcher
from services.sheets_service import (
    COLUMN_WIDTHS,
    HEADERS,
    PRIORITY_COLORS,
    SheetsService,
    build_layout_requests,
    lead_to_row,
)
from tests.conftest import VALID_LEAD


def make_lead(**overrides) -> Lead:
    return Lead(
        **(VALID_LEAD | overrides),
        created_at=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
        telegram_id=42,
        telegram_username="anna",
    )


class OkSheets:
    def __init__(self):
        self.leads = []

    async def append_lead(self, lead):
        self.leads.append(lead)


class BrokenEmail:
    async def send_lead(self, lead):
        raise ConnectionError("smtp down")


async def test_mock_mode_writes_backup(tmp_path):
    path = tmp_path / "leads_backup.json"
    result = await LeadDispatcher(BackupStorage(path)).dispatch(make_lead())

    assert result.saved and result.backed_up
    assert result.delivered == []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data[0]["client_name"] == "Анна"
    assert len(data[0]["fallback_reasons"]) == 2

    await LeadDispatcher(BackupStorage(path)).dispatch(make_lead(client_name="Олег"))
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 2


async def test_failed_channel_falls_back_without_blocking_others(tmp_path):
    sheets = OkSheets()
    path = tmp_path / "b.json"
    result = await LeadDispatcher(
        BackupStorage(path), sheets=sheets, email=BrokenEmail()
    ).dispatch(make_lead())

    assert result.delivered == ["sheets"]
    assert result.backed_up
    assert "email ошибка: ConnectionError" in result.fallback_reasons
    assert len(sheets.leads) == 1


def test_sheet_row_matches_headers():
    row = lead_to_row(make_lead())
    assert len(row) == len(HEADERS)
    assert row[1] == "@anna" and row[-1] == "New" and row[-2] == "High"


def test_email_subject_and_escaping():
    lead = make_lead(client_name="<script>Ева</script>", lead_priority="Medium")
    assert build_subject(lead).startswith("⚡ [Medium Priority] Новая заявка от")
    html = build_html(lead)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html

    msg = build_message(make_lead(), "bot@example.com", "admin@example.com")
    assert msg["Subject"].startswith("🔥 [High Priority]")
    assert msg.get_body(("html",)) is not None


class FakeSpreadsheet:
    def __init__(self, fail=False):
        self.bodies = []
        self.fail = fail

    def batch_update(self, body):
        if self.fail:
            raise RuntimeError("api down")
        self.bodies.append(body)


class FakeWorksheet:
    id = 7

    def __init__(self, frozen=0, fail=False):
        self.frozen_row_count = frozen
        self.spreadsheet = FakeSpreadsheet(fail)


def test_layout_covers_every_column_and_priority():
    requests = build_layout_requests(sheet_id=7)
    kinds = [next(iter(r)) for r in requests]
    assert COLUMN_WIDTHS and len(COLUMN_WIDTHS) == len(HEADERS)
    assert kinds.count("updateDimensionProperties") == len(HEADERS)
    assert kinds.count("addConditionalFormatRule") == len(PRIORITY_COLORS)
    assert {"setBasicFilter", "updateSheetProperties"} <= set(kinds)
    assert all(
        r["addConditionalFormatRule"]["rule"]["booleanRule"]["condition"]["type"] == "TEXT_EQ"
        for r in requests
        if "addConditionalFormatRule" in r
    )


def test_layout_applied_once_and_never_raises():
    fresh = FakeWorksheet(frozen=0)
    SheetsService._apply_layout(fresh)
    assert len(fresh.spreadsheet.bodies) == 1

    styled = FakeWorksheet(frozen=1)
    SheetsService._apply_layout(styled)
    assert styled.spreadsheet.bodies == []

    SheetsService._apply_layout(FakeWorksheet(frozen=0, fail=True))
