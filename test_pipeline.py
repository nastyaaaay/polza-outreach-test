"""
Тесты чистых функций пайплайна — тех, что работают без сети и без LLM.

Почему именно эти четыре: в них жили реальные баги, найденные уже после
сдачи. Выбор email по алфавиту клал в базу бухгалтерию вместо продаж;
сравнение доменов подстрокой на паре ony.ru / anthony@partner.com брало
чужой адрес, а домен без точки ронял скрипт с IndexError; перебор адресов
обрывался на первой заглушке; в колонку имени попадал человек из портфолио.
Всё это ловится за секунды без единого сетевого запроса.

Запуск:
    python -m pytest test_pipeline.py -q
или без pytest:
    python test_pipeline.py
"""

import re

from collect_leads import (
    _email_rank,
    _is_plausible_email,
    extract_email,
    looks_like_template,
    normalize_contact_name,
)
from finalize import sanitize_personalization
from check_and_personalize import _url_variants, strip_spam


# --- выбор email -----------------------------------------------------------

def test_email_prefers_sales_over_accounting():
    """Главная претензия ревью: sorted(pool)[0] клал в базу buh@ (бухгалтерия),
    потому что "b" идёт раньше "i". Роль должна решать, а не алфавит."""
    html = "buh@jcat.ru info@jcat.ru mail@jcat.ru"
    assert extract_email(html, "jcat.ru") == "info@jcat.ru"


def test_email_prefers_sales_first():
    html = "info@example.ru sales@example.ru"
    assert extract_email(html, "example.ru") == "sales@example.ru"


def test_email_skips_blacklisted_roles():
    """pdn-adfact@ (запросы по персданным), concurs@ (тендеры), hr@ — это
    не адреса для коммерческого предложения."""
    html = "pdn-adfact@mediascope.net team@mediascope.net"
    assert extract_email(html, "mediascope.net") == "team@mediascope.net"


def test_email_domain_compared_strictly_not_by_substring():
    """Сравнение подстрокой на ony.ru засчитывало anthony@partner.com как
    "адрес на своём домене" — потому что "ony" входит в "anthony"."""
    html = "anthony@partner.com hello@ony.ru"
    assert extract_email(html, "ony.ru") == "hello@ony.ru"


def test_email_domain_without_dot_does_not_crash():
    """Домен без точки ронял выбор email с IndexError."""
    assert extract_email("hello@example.ru", "localhost") == "hello@example.ru"


def test_email_rejects_js_library_versions():
    """Регулярка на email ловит и "player@1.8.0.js" из <script src=...>."""
    assert not _is_plausible_email("player@1.8.0.js")
    assert not _is_plausible_email("swiper@8.4.5.min.js")
    assert _is_plausible_email("hello@depotwpf.ru")


def test_email_rejects_tracking_domains():
    assert not _is_plausible_email("a1b2c3@stacks.vk-portal.net")


def test_email_rank_is_deterministic():
    """Ранг — кортеж (приоритет роли, длина): при равном приоритете выбор
    не должен зависеть от порядка обхода множества."""
    assert _email_rank("sales@x.ru") < _email_rank("info@x.ru")
    assert _email_rank("info@x.ru") < _email_rank("buh@x.ru")


# --- перебор вариантов адреса ---------------------------------------------

def test_url_variants_cover_www_and_http():
    """Internor живёт только на http://www. — https и голый домен отдают
    заглушку Aliyun. Скрапер, ходящий только по https, объявит его мёртвым."""
    variants = _url_variants("internor-mach.com")
    assert "https://internor-mach.com" in variants
    assert "http://www.internor-mach.com" in variants


def test_url_variants_keeps_explicit_scheme():
    assert _url_variants("https://example.com") == ["https://example.com"]


def test_url_variants_handles_www_prefix():
    variants = _url_variants("www.nttzmt.com")
    assert "https://www.nttzmt.com" in variants
    assert "https://nttzmt.com" in variants


# --- очистка текста перед LLM ---------------------------------------------

def test_strip_spam_removes_adult_seo_keywords():
    """Главная jillionsupply.com нашпигована посторонними SEO-ключами прямо
    между товарными ссылками. Если скормить их модели, лексика может всплыть
    в холодном письме."""
    text = "Quality bearings supplier. Buy dildo online cheap. We ship worldwide."
    cleaned = strip_spam(text)
    assert "dildo" not in cleaned.lower()
    assert "bearings" in cleaned


