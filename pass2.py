"""
Проход по компаниям с уже известным доменом (known_domains.py): пропускаем
поиск и сразу идём fetch -> email -> имя контакта -> LLM-персонализация
через ту же process_company() из collect_leads.py, только с готовым
website вместо search_website(). Результат дописывается в output/leads.csv.

Поскольку в known_domains.py теперь собраны ВСЕ найденные домены, этого
скрипта достаточно для полного воспроизведения базы — без обращений к
поисковику и без зависимости от его капчи. Если output/leads.csv не
существует или пуст, заголовок пишется здесь же.
"""

import csv
import time

import httpx

from collect_leads import OUTPUT_PATH, process_company
from known_domains import KNOWN_DOMAINS


def _already_processed(path) -> set[str]:
    """Имена, уже записанные в output/leads.csv — если pass2.py запустить
    повторно (например, после сбоя на середине), дописывание "a" без этой
    проверки задвоило бы строки для уже обработанных компаний."""
    if not path.exists():
        return set()
    with open(path, encoding="utf-8") as f:
        return {row["name"] for row in csv.DictReader(f)}


def main():
    fieldnames = [
        "name", "website", "contact_name", "email",
        "personalization", "personalization_source",
    ]
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    needs_header = not OUTPUT_PATH.exists() or OUTPUT_PATH.stat().st_size == 0
    done = _already_processed(OUTPUT_PATH)
    items = [(name, site) for name, site in KNOWN_DOMAINS.items() if name not in done]
    stats = {"llm_failures": 0}

    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f, httpx.Client() as client:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()

        for i, (name, website) in enumerate(items, start=1):
            print(f"[{i}/{len(items)}] {name} ...", end=" ", flush=True)
            failures_before = stats["llm_failures"]
            row = process_company(client, name, stats, website=website)
            writer.writerow(row)
            f.flush()
            if stats["llm_failures"] > failures_before:
                print("LLM недоступна — без персонализации")
            else:
                print("OK" if row["email"] or row["personalization"] else "частично")
            time.sleep(0.5)

    if stats["llm_failures"]:
        print(
            f"\n{stats['llm_failures']} из {len(items)} строк — без "
            "персонализации из-за ошибки LLM"
        )


if __name__ == "__main__":
    main()
