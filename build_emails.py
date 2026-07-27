"""
build_emails.py — мёржит output/leads_final.csv с шаблоном email_sequence.md
в готовые к отправке письма (output/emails_ready.csv).

Раньше это был единственный шаг всего пайплайна, который никогда не
выполнялся: база и цепочка писем существовали как два острова, и первый же
реальный прогон "подставь строку базы в шаблон" ломался сразу на нескольких
местах — {{имя}} нечем заполнить (колонки не было), а персонализация
заканчивается точкой там, где шаблон ждал обрывок фразы перед тире
("...кейсов. — поэтому пишу вам"). Подробности — в независимом ревью
(High: "База и письма не стыкуются", High: "Шаблон письма 1 ломается на 48
из 49 персонализаций").

Запуск:
    python build_emails.py

Результат:
    output/emails_ready.csv — по одной строке на компанию с готовыми темой
    и телом всех трёх писем; в конце прогона печатается сводка, у скольких
    строк сработал фолбэк по имени/персонализации — это не ошибка, но
    прозрачность здесь важнее тишины (тот же принцип, что и в остальном
    пайплайне: пустая ячейка честнее выдуманного факта).
"""

import csv
import re
from pathlib import Path

from finalize import COLUMNS_RU

LEADS_PATH = Path(__file__).parent / "output" / "leads_final.csv"
TEMPLATE_PATH = Path(__file__).parent / "email_sequence.md"
OUTPUT_PATH = Path(__file__).parent / "output" / "emails_ready.csv"

# Персонализация иногда честно приходит как "недостаточно данных для
# персонализации" (см. generate_personalization() в collect_leads.py) —
# в письме это должно вести себя как отсутствие персонализации, а не
# попадать в текст письма буквально.
_NO_DATA_MARKERS = (
    "недостаточно данных для персонализации",
    "not enough data for personalization",
)


def _looks_like_no_data(text: str) -> bool:
    normalized = text.strip().strip(".!").lower()
    return not normalized or any(marker in normalized for marker in _NO_DATA_MARKERS)


def _ensure_sentence(text: str) -> str:
    text = text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


# ТЗ: "Каждое письмо: тема + тело. Длина - до 120 слов." Шаблон письма 1
# укладывается в 87 слов, но персонализация добавляет к нему ещё 30-50 —
# и в собранном письме лимит превышался на 11 строках из 43 (максимум 137
# слов у Ipsos Comcon). Проверка шаблона этого не показывает: считать надо
# готовое письмо, а не заготовку.
WORD_LIMIT = 120

