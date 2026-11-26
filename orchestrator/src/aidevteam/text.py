from __future__ import annotations


def clean_text(value: str) -> str:
    """Вернуть строку, которую можно записать в UTF-8-файл.

    Если stdin/argv прочитаны в локали без UTF-8 (например, контейнер с LANG=C),
    Python декодирует байты через surrogateescape и оставляет суррогаты
    (U+DC80..U+DCFF). Прямая запись такого текста падает с UnicodeEncodeError,
    поэтому возвращаем исходные байты обратно и декодируем их как UTF-8.
    Битые байты заменяются на U+FFFD — текст остаётся валидным.
    """
    if not any("\ud800" <= ch <= "\udfff" for ch in value):
        return value
    raw = value.encode("utf-8", "surrogateescape")
    return raw.decode("utf-8", "replace")