def test_strip_spam_keeps_clean_text_intact():
    text = "We manufacture carbide inserts for metalworking."
    assert strip_spam(text) == text


# --- гигиена вывода модели -------------------------------------------------

def test_sanitize_strips_markdown_and_unicode_junk():
    """В сданный CSV уехали **звёздочки**, неразрывные дефисы U+2011 и
    узкие пробелы U+202F — они ломают наивный поиск и вёрстку письма."""
    dirty = "**Топ‑1** агентство с опытом"
    clean = sanitize_personalization(dirty, {})
    assert "**" not in clean
    assert "‑" not in clean
    assert " " not in clean
    assert "Топ-1" in clean


def test_sanitize_drops_search_suffix_leak():
    """В персонализацию утекали служебные суффиксы из seed-списка:
    «Компания «Тарантул агентство маркетинг» обещает…»"""
    clean = sanitize_personalization(
        "Компания «Тарантул агентство маркетинг» обещает результат.",
        {"Тарантул агентство маркетинг": "Тарантул"},
    )
    assert "агентство маркетинг" not in clean
    assert "Тарантул" in clean


# --- имя контакта ----------------------------------------------------------

def test_contact_name_rejects_person_from_portfolio():
    """У Litera.Studio в контакт уехал «Ильи Чируна» — владелец арт-галереи,
    ДЛЯ которой студия делала открытки. Признак: имя дословно есть в тексте
    персонализации, то есть взято из рассказа о клиентах."""
    personalization = (
        "Наша студия создала набор открыток для арт-галереи Ильи Чируна "
        "и фирменный стиль «ПРОМАКТИВ»."
    )
    assert normalize_contact_name("Ильи Чируна", personalization) == ""


def test_contact_name_shortens_full_russian_name():
    """«Привет, Новицкий Сергей Владимирович!» в холодном письме звучит
    как повестка. Обращаться нужно по имени."""
    assert normalize_contact_name("Новицкий Сергей Владимирович") == "Сергей"


def test_contact_name_drops_patronymic():
    assert normalize_contact_name("Максим Андреевич") == "Максим"


def test_contact_name_takes_first_of_name_surname():
    assert normalize_contact_name("Иван Петров") == "Иван"


def test_contact_name_rejects_phrase():
    assert normalize_contact_name("контактное лицо не указано на сайте") == ""


def test_contact_name_keeps_single_name():
    assert normalize_contact_name("Сергей") == "Сергей"


def test_contact_name_survives_clean_personalization():
    """Имя не должно выбрасываться, если оно просто не встречается в тексте."""
    assert normalize_contact_name("Сергей", "Агентство работает с 2005 года.") == "Сергей"


# --- детект незаполненного шаблона ----------------------------------------

def test_template_detected_by_placeholder_phrase():
    """Главная rocket-media.ru — демо-рыба WordPress-темы: модель честно
    пересказала её и в базу уехал несуществующий продукт "Launchify".
    Плейсхолдер темы остался прямо в тексте."""
    text = (
        "Display a site wide notice to your visitors here We've just launched "
        "a new product. Launchify has everything you need."
    )
    assert looks_like_template(text, "https://rocket-media.ru/")


def test_template_detected_by_russian_domain_without_cyrillic():
    """Российское агентство, на сайте которого нет ни одной кириллической
    буквы, — это непереведённая демо-тема, а не настоящий сайт."""
    text = "The perfect way to launch your next project with our expert guidance."
    assert looks_like_template(text, "https://example.ru/")


def test_real_russian_site_is_not_template():
    text = "Агентство полного цикла: медиа, креатив, аналитика с 2005 года."
    assert not looks_like_template(text, "https://example.ru/")


def test_foreign_english_site_is_not_template():
    """Английский текст на нероссийском домене — норма, не шаблон."""
    text = "We manufacture carbide inserts for metalworking since 1998."
    assert not looks_like_template(text, "https://jatcarbide.com/")


def test_empty_text_is_not_template():
    assert not looks_like_template("", "https://example.ru/")


if __name__ == "__main__":
    failed = 0
    for label, fn in sorted(globals().items()):
        if label.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ok    {label}")
            except AssertionError as e:
                failed += 1
                print(f"  FAIL  {label}: {e or 'assertion failed'}")
    print()
    print("всё зелёное" if not failed else f"провалено тестов: {failed}")
    raise SystemExit(1 if failed else 0)
