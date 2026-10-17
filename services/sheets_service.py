from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

from schemas.lead import Lead

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

HEADERS = [
    "Дата/Время",
    "Telegram @username",
    "Telegram ID",
    "Имя",
    "Контакты",
    "Категория",
    "Краткое саммари (AI)",
    "Бюджет/Сроки",
    "Приоритет",
    "Статус",
]


# Column widths in pixels, in the same order as HEADERS.
COLUMN_WIDTHS = [150, 140, 110, 130, 200, 130, 380, 180, 100, 90]

PRIORITY_COLORS = {
    "High": {"red": 0.96, "green": 0.78, "blue": 0.76},
    "Medium": {"red": 1.0, "green": 0.95, "blue": 0.70},
    "Low": {"red": 0.88, "green": 0.88, "blue": 0.88},
}
PRIORITY_COLUMN = HEADERS.index("Приоритет")
HEADER_BG = {"red": 0.17, "green": 0.24, "blue": 0.31}


def build_layout_requests(sheet_id: int) -> list[dict]:
    """batchUpdate requests: widths, wrapping, header style, freeze, filter, priority colors."""
    n = len(HEADERS)
    requests: list[dict] = []

    for i, width in enumerate(COLUMN_WIDTHS[:n]):
        requests.append(
            {
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1,
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize",
                }
            }
        )

    requests.append(  # data rows: wrap long text, align to the top
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 1, "startColumnIndex": 0, "endColumnIndex": n},
                "cell": {
                    "userEnteredFormat": {"wrapStrategy": "WRAP", "verticalAlignment": "TOP"}
                },
                "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)",
            }
        }
    )
    requests.append(  # header
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": n,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": HEADER_BG,
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                        },
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "WRAP",
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
            }
        }
    )
    requests.append(
        {
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            }
        }
    )
    requests.append(
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endColumnIndex": n,
                    }
                }
            }
        }
    )
    for index, (priority, color) in enumerate(PRIORITY_COLORS.items()):
        requests.append(
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [
                            {
                                "sheetId": sheet_id,
                                "startRowIndex": 1,
                                "startColumnIndex": PRIORITY_COLUMN,
                                "endColumnIndex": PRIORITY_COLUMN + 1,
                            }
                        ],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": priority}],
                            },
                            "format": {"backgroundColor": color, "textFormat": {"bold": True}},
                        },
                    },
                    "index": index,
                }
            }
        )
    return requests


def load_credentials(source: str) -> Credentials:
    """`source` is either a path to the key file or the JSON content itself."""
    source = source.strip()
    if source.startswith("{"):
        info = json.loads(source)
    else:
        info = json.loads(Path(source).read_text(encoding="utf-8"))
    return Credentials.from_service_account_info(info, scopes=SCOPES)


def lead_to_row(lead: Lead) -> list[str]:
    return [
        lead.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        lead.username_display,
        str(lead.telegram_id),
        lead.client_name,
        lead.contact_info,
        lead.category,
        lead.ai_summary,
        lead.budget_or_timeline,
        lead.lead_priority,
        lead.status,
    ]


class SheetsService:
    def __init__(
        self, credentials_source: str, spreadsheet_id: str, worksheet_name: str = "Leads"
    ) -> None:
        self._credentials_source = credentials_source
        self._spreadsheet_id = spreadsheet_id
        self._worksheet_name = worksheet_name
        self._worksheet: gspread.Worksheet | None = None
        self._lock = asyncio.Lock()

    def _get_worksheet(self) -> gspread.Worksheet:
        if self._worksheet is None:
            client = gspread.authorize(load_credentials(self._credentials_source))
            spreadsheet = client.open_by_key(self._spreadsheet_id)
            try:
                ws = spreadsheet.worksheet(self._worksheet_name)
            except gspread.WorksheetNotFound:
                ws = spreadsheet.add_worksheet(
                    self._worksheet_name, rows=1000, cols=len(HEADERS)
                )
            self._apply_layout(ws)
            self._worksheet = ws
        return self._worksheet

    @staticmethod
    def _apply_layout(ws: gspread.Worksheet) -> None:
        """Style the sheet once; a frozen header row marks it as already styled."""
        if ws.frozen_row_count >= 1:
            return
        try:
            ws.spreadsheet.batch_update({"requests": build_layout_requests(ws.id)})
        except Exception:
            # Cosmetics must never block delivering a lead.
            logger.warning("Could not style the worksheet", exc_info=True)

    def _append_sync(self, lead: Lead) -> None:
        ws = self._get_worksheet()
        if not any(cell.strip() for cell in ws.row_values(1)):
            ws.update([HEADERS], "A1", value_input_option="RAW")
        # RAW: user text like "=HYPERLINK(...)" is stored as text, not a formula
        ws.append_row(lead_to_row(lead), value_input_option="RAW")

    async def append_lead(self, lead: Lead) -> None:
        async with self._lock:
            await asyncio.to_thread(self._append_sync, lead)
