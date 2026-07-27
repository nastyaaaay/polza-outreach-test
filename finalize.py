"""
Собирает финальный output/leads_final.csv из output/leads.csv:
  - убирает строки без сайта (не удалось найти компанию — не подставляем
    выдуманные данные, просто пропускаем);
  - убирает дубли по названию (компания могла попасть в оба прохода —
    поиск через DuckDuckGo и fallback через 2ГИС);
  - убирает единственный обнаруженный "мусорный" email (служебный
    трекинг-адрес ВКонтакте на групповой странице, не реальный контакт);
  - убирает уточняющие слова, которые добавлялись в companies_seed.py/
    known_domains.py только для точности поиска (например,
    "Qsoft разработка сайтов" -> "Qsoft");
  - чистит текст персонализации: markdown-звёздочки, юникод-мусор
    (неразрывный дефис/узкий пробел) и утёкшие в текст поисковые суффиксы
    названия компании (LLM в generate_personalization() получала на вход
    сырое имя из companies_seed.py вместе с суффиксом и иногда честно
    пересказывала его обратно в тексте персонализации).
"""

import csv
import re
from pathlib import Path

_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")

# Юникод-символы, которые LLM иногда вставляет вместо обычных ascii-аналогов
# (типографский неразрывный дефис, узкий и обычный неразрывный пробел) —
# ломают наивный поиск/подстановку в шаблоне письма и выглядят как мусор
# при копировании в почтовый клиент.
_UNICODE_FIXES = {
    "‑": "-",
    " ": " ",
    " ": " ",
}


def sanitize_personalization(text: str, name_fixes: dict[str, str]) -> str:
    if not text:
        return text
    for raw, clean in name_fixes.items():
        text = text.replace(raw, clean)
    for bad, good in _UNICODE_FIXES.items():
        text = text.replace(bad, good)
    text = _MD_BOLD_RE.sub(r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()

SRC = Path("output/leads.csv")
DST = Path("output/leads_final.csv")

# Уточняющие суффиксы для поиска -> чистое название компании
RENAME = {
    "Depot Branding Agency": "Depot",
    "MGcom рекламное агентство": "MGcom",
    "Родная речь рекламное агентство": "Родная речь",
    "Schmidt Schmidt": "Schmidt Export",
    "Walnut team агентство": "Walnut Team",
    "Rocket media агентство": "Rocket Media",
    "Макс медиа маркетинговое агентство": "Макс медиа",
    "Osminog project автоматизация 1С": "Osminog Project",
    "hubes агентство": "Hubes",
    "Игроник агентство": "Игроник",
    "ПроКонтекст агентство": "ПроКонтекст",
    "Brandmaker Киров": "Brandmaker",
    "Premium-lift рекламная компания": "Premium-lift",
    "Джейкет сервис объявлений": "Джейкет",
    "Victory group маркетинг": "Victory Group",
    "БТЛ Сервис агентство": "БТЛ Сервис",
    "Глобал артс агентство": "Глобал Артс",
    "Тарантул агентство маркетинг": "Тарантул",
    "Социо про исследования": "Социо Про",
    "Servizoria компания": "Servizoria",
    "Litera.Studio графический дизайн": "Litera.Studio",
    "Pragmatix digital SEO": "Pragmatix Digital",
    "Findby SEO продвижение": "Findby",
    "Аудитория агентство Москва": "Аудитория",
    "Упаковщик агентство маркетинг": "Упаковщик",
    "Домовой и Партнеры агентство": "Домовой и Партнеры",
    "DigitalWill агентство": "DigitalWill",
    "Agency-5 агентство": "Agency-5",
    "ArrivoMedia агентство": "ArrivoMedia",
    "Kochev агентство": "Kochev Marketing",
    "Ony агентство Москва": "Ony",
    "Лига трафика агентство": "Лига трафика",
    "Neurobrand агентство": "Neurobrand",
    "Agima интерактивное агентство": "Agima",
    "inSales платформа онлайн-торговли": "inSales",
    "БТВ-Инфо IT-компания": "БТВ-Инфо",
    "Qsoft разработка сайтов": "Qsoft",
}


def main():
    seen = set()
    rows = []
    with open(SRC, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = RENAME.get(row["name"].strip(), row["name"].strip())
            if not row["website"] or name in seen:
                continue
            seen.add(name)
            if row["email"] and "vk-portal.net" in row["email"]:
                row["email"] = ""
            row["name"] = name
            row["personalization"] = sanitize_personalization(row["personalization"], RENAME)
            rows.append(row)

    with open(DST, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "name", "website", "contact_name", "email",
                "personalization", "personalization_source",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"final rows: {len(rows)}")
    print(f"with contact name: {sum(1 for r in rows if r.get('contact_name'))}")
    print(f"with email: {sum(1 for r in rows if r['email'])}")
    print(f"with personalization: {sum(1 for r in rows if r['personalization'])}")


if __name__ == "__main__":
    main()
