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
import os
import re
import time
from pathlib import Path

import ftfy
import httpx
from bs4 import BeautifulSoup

from companies_seed import COMPANY_NAMES

# Дефолт — localhost, а не домашний IP разработчика: у проверяющего свой
# Ollama (если есть) слушает локально. Переопределяется через переменную
# окружения OLLAMA_URL, если сервер поднят на другом адресе.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/chat")
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


# Соцсети и каталоги: попадают в топ выдачи по названию компании чаще, чем
# её собственный сайт, но группа ВКонтакте — не сайт (у "Лига трафика
# агентство" в базу так попал vk.com/liga_traffic вместо реального домена,
# без единого шанса найти на нём email или текст для персонализации).
NOT_A_WEBSITE_DOMAINS = (
    "vk.com", "vk.ru", "ok.ru", "facebook.com", "instagram.com",
    "t.me", "telegram.me", "youtube.com", "linkedin.com",
    "2gis.ru", "yandex.ru", "zoon.ru", "wikipedia.org",
)


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
            host = (httpx.URL(target).host or "").lower()
            if any(host == d or host.endswith("." + d) for d in NOT_A_WEBSITE_DOMAINS):
                continue
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
    """Ищем ссылку на страницу контактов/о компании на главной.

    Ссылка "Контакты" иногда ведёт не на страницу, а прямо на mailto:/tel: —
    такой href пройдёт мимо CONTACT_LINK_RE по тексту ссылки, но не является
    HTTP-адресом: fetch() на нём просто впустую тратит retries и время.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    for a in soup.select("a[href]"):
        text = a.get_text(" ", strip=True)
        href = a["href"]
        if href.startswith(("mailto:", "tel:")):
            continue
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


# Локальные части адреса, которые предпочитаем при выборе из нескольких
# кандидатов на одном домене — реальные точки входа для коммерческого
# письма, в порядке убывания приоритета.
_LOCAL_PART_PRIORITY = ("sales", "hello", "info", "office", "reception")

# Локальные части, которые почти никогда не ведут к человеку, способному
# ответить на холодное письмо (бухгалтерия, персданные, HR, тендеры) —
# такой адрес не выбираем, даже если это единственный кандидат на домене.
_LOCAL_PART_BLACKLIST = (
    "buh", "accounting", "hr", "job", "vacan",
    "pdn", "privacy", "concurs", "tender", "legal",
)


def _same_domain(email_domain: str, company_domain: str) -> bool:
    """Строгое сравнение доменов: не по вхождению подстроки (иначе
    anthony@partner.com ложно матчится с доменом ony.ru — воспроизведено
    на реальных данных), а по полному совпадению домена или его поддомена."""
    email_domain = email_domain.lower().removeprefix("www.")
    company_domain = company_domain.lower().removeprefix("www.")
    return bool(company_domain) and (
        email_domain == company_domain or email_domain.endswith("." + company_domain)
    )


def _email_rank(email: str) -> tuple[int, int]:
    """Чем меньше — тем выше приоритет при выборе из нескольких адресов."""
    local = email.split("@", 1)[0].lower()
    if any(bad in local for bad in _LOCAL_PART_BLACKLIST):
        return (2, 0)
    for rank, prefix in enumerate(_LOCAL_PART_PRIORITY):
        if local.startswith(prefix):
            return (0, rank)
    return (1, 0)


def extract_email(html_text: str, domain: str) -> str | None:
    candidates = {e for e in EMAIL_RE.findall(html_text) if _is_plausible_email(e)}
    if not candidates:
        return None
    # Предпочитаем адрес на домене самой компании — реже ловим email
    # виджетов аналитики/чужих сервисов, случайно попавших в код страницы.
    domain_matches = [e for e in candidates if _same_domain(e.rsplit("@", 1)[-1], domain)]
    pool = domain_matches or list(candidates)
    return sorted(pool, key=lambda e: (*_email_rank(e), e))[0]


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


def ask_llm(
    client: httpx.Client,
    system_prompt: str,
    user_prompt: str,
    stats: dict | None = None,
    timeout: int = 60,
) -> str:
    """Пустая строка означает две разные вещи, которые нельзя путать:
    честное "в тексте этого нет" и "LLM недоступна/упала" (запрос не
    прошёл). Второй случай молча портит прогон — прошлая версия писала
    "OK" даже когда персонализация не сгенерировалась. Поэтому здесь отказ
    LLM считается в stats, а main() печатает итоговую сводку."""
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
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except (httpx.HTTPError, KeyError):
        if stats is not None:
            stats["llm_failures"] += 1
        return ""


def generate_personalization(
    client: httpx.Client, company: str, context: str, stats: dict | None = None
) -> str:
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
    return ask_llm(client, system_prompt, user_prompt, stats)


# Отчество опознаётся по окончанию. Нужно, чтобы понять, где в "Новицкий
# Сергей Владимирович" собственно имя: в холодном письме "Привет, Новицкий
# Сергей Владимирович!" звучит как повестка, а не как письмо человеку.
_PATRONYMIC_RE = re.compile(
    r"^[А-ЯЁ][а-яё]+(ович|евич|ьевич|овна|евна|ьевна|инична|ична)$"
)


def normalize_contact_name(name: str, personalization: str = "") -> str:
    """Приводит найденное имя к форме, пригодной для обращения в письме.

    Две проблемы, вылезшие при сборке реальных писем:

    1. Модель берёт имя из портфолио. У Litera.Studio в контакт уехал
       "Ильи Чируна" — владелец арт-галереи, ДЛЯ которой студия делала
       открытки. Признак такого случая надёжный: имя дословно присутствует
       в тексте персонализации, то есть взято из рассказа о клиентах, а не
       из контактов компании. Такое имя выбрасываем — пустая колонка
       честнее, чем письмо к чужому человеку.
    2. ФИО целиком. Из "Фамилия Имя Отчество" для обращения нужно только
       имя; отчество как раз и позволяет понять, какое слово именем является.

    Возвращает пустую строку, если пригодного имени нет.
    """
    name = name.strip().strip(".\"'«»")
    if not name:
        return ""

    words = name.split()
    # Больше трёх слов — это уже не имя, а фраза ("контактное лицо не указано").
    if not words or len(words) > 3:
        return ""

    # Имя из кейса или портфолио, а не контакт компании.
    if personalization:
        significant = [w for w in words if len(w) > 3]
        if significant and any(w in personalization for w in significant):
            return ""

    # "Фамилия Имя Отчество" -> имя.
    if len(words) == 3 and _PATRONYMIC_RE.match(words[2]):
        return words[1]
    # "Имя Отчество" -> имя: тон писем неформальный ("Привет, ..."),
    # обращение по имени-отчеству в нём звучит чужеродно.
    if len(words) == 2 and _PATRONYMIC_RE.match(words[1]):
        return words[0]
    # "Имя Фамилия" -> имя. Обратный порядок без отчества не отличить без
    # словаря фамилий, поэтому берём первое слово: для распространённого
    # "Имя Фамилия" это верно.
    if len(words) == 2:
        return words[0]
    return words[0]


def extract_contact_name(
    client: httpx.Client,
    html_text: str,
    stats: dict | None = None,
    personalization: str = "",
) -> str:
    """Имя человека, к которому можно обратиться в письме, а не название
    компании. ТЗ требует колонку "имя" в базе, и цепочка писем обращается
    "{{имя}}" — без этой колонки шаблон физически не с чем смёржить.

    Ищем только явно подписанное имя (директор, менеджер, контактное лицо)
    на странице "Контакты"/"О компании"/"Команда". Модели запрещено гадать:
    для большинства российских агентств такого имени на сайте просто нет,
    и пустая строка здесь честнее выдуманного "Иван". Результат дополнительно
    проходит normalize_contact_name() — промпта одного оказалось мало.
    """
    text = extract_context_text(html_text)
    if not text.strip():
        return ""

    system_prompt = (
        "На странице сайта компании иногда указано имя конкретного "
        "сотрудника этой компании (директор, менеджер по продажам, "
        "контактное лицо). Ответь строго одной строкой:\n"
        "ИМЯ: <имя в именительном падеже> или ИМЯ: нет\n"
        "Пиши 'нет', если на странице нет явно подписанного имени "
        "сотрудника этой компании. Не выдумывай и не бери за имя "
        "человека название компании, бренда или домена.\n"
        "ВАЖНО: не бери имена людей из примеров работ, портфолио, кейсов, "
        "отзывов, списка клиентов и партнёров — это чужие люди, а не "
        "контакты этой компании. Если имя встречается только там, отвечай "
        "'нет'."
    )
    user_prompt = f"Текст страницы:\n{text[:1200]}"
    answer = ask_llm(client, system_prompt, user_prompt, stats)

    m = re.search(r"ИМЯ:\s*(.+)", answer)
    if not m:
        return ""
    name = m.group(1).strip().strip(".\"'")
    if not name or re.match(r"(нет|no|none|неизвест)", name, re.IGNORECASE):
        return ""
    return normalize_contact_name(name[:40], personalization)


def process_company(
    client: httpx.Client, name: str, stats: dict | None = None, website: str | None = None
) -> dict:
    """website=None — ищем сайт через search_website() (проход 1). Если сайт
    уже известен (проход 2, из known_domains.py), передаём его напрямую и
    поиск пропускаем — вся остальная логика (fetch -> email -> контекст ->
    имя -> персонализация) общая для обоих проходов."""
    row = {
        "name": name,
        "website": "",
        "contact_name": "",
        "email": "",
        "personalization": "",
        "personalization_source": "",
    }

    if website is None:
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

    # Фетчим страницу контактов не только для email/контекста, но и для
    # имени: чаще всего конкретное имя (не название компании) встречается
    # именно там, а не на главной.
    contact_url = find_contact_page(website, home_html)
    contact_html = fetch(client, contact_url) if contact_url else None
    if contact_html:
        if not email:
            email = extract_email(contact_html, domain)
        if not context:
            context = extract_context_text(contact_html)
            source_url = contact_url

    # Персонализацию считаем раньше имени намеренно: она нужна как фильтр.
    # Если найденное "имя контакта" дословно встречается в тексте
    # персонализации — это имя из портфолио или отзыва, а не контакт
    # компании (см. normalize_contact_name).
    personalization = generate_personalization(client, name, context, stats)

    contact_name = (
        extract_contact_name(client, contact_html, stats, personalization)
        if contact_html
        else ""
    )
    if not contact_name:
        contact_name = extract_contact_name(client, home_html, stats, personalization)

    row["email"] = email or ""
    row["contact_name"] = contact_name
    row["personalization_source"] = source_url if context else ""
    row["personalization"] = personalization

    return row


def main():
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    fieldnames = [
        "name", "website", "contact_name", "email",
        "personalization", "personalization_source",
    ]
    stats = {"llm_failures": 0}

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f, httpx.Client() as client:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, name in enumerate(COMPANY_NAMES, start=1):
            print(f"[{i}/{len(COMPANY_NAMES)}] {name} ...", end=" ", flush=True)
            failures_before = stats["llm_failures"]
            row = process_company(client, name, stats)
            writer.writerow(row)
            f.flush()
            if not row["website"]:
                print("сайт не найден")
            elif stats["llm_failures"] > failures_before:
                print("сайт найден, но LLM недоступна — без персонализации")
            else:
                print("OK")
            time.sleep(1.5)  # не долбим DuckDuckGo слишком часто

    if stats["llm_failures"]:
        print(
            f"\n{stats['llm_failures']} из {len(COMPANY_NAMES)} строк — без "
            "персонализации из-за ошибки LLM (не путать с честным "
            "'недостаточно данных' — это отказ запроса)"
        )


if __name__ == "__main__":
    main()
