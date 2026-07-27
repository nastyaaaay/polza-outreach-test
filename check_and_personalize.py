"""
check_and_personalize.py — задача 4 тестового Polza Agency.

Базу из 15 компаний прислал клиент с прямой подсказкой «будь начеку, вдруг мы
что-то перепутали». Проверка показала, что перепутали в семи строках из
пятнадцати (полный разбор с доказательствами — в task4_analysis.md), поэтому
скрипт не просто персонализирует строки, а сначала их проверяет.

Ключевое решение: колонке с сайтом не доверяем. Для каждой компании скрипт
сам открывает сайт из базы, смотрит, кому этот сайт реально принадлежит, и
только потом решает, по какому адресу строить персонализацию. Если сайт из
базы чужой или мёртвый — берём настоящий сайт компании и пишем в отчёт, что
именно не сошлось. Молча подменять данные клиента нельзя: расхождение — это
и есть половина ценности работы.

Что проверяется по каждой строке:
  1. Отвечает ли сайт из базы (с фолбэком на www и http) и не заглушка ли это.
  2. Кому сайт принадлежит — по заголовку и реальному тексту страницы,
     вердикт выносит локальная LLM, факты берутся только со страницы.
  3. Совпадает ли домен почты с доменом сайта, не бесплатный ли это ящик.
  4. Не повторяется ли домен в других строках базы.
  5. Попадает ли компания в нишу базы (станки и металлообработка).
Затем — персонализация по реально найденному сайту, на языке этого сайта.

Запуск:
    python check_and_personalize.py

Вход:
    input/polza_base.csv — база от клиента как есть, БЕЗ строки заголовков
    (первая строка — уже компания; читатель с header=0 потерял бы её).

Результат:
    output/polza_checked.csv
"""

import csv
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import ftfy
import httpx
from bs4 import BeautifulSoup

from collect_leads import (
    HEADERS,
    OLLAMA_MODEL,
    OLLAMA_URL,
    extract_context_text,
    find_contact_page,
    search_website,
)
from polza_known_sites import VERIFIED_SITES

BASE_PATH = Path(__file__).parent / "input" / "polza_base.csv"
OUTPUT_PATH = Path(__file__).parent / "output" / "polza_checked.csv"

# Ниша базы, как она читается по самим компаниям: станочники, инструментальщики
# и поставщики производственного оборудования. Формулировка идёт в промпт
# проверки профиля, поэтому держим её здесь, а не внутри функции.
BASE_NICHE = (
    "станки, металлообрабатывающее и кузнечно-прессовое оборудование, "
    "промышленный режущий инструмент, приводы и промышленная автоматизация "
    "(ЧПУ, АСУТП, сервоприводы, роботы), контрольно-измерительное "
    "оборудование для металлов"
)

# Каталоги, справочники и соцсети: в поисковой выдаче они часто стоят выше
# сайта самой компании, но персонализацию по ним строить нельзя — там
# перепечатанное описание, а не то, что компания пишет о себе сама.
AGGREGATOR_DOMAINS = (
    "made-in-china.com", "alibaba.com", "aliexpress.com", "industryarena.com",
    "tradekey.com", "tradeindia.com", "importkey.com", "tradedata.pro",
    "zoominfo.com", "crunchbase.com", "linkedin.com", "facebook.com",
    "wikipedia.org", "machinetools.com", "expocentr.ru", "2gis.ru",
    "rusprofile.ru", "list-org.com", "exportbase.ru",
)

# Бесплатные почтовые сервисы. Адрес на таком домене — не повод выкинуть
# компанию (китайские заводы сплошь и рядом пишут с 126.com), но по домену
# почты компанию уже не опознать, и сверка «домен почты = домен сайта»
# на такой строке ложно срабатывает.
FREE_MAIL_DOMAINS = {
    "126.com", "163.com", "qq.com", "sina.com", "aliyun.com",
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com",
    "mail.ru", "yandex.ru", "bk.ru", "list.ru", "inbox.ru",
}

