"""
Второй проход: для компаний, чьи сайты уже найдены (через 2ГИС или известны
напрямую), пропускаем поиск и сразу идём fetch -> email -> LLM-персонализация.
Результат дописывается в output/leads.csv.
"""

import csv
import time

import httpx

from collect_leads import (
    OUTPUT_PATH,
    extract_context_text,
    extract_contact_name,
    extract_email,
    fetch,
    find_contact_page,
    generate_personalization,
)
from known_domains import KNOWN_DOMAINS


def process_known(client: httpx.Client, name: str, website: str, stats: dict | None = None) -> dict:
    row = {
        "name": name,
        "website": website,
        "contact_name": "",
        "email": "",
        "personalization": "",
        "personalization_source": "",
    }

    domain = httpx.URL(website).host or ""
    home_html = fetch(client, website)
    if not home_html:
        return row

    email = extract_email(home_html, domain)
    context = extract_context_text(home_html)
    source_url = website

    contact_url = find_contact_page(website, home_html)
    contact_html = fetch(client, contact_url) if contact_url else None
    if contact_html:
        if not email:
            email = extract_email(contact_html, domain)
        if not context:
            context = extract_context_text(contact_html)
            source_url = contact_url

    contact_name = extract_contact_name(client, contact_html, stats) if contact_html else ""
    if not contact_name:
        contact_name = extract_contact_name(client, home_html, stats)

    row["email"] = email or ""
    row["contact_name"] = contact_name
    row["personalization_source"] = source_url if context else ""
    row["personalization"] = generate_personalization(client, name, context, stats)
    return row


def main():
    fieldnames = [
        "name", "website", "contact_name", "email",
        "personalization", "personalization_source",
    ]
    items = list(KNOWN_DOMAINS.items())
    stats = {"llm_failures": 0}

    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f, httpx.Client() as client:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        for i, (name, website) in enumerate(items, start=1):
            print(f"[{i}/{len(items)}] {name} ...", end=" ", flush=True)
            failures_before = stats["llm_failures"]
            row = process_known(client, name, website, stats)
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
