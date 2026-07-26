"""
collect_leads.py — сбор B2B-базы + персонализация.
Тестовое задание Polza Agency, задачи 1 и 2.

Пайплайн для каждой компании из companies_seed.py:
  1. Ищем официальный сайт через DuckDuckGo HTML-версию (без API-ключа и капчи).
  2. Скачиваем главную страницу и, если получится найти, страницу
     "О компании" / "Контакты" — вытаскиваем email и кусок реального текста.
  3. Реальный текст о компании передаём локальной LLM (Ollama, тот же сервер,
     что в проекте smart-intake-automation) с просьбой сформулировать
     персонализацию: 1-2 предложения, по-русски, без выдумывания фактов —
     только на основе того, что реально написано на сайте компании.
  4. Пишем результат построчно в CSV сразу по готовности, чтобы долгий прогон
     не терял прогресс при сбое на середине.

Запуск:
    python collect_leads.py

Результат:
    output/leads.csv — name, website, contact_name, email, personalization,
    personalization_source (откуда взят факт — для проверки, что не выдумано)
"""

import csv
import re
import time
from pathlib import Path

import ftfy
import httpx
from bs4 import BeautifulSoup

from companies_seed import COMPANY_NAMES

OLLAMA_URL = "http://192.168.1.202:11434/api/chat"
OLLAMA_MODEL = "gpt-oss:20b"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CONTACT_LINK_RE = re.compile(r"контакт|contact|о\s?компании|about", re.IGNORECASE)

OUTPUT_PATH = Path(__file__).parent / "output" / "leads.csv"


def _get_with_retries(client: httpx.Client, url: str, *, params=None, retries=3) -> httpx.Response | None:
    """GET с повторами — сеть периодически отваливается на единичных запросах
    (таймауты, оборванные соединения), при 50+ компаниях это не разовая
    случайность, а ожидаемая часть прогона."""
    last_error = None
    for attempt in range(retries):
        try:
            resp = client.get(url, params=params, headers=HEADERS, timeout=15, follow_redirects=True)
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    return None


def search_website(client: httpx.Client, query: str) -> str | None:
    """Ищем официальный сайт компании через DuckDuckGo HTML-выдачу."""
    resp = _get_with_retries(
        client, "https://html.duckduckgo.com/html/", params={"q": f"{query} официальный сайт"}
    )
    if resp is None:
        return None

    soup = BeautifulSoup(resp.text, "html.parser")
    for link in soup.select("a.result__a"):
        href = link.get("href", "")
        # DuckDuckGo html-версия отдаёт редиректный URL вида
        # //duckduckgo.com/l/?uddg=<encoded target url>
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            from urllib.parse import unquote

            target = unquote(m.group(1))
        else:
            target = href
        if target.startswith("http") and "duckduckgo.com" not in target:
            return target
    return None


def fetch(client: httpx.Client, url: str) -> str | None:
    """Скачивает страницу и декодирует её в текст.

    На части сайтов (встречалось на Tilda-лендингах вроде mgcom.ru) заголовок
    и <meta charset> честно объявляют UTF-8, но реальный текст в кириллице
    залит с ошибкой перекодировки — классический мохибейк, который обычные
    детекторы кодировки (включая BeautifulSoup.UnicodeDammit) не ловят, потому
    что байты формально валидны как UTF-8, просто не то значат. Поэтому после
    декодирования дополнительно прогоняем текст через ftfy.fix_text(), которая
    умеет распознавать и чинить именно такой паттерн двойной перекодировки.
    """
    resp = _get_with_retries(client, url)
    if resp is None:
        return None
    try:
        text = resp.content.decode("utf-8")
    except UnicodeDecodeError:
        text = resp.content.decode("cp1251", errors="replace")
    return ftfy.fix_text(text)


def find_contact_page(base_url: str, html_text: str) -> str | None:
    """Ищем ссылку на страницу контактов/о компании на главной."""
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if CONTACT_LINK_RE.search(text) or CONTACT_LINK_RE.search(href):
            return str(httpx.URL(base_url).join(href))
    return None


_ASSET_EXT_RE = re.compile(
    r"\.(png|jpe?g|svg|gif|webp|js|css|ico|woff2?|ttf|map|json|xml)$", re.IGNORECASE
)

