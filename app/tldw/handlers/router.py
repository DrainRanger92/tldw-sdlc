"""Корневой роутер TLDW.

Единая точка подключения слоя handlers к Dispatcher: корневой роутер получает
сервис конспекта, allowlist из настроек и — когда шаблоны подключены — сервис
шаблонов с разбором описания и распознаванием речи. Сборка не ходит в сеть.

Порядок регистрации важен (T-007): обработчик шаблона подключается **раньше**
обработчика ссылок. Пока пользователь описывает шаблон, сообщение со ссылкой
должно попасть в сценарий шаблона и не запускать обработку видео; обработчик
ссылок получает только те сообщения, которые сценарий не забрал себе.

Проводка шаблона в генерацию (T-008): когда зависимости шаблона переданы,
``template_service`` уходит и в обработчик шаблона, и в обработчик ссылок —
последний снимает активный шаблон отправителя и передаёт его в сервис конспекта.
Голосовое описание (T-012) приходит в тот же обработчик шаблона через узкий
протокол ``SpeechTranscriber``; ``None`` оставляет только текстовый ввод.

Fail-open (T-016). ``create_router(service)`` без allowlist остаётся валидным
документированным поведением MVP — доступ всем, — но больше не молчит: пишется
:data:`tldw.access.MISSING_ALLOWLIST_WARNING`, а политика создаётся явным
:meth:`tldw.access.AccessPolicy.open`. Явный пустой кортеж — осознанный доступ
всем без предупреждения о забытом аргументе (стартовое предупреждение пишет
``bootstrap``).
"""

from aiogram import Router

from tldw.access import warn_if_allowlist_missing
from tldw.handlers.link import LinkHandler, SummaryProcessor
from tldw.handlers.template import SpeechTranscriber, TemplateHandler, TemplateParser
from tldw.services.template import TemplateService


def create_router(
    service: SummaryProcessor,
    *,
    allowed_user_ids: tuple[int, ...] | None = None,
    template_service: TemplateService | None = None,
    template_parser: TemplateParser | None = None,
    speech_transcriber: SpeechTranscriber | None = None,
) -> Router:
    """Создать корневой роутер: сначала шаблон, затем обработчик ссылок.

    :param service: сервис конспекта.
    :param allowed_user_ids: allowlist из ``Settings``; пустой — доступ всем.
        ``None`` — аргумент не передан: доступ тоже открыт (поведение MVP), но
        в лог пишется предупреждение, чтобы забытая проводка была видна.
    :param template_service: сервис шаблонов; без него сценарий шаблона не
        подключается, а обработчик ссылок не применяет пользовательский шаблон
        и поведение остаётся прежним.
    :param template_parser: разбор описания шаблона (LLM-адаптер).
    :param speech_transcriber: распознавание речи (адаптер); ``None`` — бот
        предлагает текстовый ввод вместо голосового.
    :raises ValueError: если задана только одна из зависимостей шаблона.
    """
    router = Router(name="tldw")
    if (template_service is None) != (template_parser is None):
        raise ValueError("нужны оба аргумента: template_service и template_parser")
    if allowed_user_ids is None:
        warn_if_allowlist_missing()
        resolved_user_ids: tuple[int, ...] = ()
    else:
        resolved_user_ids = allowed_user_ids
    if template_service is not None and template_parser is not None:
        TemplateHandler(
            template_service,
            template_parser,
            speech=speech_transcriber,
            allowed_user_ids=resolved_user_ids,
        ).register(router)
    LinkHandler(
        service,
        allowed_user_ids=resolved_user_ids,
        template_service=template_service,
    ).register(router)
    return router


def find_link_handler(router: Router) -> LinkHandler | None:
    """Найти обработчик ссылки, зарегистрированный роутером.

    Нужен сборке приложения (:meth:`tldw.bootstrap.Application.close`), чтобы
    отменить фоновые задачи при остановке. Возвращает ``None``, если обработчик
    ещё не зарегистрирован.
    """
    for registered in router.message.handlers:
        owner = getattr(registered.callback, "__self__", None)
        if isinstance(owner, LinkHandler):
            return owner
    return None


__all__ = ["create_router", "find_link_handler"]
