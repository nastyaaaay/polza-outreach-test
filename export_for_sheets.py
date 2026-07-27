"""
export_for_sheets.py — готовит CSV-файлы под вставку отдельными листами
в Google Таблицу.

ТЗ по задаче 3: "Оформить в отдельном документе или в той же таблице
отдельным листом". email_sequence.md — исходник цепочки и остаётся
источником правды (править нужно его), а этот скрипт разворачивает его в
таблицу, чтобы проверяющему не приходилось открывать репозиторий.

Делает два файла:
  output/sheet_sequence.csv — сама цепочка: письмо, когда отправлять,
    тема, тело. Это лист "Цепочка писем".
  output/sheet_emails.csv   — 51 собранное письмо с подставленными
    именем и персонализацией. Это лист "Готовые письма"; сверх ТЗ, но
    показывает, что цепочка стыкуется с базой.

Запуск:
    python export_for_sheets.py
"""

import csv
import re
from pathlib import Path

from build_emails import OUTPUT_PATH as EMAILS_PATH
from build_emails import TEMPLATE_PATH, parse_template

SEQUENCE_DST = Path(__file__).parent / "output" / "sheet_sequence.csv"
EMAILS_DST = Path(__file__).parent / "output" / "sheet_emails.csv"
CHECKED_SRC = Path(__file__).parent / "output" / "polza_checked.csv"
CHECKED_DST = Path(__file__).parent / "output" / "sheet_polza_checked.csv"

# Лист по задаче 4. Порядок колонок не как в исходном CSV: сначала то, что
# дал клиент, сразу за ним — что именно не сошлось (главный результат
# задачи, его должно быть видно без прокрутки), и только потом как
# проверяли и что получилось.
CHECKED_COLUMNS = {
    "name": "Компания",
    "email": "Email из базы",
    "site_in_base": "Сайт из базы",
    "issues": "Что не сошлось",
    "verdict": "Сайт принадлежит этой компании?",
    "site_owner": "Кому принадлежит сайт по факту",
    "site_status": "Статус сайта из базы",
    "site_used": "По какому сайту строили персонализацию",
    "site_source": "Откуда взяли этот сайт",
    "industry": "Отрасль",
    "niche": "Ниша базы",
    "language": "Язык письма",
    "personalization": "Персонализация",
    "personalization_source": "Источник персонализации",
}

# Заголовки листа с готовыми письмами: emails_ready.csv машинный
# (subject_1/body_1), а лист читают глазами.
EMAIL_COLUMNS = {
    "name": "Компания",
    "email": "Email",
    "subject_1": "Тема 1",
    "body_1": "Письмо 1",
    "subject_2": "Тема 2",
    "body_2": "Письмо 2",
    "subject_3": "Тема 3",
    "body_3": "Письмо 3",
}


def extract_schedule(md_text: str) -> list[str]:
    """Тайминг отправки берём из заголовков самого шаблона, а не хардкодим
    рядом: иначе при правке цепочки они разъедутся."""
    titles = re.findall(r"^## Письмо \d+ — (.+)$", md_text, re.M)
    schedule = []
    for title in titles:
        if "3 дня" in title:
            schedule.append("через 3 дня после письма 1")
        elif "5 дней" in title:
            schedule.append("через 5 дней после письма 2")
        else:
            schedule.append("день 0 — первое касание")
    return schedule


def main() -> None:
    md_text = TEMPLATE_PATH.read_text(encoding="utf-8")
    letters = parse_template(md_text)
    schedule = extract_schedule(md_text)

    with open(SEQUENCE_DST, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Письмо", "Когда отправлять", "Тема", "Тело"])
        for i, letter in enumerate(letters, start=1):
            writer.writerow([
                f"Письмо {i}",
                schedule[i - 1] if i <= len(schedule) else "",
                letter["subject"],
                letter["body"],
            ])

    with open(EMAILS_PATH, encoding="utf-8") as src, open(
        EMAILS_DST, "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=list(EMAIL_COLUMNS.values()))
        writer.writeheader()
        count = 0
        for row in reader:
            writer.writerow({ru: row.get(en, "") for en, ru in EMAIL_COLUMNS.items()})
            count += 1

    with open(CHECKED_SRC, encoding="utf-8") as src, open(
        CHECKED_DST, "w", newline="", encoding="utf-8"
    ) as dst:
        reader = csv.DictReader(src)
        writer = csv.DictWriter(dst, fieldnames=list(CHECKED_COLUMNS.values()))
        writer.writeheader()
        checked = 0
        issues = 0
        for row in reader:
            writer.writerow({ru: row.get(en, "") for en, ru in CHECKED_COLUMNS.items()})
            checked += 1
            issues += bool(row.get("issues"))

    print(f"{SEQUENCE_DST.name}: {len(letters)} письма (лист «Цепочка писем»)")
    print(f"{EMAILS_DST.name}: {count} строк (лист «Готовые письма»)")
    print(
        f"{CHECKED_DST.name}: {checked} строк, из них с замечаниями {issues} "
        "(лист «Задача 4 — проверка базы»)"
    )


if __name__ == "__main__":
    main()