# Признаки страницы-заглушки. У Internor оба домена из базы отдают именно
# такую: хостинг Aliyun остановлен, HTML — один meta refresh на служебную
# страницу, никакого контента для персонализации там нет.
STUB_MARKERS = (
    "ipvisit_stop",
    "domain is for sale",
    "домен продается",
    "домен продаётся",
    "account suspended",
)

# Главная jillionsupply.com нашпигована посторонними SEO-ключами для взрослых —
# они вставлены прямо между реальными товарными ссылками. Если такой текст
# уедет в LLM, лексика может всплыть в персонализации холодного письма.
# Поэтому фрагменты с этими словами вырезаем до вызова модели.
SPAM_RE = re.compile(
    r"\b(dildo|pussy|porn|sex\s?toy|escort|viagra|casino|adult\s?toy)\b",
    re.IGNORECASE,
)


@dataclass
class Probe:
    """Результат попытки открыть сайт."""

    url: str = ""          # финальный URL после редиректов
    status: int = 0
    title: str = ""
    text: str = ""         # осмысленный текст страницы (meta description + абзацы)
    html: str = ""         # нужен, чтобы при пустой главной дойти до «О компании»
    is_stub: bool = False
    error: str = ""

    @property
    def alive(self) -> bool:
        return self.status == 200 and not self.is_stub and bool(self.text or self.title)


@dataclass
class Row:
    """Строка базы плюс всё, что про неё выяснили."""

    name: str
    email: str
    site_in_base: str
    issues: list[str] = field(default_factory=list)


def load_base(path: Path) -> list[Row]:
    """Читаем базу клиента: три колонки, без заголовков, строго UTF-8.

    Заголовков в файле нет — первая строка уже содержит компанию. Пустые
    строки в конце листа пропускаем (в выгрузке из Google Sheets такая есть).
    """
    rows = []
    with open(path, encoding="utf-8", newline="") as f:
        for raw in csv.reader(f):
            if not raw or not any(cell.strip() for cell in raw):
                continue
            cells = (raw + ["", "", ""])[:3]
            rows.append(Row(*(c.strip() for c in cells)))
    return rows


def _url_variants(site: str) -> list[str]:
    """Варианты адреса для одной строки базы.

    В базе домены записаны без схемы, и часть из них отвечает только с www:
    голые nttzmt.com и fengyitool.com уходят в таймаут, www-версии тех же
    сайтов открываются нормально. Поэтому перебираем варианты, а не считаем
    первый неответ приговором.
    """
    if site.startswith("http"):
        return [site]
    host = site.strip("/")
    if host.startswith("www."):
        return [f"https://{host}", f"http://{host}", f"https://{host[4:]}"]
    return [f"https://{host}", f"https://www.{host}", f"http://www.{host}", f"http://{host}"]


def open_site(client: httpx.Client, site: str) -> Probe:
    """Открываем сайт, перебирая варианты адреса, и вытаскиваем из него
    заголовок и осмысленный текст.

    Заглушка на одном варианте адреса не означает, что сайт мёртв — у
    Internor https-версия отдаёт заглушку остановленного хостинга Aliyun,
    а живой сайт открывается только на http://www. Поэтому заглушка не
    прерывает перебор (было: return, из-за чего следующий вариант даже не
    пробовался) — перебор продолжается, и только если ВСЕ варианты
    оказались заглушкой/пустыми, возвращаем последнюю такую попытку."""
    probe = Probe()
    if not site:
        probe.error = "адрес не указан"
        return probe

    last_error = ""
    last_stub: Probe | None = None
    for url in _url_variants(site):
        try:
            resp = client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        except httpx.HTTPError as e:
            last_error = f"{type(e).__name__}"
            continue

        candidate = Probe(url=str(resp.url), status=resp.status_code)
        if resp.status_code != 200:
            last_error = f"HTTP {resp.status_code}"
            continue

        try:
            html = resp.content.decode("utf-8")
        except UnicodeDecodeError:
            html = resp.content.decode("cp1251", errors="replace")
        html = ftfy.fix_text(html)

        lowered = html.lower()
        if any(marker in lowered for marker in STUB_MARKERS):
            candidate.is_stub = True
            candidate.error = "страница-заглушка"
            last_stub = candidate
            last_error = candidate.error
            continue

        soup = BeautifulSoup(html, "html.parser")
        candidate.title = soup.title.get_text(strip=True) if soup.title else ""
        candidate.text = strip_spam(extract_context_text(html))
        if len(candidate.text) < 120:
            candidate.text = strip_spam(visible_text(html)) or candidate.text
        candidate.html = html
        if not candidate.title and not candidate.text:
            candidate.is_stub = True
            candidate.error = "страница пустая"
            last_stub = candidate
            last_error = candidate.error
            continue

        return candidate

    if last_stub is not None:
        return last_stub
    probe.error = last_error or "не отвечает"
    return probe


