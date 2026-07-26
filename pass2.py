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
    extract_email,
    fetch,
    find_contact_page,
    generate_personalization,
)
from known_domains import KNOWN_DOMAINS


def process_known(client: httpx.Client, name: str, website: str) -> dict:
    row = {
        "name": name,
        "website": website,
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
    if contact_url and (not email or not context):
        contact_html = fetch(client, contact_url)
        if contact_html:
            if not email:
                email = extract_email(contact_html, domain)
            if not context:
                context = extract_context_text(contact_html)
                source_url = contact_url

    row["email"] = email or ""
    row["personalization_source"] = source_url if context else ""
    row["personalization"] = generate_personalization(client, name, context)
    return row


def main():
    fieldnames = ["name", "website", "email", "personalization", "personalization_source"]
    items = list(KNOWN_DOMAINS.items())

    with open(OUTPUT_PATH, "a", newline="", encoding="utf-8") as f, httpx.Client() as client:
        writer = csv.DictWriter(f, fieldnames=fieldnames)

        for i, (name, website) in enumerate(items, start=1):
            print(f"[{i}/{len(items)}] {name} ...", end=" ", flush=True)
            row = process_known(client, name, website)
            writer.writerow(row)
            f.flush()
            print("OK" if row["email"] or row["personalization"] else "частично")
            time.sleep(0.5)


if __name__ == "__main__":
    main()
