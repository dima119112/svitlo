#!/usr/bin/env python3
"""Fetch today's published outage schedule for queue 4.1 from Zhytomyroblenergo."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

URL = "https://www.ztoe.com.ua/unhooking-search.php"
OUT = Path("schedule.json")
TZ = ZoneInfo("Europe/Kyiv")


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_red(cell) -> bool:
    """Interpret red schedule cells as planned disconnection, per operator legend."""
    attrs = " ".join([
        str(cell.get("bgcolor", "")),
        str(cell.get("style", "")),
        str(cell.get("class", "")),
    ]).lower().replace(" ", "")
    # Explicit common CSS forms; don't infer outage from arbitrary text.
    red_markers = (
        "red", "#f00", "#ff0000", "rgb(255,0,0)",
        "#f44336", "#e60000", "#ff3333", "#dc3545",
    )
    return any(marker in attrs for marker in red_markers)


def is_white(cell) -> bool:
    attrs = " ".join([
        str(cell.get("bgcolor", "")),
        str(cell.get("style", "")),
        str(cell.get("class", "")),
    ]).lower().replace(" ", "")
    return any(marker in attrs for marker in ("white", "#fff", "#ffffff", "rgb(255,255,255)"))


def parse():
    response = requests.get(
        URL,
        timeout=30,
        headers={"User-Agent": "Mozilla/5.0 (compatible; svitlo-schedule/1.0)"},
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    # Find the schedule table by its header labels, then the row for queue 4 / subqueue 4.1.
    target_table = None
    for table in soup.find_all("table"):
        table_text = norm(table.get_text(" ", strip=True))
        if "Підчерга" in table_text and re.search(r"00:00-00:30", table_text):
            target_table = table
            break
    if target_table is None:
        raise RuntimeError("Не вдалося знайти таблицю погодинних відключень на офіційному сайті.")

    target_row = None
    for row in target_table.find_all("tr"):
        cells = row.find_all(["td", "th"], recursive=False)
        if len(cells) >= 3:
            first = norm(cells[0].get_text(" ", strip=True))
            second = norm(cells[1].get_text(" ", strip=True))
            if first == "4" and second == "4.1":
                target_row = cells
                break
    if target_row is None:
        raise RuntimeError("У таблиці не знайдено рядок підчерги 4.1.")

    # Most versions of the operator table have queue and subqueue columns followed by 48 half-hour cells.
    slots = target_row[2:]
    if len(slots) < 48:
        raise RuntimeError(f"Очікувалося 48 півгодинних інтервалів, знайдено {len(slots)}.")

    periods = []
    for i, cell in enumerate(slots[:48]):
        start_minutes = i * 30
        end_minutes = (i + 1) * 30
        start = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
        end = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
        if is_red(cell):
            state = "off"
        elif is_white(cell):
            state = "on"
        else:
            # If the markup changes or the cell's color cannot be interpreted,
            # fail closed rather than publish a guessed schedule.
            raise RuntimeError(f"Не вдалося визначити колір інтервалу {start}–{end}.")

        if periods and periods[-1]["state"] == state:
            periods[-1]["end"] = end
        else:
            periods.append({"start": start, "end": end, "state": state})

    # Extract date from the schedule heading, usually shown as DD.MM.YYYY.
    text = soup.get_text(" ", strip=True)
    matches = re.findall(r"(?:Черга\s+Підчерга\s+)?(\d{2})\.(\d{2})\.(\d{4})", text)
    date_iso = None
    if matches:
        # Prefer today's date if it appears in the page; otherwise use the most recent date found.
        dates = [f"{y}-{m}-{d}" for d, m, y in matches]
        today = datetime.now(TZ).date().isoformat()
        date_iso = today if today in dates else dates[-1]

    if not date_iso:
        raise RuntimeError("Не вдалося визначити дату графіка.")

    today = datetime.now(TZ).date().isoformat()
    if date_iso != today:
        raise RuntimeError(f"Сторінка містить графік на {date_iso}, а сьогодні {today}; не публікую застарілі дані.")

    return {
        "status": "ok",
        "date": date_iso,
        "updated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "source": URL,
        "queue": "4.1",
        "periods": periods,
        "message": None,
    }


def main():
    try:
        data = parse()
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Оновлено schedule.json: {data['date']}, {len(data['periods'])} об'єднаних інтервалів.")
    except Exception as exc:
        print(f"Помилка отримання графіка: {exc}", file=sys.stderr)
        # Do not overwrite a previously valid schedule with guessed data.
        sys.exit(1)


if __name__ == "__main__":
    main()