# Домены аналитики/трекинга/соцсетей, чьи "технические" адреса иногда попадают
# под email-регулярку, но реальными контактными адресами компании не являются
# (например, ВК подставляет на групповых страницах служебные ID вида
# <hash>@stacks.vk-portal.net вместо настоящей почты).
_NOISE_DOMAINS = (
    "vk-portal.net",
    "sentry.io",
    "sentry-next.wixpress.com",
    "google-analytics.com",
    "doubleclick.net",
    "wixpress.com",
    "example.com",
    "yourdomain.com",
)


def _is_plausible_email(email: str) -> bool:
    """Regex для email ловит и настоящую почту, и мусор: имя JS-библиотеки с
    версией (например, "player@1.8.0.js" из тега <script src=...>), а на
    страницах вроде групп ВКонтакте — служебные технические адреса аналитики.
    Отсекаем: расширения файлов, "домены" из одних цифр и известные домены
    трекинга/аналитики."""
    if _ASSET_EXT_RE.search(email):
        return False
    domain_part = email.rsplit("@", 1)[-1].lower()
    if any(domain_part.endswith(noise) for noise in _NOISE_DOMAINS):
        return False
    host_body = domain_part.rsplit(".", 1)[0]
    if re.fullmatch(r"[\d.\-]+", host_body):
        return False
    return True


def extract_email(html_text: str, domain: str) -> str | None:
    candidates = {e for e in EMAIL_RE.findall(html_text) if _is_plausible_email(e)}
    if not candidates:
        return None
    # Предпочитаем адрес на домене самой компании — реже ловим email
    # виджетов аналитики/чужих сервисов, случайно попавших в код страницы.
    domain_matches = [e for e in candidates if domain.split(".")[-2] in e.lower()]
    pool = domain_matches or list(candidates)
    return sorted(pool)[0]


def extract_context_text(html_text: str) -> str:
    """Достаём короткий реальный текст о компании: meta description + первый
    осмысленный абзац. Это сырьё для LLM-персонализации — не выдумываем факты,
    а даём модели то, что реально написано на сайте."""
    soup = BeautifulSoup(html_text, "html.parser")
    parts = []

    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        parts.append(meta["content"].strip())

    for tag in soup.find_all(["p", "h1", "h2"]):
        text = tag.get_text(" ", strip=True)
        if len(text) > 40:
            parts.append(text)
        if len(" ".join(parts)) > 600:
            break

    return " ".join(parts)[:800]


def generate_personalization(client: httpx.Client, company: str, context: str) -> str:
    if not context.strip():
        return ""

    system_prompt = (
        "Ты помогаешь составить персонализацию для холодного B2B-письма. "
        "На основе присланного реального текста с сайта компании напиши "
        "1-2 коротких предложения на русском языке — конкретный факт о "
        "компании, который можно использовать как персональный крючок в "
        "письме. Не выдумывай ничего, чего нет в тексте. Если в тексте "
        "нет ничего конкретного, кроме общих слов — так и напиши: "
        "'недостаточно данных для персонализации'."
    )
    user_prompt = f"Компания: {company}\n\nТекст с сайта:\n{context}"

    try:
        resp = client.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "think": "low",
                "options": {"temperature": 0.3},
                "stream": False,
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except (httpx.HTTPError, KeyError):
        return ""


def process_company(client: httpx.Client, name: str) -> dict:
    row = {
        "name": name,
        "website": "",
        "email": "",
        "personalization": "",
        "personalization_source": "",
    }

    website = search_website(client, name)
    if not website:
        return row
    row["website"] = website

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
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    fieldnames = ["name", "website", "email", "personalization", "personalization_source"]

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f, httpx.Client() as client:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, name in enumerate(COMPANY_NAMES, start=1):
            print(f"[{i}/{len(COMPANY_NAMES)}] {name} ...", end=" ", flush=True)
            row = process_company(client, name)
            writer.writerow(row)
            f.flush()
            print("OK" if row["website"] else "сайт не найден")
            time.sleep(1.5)  # не долбим DuckDuckGo слишком часто


if __name__ == "__main__":
    main()