def visible_text(html: str) -> str:
    """Запасной способ достать текст страницы.

    Основной extract_context_text() берёт meta description и абзацы p/h1/h2 —
    на большинстве сайтов этого достаточно. Но, например, technosphera.ru
    верстает контент дивами и таблицами, и оттуда возвращается пустота: и
    главная, и страница «О компании» дают три символа. Тогда берём весь
    видимый текст, выкинув навигацию, подвал и скрипты.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    chunks = [
        chunk
        for chunk in (line.strip() for line in soup.get_text("\n").splitlines())
        if len(chunk) > 40
    ]
    return re.sub(r"\s+", " ", " ".join(chunks))[:800]


def strip_spam(text: str) -> str:
    """Выбрасываем фрагменты с посторонними SEO-ключами перед подачей в LLM."""
    parts = [p for p in re.split(r"(?<=[.!?。])\s+|\s{2,}", text) if not SPAM_RE.search(p)]
    return " ".join(parts).strip()


def ask_llm(client: httpx.Client, system_prompt: str, user_prompt: str) -> str:
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
                "options": {"temperature": 0.2},
                "stream": False,
            },
            timeout=120,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except (httpx.HTTPError, KeyError):
        return ""


def check_owner(client: httpx.Client, name: str, probe: Probe) -> tuple[str, str]:
    """Сверяем, принадлежит ли открытый сайт компании из базы.

    Это и есть та проверка, ради которой всё затевалось: именно она ловит
    случай, когда в строке «Tengzhong Machinery» стоит сайт кабельного завода
    «Ункомтех». Модель судит только по заголовку и тексту страницы, гадать
    ей запрещено — иначе она начнёт «узнавать» компании по названию домена.

    Возвращает (вердикт, чей это сайт по тексту страницы).
    """
    if not probe.alive:
        return "неясно", ""

    system_prompt = (
        "Ты сверяешь, принадлежит ли сайт указанной компании. Тебе дают "
        "название компании из базы и реальные заголовок и текст с сайта. "
        "Суди только по присланному тексту, ничего не додумывай. "
        "Учти: у китайских компаний на сайте может стоять только иероглифическое "
        "название — если по тексту нельзя понять, та же это компания или другая, "
        "отвечай НЕЯСНО, а не НЕ СОВПАДАЕТ. НЕ СОВПАДАЕТ — только когда на "
        "странице явно видно другую компанию с другим родом деятельности. "
        "Ответь строго двумя строками:\n"
        "ВЕРДИКТ: СОВПАДАЕТ или НЕ СОВПАДАЕТ или НЕЯСНО\n"
        "ВЛАДЕЛЕЦ: чей это сайт по тексту страницы, коротко"
    )
    user_prompt = (
        f"Компания из базы: {name}\n\n"
        f"Заголовок сайта: {probe.title}\n\n"
        f"Текст со страницы:\n{probe.text[:800]}"
    )
    answer = ask_llm(client, system_prompt, user_prompt)

    verdict = "неясно"
    if re.search(r"ВЕРДИКТ:\s*НЕ\s+СОВПАДАЕТ", answer, re.IGNORECASE):
        verdict = "не совпадает"
    elif re.search(r"ВЕРДИКТ:\s*СОВПАДАЕТ", answer, re.IGNORECASE):
        verdict = "совпадает"

    owner = ""
    m = re.search(r"ВЛАДЕЛЕЦ:\s*(.+)", answer)
    if m:
        owner = m.group(1).strip().strip(".")[:120]

    # «Не совпадает» засчитываем только если модель назвала, кому сайт
    # принадлежит на самом деле. Без этого она иногда отвергает верную строку
    # просто потому, что не нашла названия на странице, — а это уже не
    # обнаруженная подмена, а недостаток текста.
    if verdict == "не совпадает" and (
        not owner or re.match(r"(неизвест|неясн|не удалось|нет )", owner, re.IGNORECASE)
    ):
        verdict = "неясно"
    return verdict, owner


def enrich_context(client: httpx.Client, probe: Probe) -> None:
    """Если на главной почти нет текста — дочитываем страницу «О компании».

    Так устроена, например, главная technosphera.ru: сплошная навигация и
    карта сайта, живого описания компании нет, и модель честно отвечает
    «недостаточно данных». Реальное описание лежит страницей глубже.
    """
    if len(probe.text) >= 120 or not probe.html or not probe.url:
        return
    contact_url = find_contact_page(probe.url, probe.html)
    if not contact_url:
        return
    deeper = open_site(client, contact_url)
    if len(deeper.text) > len(probe.text):
        probe.text = deeper.text


def check_niche(client: httpx.Client, name: str, probe: Probe) -> tuple[str, str]:
    """Чем компания занимается и попадает ли она в нишу базы.

    Ункомтех (кабель) и Техносфера (издательство) в нишу не попадают — их не
    выкидываем, а помечаем: решение об исключении из рассылки за клиентом.

    Модель сначала называет отрасль своими словами и только потом выносит
    вердикт: локальная модель на 20B заметно точнее, когда сперва проговорит
    факт, а не выдаёт ярлык сразу. Отрасль идёт в отчёт отдельной колонкой —
    по ней видно, на каком основании выставлен вердикт. Вердикт читаем строго
    из своей строки: раньше проверка искала «НЕ ПРОФИЛЬ» по всему ответу и
    переворачивалась, если эти слова встречались в рассуждении.
    """
    if not probe.alive:
        return "", ""

    system_prompt = (
        f"База клиента — это компании из сферы: {BASE_NICHE}.\n"
        "Примеры ПРОФИЛЬ: производитель фрез и свёрл; поставщик сервоприводов "
        "и промышленных роботов; завод листогибочных прессов; производитель "
        "дробеструйных установок; производитель спектрометров для анализа "
        "металлов и сплавов.\n"
        "Примеры НЕ ПРОФИЛЬ: издательство; кабельный завод; банк; "
        "туристическое агентство; поставщик продуктов питания.\n"
        "По присланному тексту с сайта ответь строго двумя строками:\n"
        "ОТРАСЛЬ: чем компания занимается, одна короткая фраза\n"
        "ВЕРДИКТ: ПРОФИЛЬ или НЕ ПРОФИЛЬ"
    )
    user_prompt = f"Компания: {name}\n\nТекст с сайта:\n{probe.text[:700]}"
    answer = ask_llm(client, system_prompt, user_prompt)

    industry = ""
    m = re.search(r"ОТРАСЛЬ:\s*(.+)", answer)
    if m:
        industry = m.group(1).strip().strip(".")[:120]

    niche = ""
    m = re.search(r"ВЕРДИКТ:\s*(НЕ\s+ПРОФИЛЬ|ПРОФИЛЬ)", answer, re.IGNORECASE)
    if m:
        niche = "вне ниши" if m.group(1).upper().startswith("НЕ") else "в нише"
    return industry, niche


def detect_language(probe: Probe) -> str:
    """Русский — только для сайтов на кириллице в доменах .ru/.su. Остальным
    (китайские экспортёры) писать логичнее по-английски: на их сайтах
    английские версии, и на русское письмо там отвечать некому."""
    host = httpx.URL(probe.url).host if probe.url else ""
    sample = f"{probe.title} {probe.text}"
    cyrillic = len(re.findall(r"[а-яА-ЯёЁ]", sample))
    if host.endswith((".ru", ".su", ".рф")) or cyrillic > 30:
        return "ru"
    return "en"


def generate_personalization(client: httpx.Client, name: str, probe: Probe, language: str) -> str:
    """Персонализация строится только по реальному тексту сайта. Модели прямо
    запрещено выдумывать: если на странице нет ничего конкретного, она обязана
    так и сказать — пустая ячейка честнее выдуманного факта."""
    context = strip_spam(probe.text)
    if not context.strip():
        return ""

    if language == "ru":
        system_prompt = (
            "Ты помогаешь составить персонализацию для холодного B2B-письма. "
            "На основе присланного реального текста с сайта компании напиши "
            "1-2 коротких предложения на русском языке — конкретный факт о "
            "компании, который можно использовать как персональный крючок в "
            "письме. Не выдумывай ничего, чего нет в тексте. Если в тексте "
            "нет ничего конкретного, кроме общих слов — так и напиши: "
            "'недостаточно данных для персонализации'."
        )
        user_prompt = f"Компания: {name}\n\nТекст с сайта:\n{context}"
    else:
        system_prompt = (
            "You help write the personalization line for a cold B2B email. "
            "Using only the real text from the company website below, write "
            "1-2 short sentences in English: a concrete fact about the company "
            "that works as a personal hook. Invent nothing that is not in the "
            "text. If the text has nothing concrete beyond generic phrases, "
            "reply exactly: 'not enough data for personalization'."
        )
        user_prompt = f"Company: {name}\n\nWebsite text:\n{context}"

    return ask_llm(client, system_prompt, user_prompt)


def real_site_candidates(client: httpx.Client, name: str) -> list[tuple[str, str]]:
    """Кандидаты на роль настоящего сайта компании, в порядке приоритета.

    Сначала поисковая выдача, потом проверенный вручную адрес из
    polza_known_sites.py. Возвращаем именно список, а не один адрес: поиск
    регулярно отдаёт мёртвую ссылку (у Rogen Technologies — rogentech.cn,
    который не открывается, при живом rogentech.com), и на одном кандидате
    пайплайн бы на этом и остановился. Источник идёт вторым значением и
    попадает в отчёт, чтобы было видно, откуда взялся адрес.
    """
    candidates = []
    found = search_website(client, name)
    if found and not any(a in (httpx.URL(found).host or "") for a in AGGREGATOR_DOMAINS):
        candidates.append((found, "поиск"))
    verified = VERIFIED_SITES.get(name)
    if verified:
        candidates.append((verified, "проверено вручную"))
    return candidates


def describe_site_status(probe: Probe) -> str:
    if probe.alive:
        return "отвечает"
    if probe.is_stub:
        return probe.error or "заглушка"
    return probe.error or "не отвечает"


def collect_issues(row: Row, probe: Probe, verdict: str, owner: str, duplicates: set[str]) -> None:
    """Складываем все замечания по строке в человекочитаемый список."""
    email_domain = row.email.rsplit("@", 1)[-1].lower() if "@" in row.email else ""
    site_domain = row.site_in_base.lower().removeprefix("www.")

    if verdict == "не совпадает":
        owner_note = f" (сайт принадлежит: {owner})" if owner else ""
        row.issues.append(f"сайт из базы не принадлежит этой компании{owner_note}")
    if not probe.alive:
        row.issues.append(f"сайт из базы {describe_site_status(probe)}")
    if site_domain and site_domain in duplicates:
        row.issues.append("этот домен указан ещё у другой компании в базе")
    if email_domain in FREE_MAIL_DOMAINS:
        row.issues.append("почта на бесплатном ящике, а не на домене компании")
    elif email_domain and site_domain and email_domain.removeprefix("www.") != site_domain:
        row.issues.append(f"домен почты ({email_domain}) не совпадает с доменом сайта")


def check_email_against_real_site(row: Row, probe: Probe, site_domain: str) -> None:
    """collect_issues() сверяет домен почты с site_in_base — если сайт и
    почта в строке подменены СОГЛАСОВАННО (Tesid Equipment: и sales@unimach.ru,
    и unimach.ru — сайт «Юнимаша», а не настоящего Beijing TESID), эта сверка
    молчит: домены совпадают друг с другом, просто оба чужие. Здесь сверяем
    уже с доменом реально найденного сайта, по которому строится
    персонализация — письмо не должно уйти не той компании (кейс JAT/Tesid
    из task4_analysis.md, п.4 "ключевые находки")."""
    if not probe.alive or not probe.url:
        return
    email_domain = row.email.rsplit("@", 1)[-1].lower() if "@" in row.email else ""
    if not email_domain or email_domain in FREE_MAIL_DOMAINS:
        return
    real_domain = (httpx.URL(probe.url).host or "").lower().removeprefix("www.")
    if not real_domain or real_domain == site_domain:
        return  # уже проверено и отражено в collect_issues()
    if email_domain.removeprefix("www.") != real_domain:
        row.issues.append(
            f"домен почты ({email_domain}) не совпадает с реально найденным "
            f"сайтом ({real_domain}) — письмо может уйти не той компании"
        )


def main() -> None:
    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    rows = load_base(BASE_PATH)

    # Домены, встречающиеся в базе больше одного раза: если дедуплицировать
    # базу по домену, одна из таких компаний просто исчезнет.
    seen: dict[str, int] = {}
    for row in rows:
        key = row.site_in_base.lower().removeprefix("www.")
        seen[key] = seen.get(key, 0) + 1
    duplicates = {domain for domain, count in seen.items() if count > 1 and domain}

    fieldnames = [
        "name", "email", "site_in_base", "site_status", "site_owner", "verdict",
        "site_used", "site_source", "industry", "niche", "issues", "language",
        "personalization", "personalization_source",
    ]

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f, httpx.Client() as client:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()

        for i, row in enumerate(rows, start=1):
            print(f"[{i}/{len(rows)}] {row.name} ...", end=" ", flush=True)

            base_probe = open_site(client, row.site_in_base)
            enrich_context(client, base_probe)
            verdict, owner = check_owner(client, row.name, base_probe)
            collect_issues(row, base_probe, verdict, owner, duplicates)
            probe = base_probe

            # Персонализацию строим по сайту из базы только если он живой и
            # действительно принадлежит этой компании. Иначе ищем настоящий и
            # перебираем кандидатов до первого живого.
            site_source = "из базы"
            if verdict != "совпадает" or not probe.alive:
                probe, site_source = Probe(), ""
                for candidate, source in real_site_candidates(client, row.name):
                    candidate_probe = open_site(client, candidate)
                    if candidate_probe.alive:
                        enrich_context(client, candidate_probe)
                        probe, site_source = candidate_probe, source
                        break

                # Ключевое правило: если настоящий сайт не нашёлся, компания
                # остаётся без персонализации. Строить её по сайту из базы,
                # который этой компании не принадлежит, нельзя — именно так
                # Rogen Technologies получил бы письмо про пластины JAT.
                if not probe.alive:
                    row.issues.append(
                        "настоящий сайт не найден, персонализация не построена"
                    )

            check_email_against_real_site(
                row, probe, row.site_in_base.lower().removeprefix("www.")
            )

            language = detect_language(probe) if probe.alive else ""
            industry, niche = check_niche(client, row.name, probe)
            if niche == "вне ниши":
                row.issues.append("компания вне ниши базы")

            personalization = (
                generate_personalization(client, row.name, probe, language) if probe.alive else ""
            )

            writer.writerow({
                "name": row.name,
                "email": row.email,
                "site_in_base": row.site_in_base,
                "site_status": describe_site_status(base_probe),
                "site_owner": owner,
                "verdict": verdict,
                "site_used": probe.url,
                "site_source": site_source,
                "industry": industry,
                "niche": niche,
                "issues": "; ".join(row.issues),
                "language": language,
                "personalization": personalization,
                "personalization_source": probe.url if personalization else "",
            })
            f.flush()

            print("проблемы: " + "; ".join(row.issues) if row.issues else "OK")
            time.sleep(1.0)

    print(f"\nготово: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
