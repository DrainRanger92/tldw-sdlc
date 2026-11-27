"""Корневой роутер TLDW.

Единая точка подключения слоя handlers к Dispatcher: корневой роутер получает
сервис конспекта, allowlist из настроек и — когда шаблоны подключены — сервис
шаблонов с разбором описания. Сборка не ходит в сеть.

Порядок регистрации важен (T-007): обработчик шаблона подключается **раньше**
обработчика ссылок. Пока пользователь описывает шаблон, сообщение со ссылкой
должно попасть в сценарий шаблона и не запускать обработку видео; обработчик
ссылок получает только те сообщения, которые сценарий не забрал себе.

Проводка шаблона в генерацию (T-008): когда зависимости шаблона переданы,
``template_service`` уходит и в обработчик шаблона, и в обработчик ссылок —
последний снимает активный шаблон отправителя и передаёт его в сервис конспекта.
Старый вызов ``create_router(service)`` остаётся валидным.
"""

from aiogram import Router

from tldw.handlers.link import LinkHandler, SummaryProcessor
from tldw.handlers.template import TemplateHandler, TemplateParser
from tldw.services.template import TemplateService


def create_router(
    service: SummaryProcessor,
    *,
    allowed_user_ids: tuple[int, ...] = (),
    template_service: TemplateService | None = None,
    template_parser: TemplateParser | None = None,
) -> Router:
    """Создать корневой роутер: сначала шаблон, затем обработчик ссылок.

    :param service: сервис конспекта.
    :param allowed_user_ids: allowlist из ``Settings``; пустой — доступ всем.
    :param template_service: сервис шаблонов; без него сценарий шаблона не
        подключается, а обработчик ссылок не применяет пользовательский шаблон
        и поведение остаётся прежним.
    :param template_parser: разбор описания шаблона (LLM-адаптер).
    :raises ValueError: если задана только одна из зависимостей шаблона.
    """
    router = Router(name="tldw")
    if (template_service is None) != (template_parser is None):
        raise ValueError("нужны оба аргумента: template_service и template_parser")
    if template_service is not None and template_parser is not None:
        TemplateHandler(
            template_service,
            template_parser,
            allowed_user_ids=allowed_user_ids,
        ).register(router)
    LinkHandler(
        service,
        allowed_user_ids=allowed_user_ids,
        template_service=template_service,
    ).register(router)
    return router


__all__ = ["create_router"]