_WORD_RE = re.compile(r"\b[а-яА-ЯёЁa-zA-Z0-9]+\b")


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def split_sentences(text: str) -> list[str]:
    """Режем на предложения по знаку конца фразы."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p for p in parts if p.strip()]


def fit_to_word_limit(personalization: str, overhead: int, limit: int = WORD_LIMIT) -> str:
    """Укорачивает персонализацию так, чтобы письмо влезло в лимит слов.

    Урезаем именно персонализацию, а не текст письма: письмо — выверенный
    копирайтинг с лестницей CTA, а персонализация приходит от модели и её
    длина не гарантирована.

    Три шага по убыванию бережности: целиком -> по целым предложениям
    (сколько влезет) -> первое предложение, обрезанное по словам. Обрезка
    по словам — последняя мера, поэтому предложение при ней закрывается
    точкой, а не многоточием: письмо не должно выглядеть оборванным.
    """
    budget = limit - overhead
    if budget <= 0:
        return ""

    text = personalization.strip()
    if count_words(text) <= budget:
        return text

    kept: list[str] = []
    for sentence in split_sentences(text):
        candidate = kept + [sentence]
        if count_words(" ".join(candidate)) > budget:
            break
        kept = candidate
    if kept:
        return " ".join(kept)

    # Даже первое предложение не влезает — режем по словам.
    words = text.split()
    while words and count_words(" ".join(words)) > budget:
        words.pop()
    if not words:
        return ""
    return _ensure_sentence(" ".join(words).rstrip(",;:—-"))


def parse_template(md_text: str) -> list[dict]:
    """Разбирает email_sequence.md на три письма: тема + тело (всё между
    "**Тело:**" и следующим разделителем "---")."""
    sections = re.split(r"\n## Письмо \d+[^\n]*\n", md_text)[1:]
    letters = []
    for section in sections:
        subject_m = re.search(r"\*\*Тема:\*\*\s*(.+)", section)
        body_m = re.search(r"\*\*Тело:\*\*\s*\n\n(.+?)(?:\n---|\Z)", section, re.S)
        letters.append({
            "subject": subject_m.group(1).strip() if subject_m else "",
            "body": body_m.group(1).strip() if body_m else "",
        })
    if len(letters) != 3:
        raise ValueError(f"ожидалось 3 письма в шаблоне, найдено {len(letters)}")
    return letters


_TRANSITION = "Поэтому пишу именно вам, а не рассылаю по всей базе подряд."


def build_placeholders(
    contact_name: str, personalization: str, word_budget: int | None = None
) -> dict:
    """Считаем фолбэки честно: пустое имя — это факт о данных, а не повод
    выдумать "Иван". Возвращает и сами значения плейсхолдеров, и флаги —
    сработал ли фолбэк по имени/персонализации (для итоговой сводки).

    word_budget — сколько слов остаётся на блок персонализации, чтобы всё
    письмо влезло в лимит ТЗ. None — не ограничивать.
    """
    name = contact_name.strip()
    no_data = _looks_like_no_data(personalization)

    if name:
        tema = f"{name}, откуда у вас новые B2B-клиенты?"
        privet = f"Привет, {name}!"
        vstuplenie_2 = f"{name}, возвращаюсь с другого угла."
        vstuplenie_3 = f"{name}, не хочу быть навязчивой — это последнее письмо от меня по этой теме."
    else:
        tema = "Откуда у вас новые B2B-клиенты?"
        privet = "Здравствуйте!"
        vstuplenie_2 = "Возвращаюсь с другого угла — ещё один заход, вдруг он окажется актуальнее."
        vstuplenie_3 = "Не хочу быть навязчивой — это последнее письмо от меня по этой теме."

    trimmed = False
    if no_data:
        personalizatsiya_blok = (
            "Пишу вам не из общей рассылки, а прицельно — увидела вас в подборке "
            "digital и маркетинговых агентств, которые сами понимают ценность "
            "лидогенерации через холодный аутрич."
        )
    else:
        fact = _ensure_sentence(personalization)
        if word_budget is not None:
            # Фраза-переход входит в тот же бюджет, поэтому вычитаем её слова.
            fitted = fit_to_word_limit(fact, count_words(_TRANSITION), word_budget)
            trimmed = count_words(fitted) < count_words(fact)
            fact = fitted
        personalizatsiya_blok = f"{fact} {_TRANSITION}".strip() if fact else _TRANSITION

    return {
        "values": {
            "{{тема}}": tema,
            "{{приветствие}}": privet,
            "{{вступление_2}}": vstuplenie_2,
            "{{вступление_3}}": vstuplenie_3,
            "{{персонализация}}": personalizatsiya_blok,
            "{{подпись}}": "",
        },
        "used_name_fallback": not name,
        "used_personalization_fallback": no_data,
        "trimmed_personalization": trimmed,
    }


def render(template: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        template = template.replace(placeholder, value)
    # {{подпись}} намеренно пустая (задаётся при реальной рассылке) — но
    # пустая строка-плейсхолдер не должна оставлять двойной перевод строки.
    return re.sub(r"\n{3,}", "\n\n", template).strip()


def main() -> None:
    letters = parse_template(TEMPLATE_PATH.read_text(encoding="utf-8"))

    # leads_final.csv — артефакт для сдачи, шапка в нём на русском (см.
    # COLUMNS_RU в finalize.py). Читаем через тот же маппинг, чтобы имя
    # колонки было объявлено в одном месте, а не продублировано строкой.
    with open(LEADS_PATH, encoding="utf-8") as f:
        rows = [
            {en: raw.get(ru, "") for en, ru in COLUMNS_RU.items()}
            for raw in csv.DictReader(f)
        ]

    stats = {
        "no_email": 0,
        "name_fallback": 0,
        "personalization_fallback": 0,
        "trimmed": 0,
        "over_limit": 0,
    }
    out_rows = []

    for row in rows:
        if not row.get("email"):
            stats["no_email"] += 1
            continue

        # Бюджет считаем по этой же строке: длина приветствия зависит от того,
        # известно ли имя, поэтому overhead у строк разный. Рендерим письмо 1
        # с пустой персонализацией и смотрим, сколько слов уже занято.
        probe = build_placeholders(row.get("contact_name", ""), "")
        probe["values"]["{{персонализация}}"] = ""
        overhead = count_words(render(letters[0]["body"], probe["values"]))

        built = build_placeholders(
            row.get("contact_name", ""),
            row.get("personalization", ""),
            word_budget=WORD_LIMIT - overhead,
        )
        stats["name_fallback"] += built["used_name_fallback"]
        stats["personalization_fallback"] += built["used_personalization_fallback"]
        stats["trimmed"] += built["trimmed_personalization"]

        out_row = {"name": row["name"], "email": row["email"]}
        for i, letter in enumerate(letters, start=1):
            out_row[f"subject_{i}"] = render(letter["subject"], built["values"])
            out_row[f"body_{i}"] = render(letter["body"], built["values"])
            if count_words(out_row[f"body_{i}"]) > WORD_LIMIT:
                stats["over_limit"] += 1
        out_rows.append(out_row)

    fieldnames = ["name", "email"]
    for i in range(1, len(letters) + 1):
        fieldnames += [f"subject_{i}", f"body_{i}"]

    with open(OUTPUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(out_rows)

    print(f"писем собрано: {len(out_rows)}")
    print(f"без email (пропущено, отправлять некуда): {stats['no_email']}")
    print(f"без имени контакта — фолбэк 'Здравствуйте!': {stats['name_fallback']}")
    print(f"без персонализации — нейтральный фолбэк: {stats['personalization_fallback']}")
    print(
        f"персонализация укорочена под лимит {WORD_LIMIT} слов: {stats['trimmed']}"
    )
    # Должен быть ноль: если здесь не ноль, письмо нарушает требование ТЗ.
    print(f"писем сверх лимита {WORD_LIMIT} слов: {stats['over_limit']}")


if __name__ == "__main__":
    main()
