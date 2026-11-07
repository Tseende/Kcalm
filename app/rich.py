"""Красивые ответы: таблицы и абзацы через rich-сообщения Bot API 10.1+.

Ответ собирается один раз как Doc: абзацы в том же HTML, что и раньше, и таблицы. Если rich
включены, уходит sendRichMessage с настоящей таблицей. Если выключены или Telegram отказал
(старый сервер Bot API), тот же Doc отправляется обычным сообщением, а таблица превращается
в моноширинный блок: ответ не теряется ни в каком случае.

В rich HTML обычный перенос строки схлопывается в пробел, поэтому абзацы и <br> расставляются
здесь, а не в местах вызова.
"""
from __future__ import annotations

import html as htmllib
import logging
import re
from dataclasses import dataclass, field

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest, TelegramNotFound
from aiogram.methods import EditMessageText, SendMessage, SendRichMessage
from aiogram.types import (BufferedInputFile, InlineKeyboardMarkup, InputMediaPhoto,
                           InputRichMessage, InputRichMessageMedia, Message)

from app.config import get_config

log = logging.getLogger(__name__)

PLAIN_LIMIT = 4000                 # запас до 4096 знаков обычного сообщения
MAX_PLAIN_CELL = 20                # длинные названия в моноширинной таблице обрезаются


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]
    align: str = ""                 # по символу на колонку: l, r, c; пусто = слева
    total: list[str] | None = None  # итоговая строка, жирная

    def _align(self, index: int) -> str:
        return self.align[index] if index < len(self.align) else "l"

    def rich(self) -> str:
        def cell(tag: str, index: int, value: str, bold: bool = False) -> str:
            align = {"l": "left", "r": "right", "c": "center"}[self._align(index)]
            text = htmllib.escape(value, quote=False)
            return f'<{tag} align="{align}">{f"<b>{text}</b>" if bold else text}</{tag}>'

        out = ["<table bordered striped compact><tr>"
               + "".join(cell("th", i, h) for i, h in enumerate(self.headers)) + "</tr>"]
        for row in self.rows:
            out.append("<tr>" + "".join(cell("td", i, v) for i, v in enumerate(row)) + "</tr>")
        if self.total:
            out.append("<tr>" + "".join(cell("td", i, v, True)
                                        for i, v in enumerate(self.total)) + "</tr>")
        return "".join(out) + "</table>"

    def plain(self) -> str:
        def short(value: str) -> str:
            return value if len(value) <= MAX_PLAIN_CELL else value[:MAX_PLAIN_CELL - 1] + "…"

        grid = [[short(h) for h in self.headers]]
        grid += [[short(v) for v in row] for row in self.rows]
        if self.total:
            grid.append([short(v) for v in self.total])
        widths = [max(len(r[i]) for r in grid if i < len(r)) for i in range(len(self.headers))]

        def line(row: list[str]) -> str:
            cells = []
            for i, width in enumerate(widths):
                value = row[i] if i < len(row) else ""
                align = self._align(i)
                cells.append(value.rjust(width) if align == "r" else
                             value.center(width) if align == "c" else value.ljust(width))
            return "  ".join(cells).rstrip()

        rule = "─" * (sum(widths) + 2 * (len(widths) - 1))
        body = [line(grid[0]), rule] + [line(r) for r in grid[1:1 + len(self.rows)]]
        if self.total:
            body += [rule, line(grid[-1])]
        return "<pre>" + htmllib.escape("\n".join(body), quote=False) + "</pre>"


def paragraphs(text: str) -> str:
    """Текст с \\n (HTML бота) → абзацы <p> и <br> для rich."""
    blocks = [b for b in re.split(r"\n{2,}", text.strip("\n")) if b.strip()]
    return "".join("<p>" + b.strip("\n").replace("\n", "<br>") + "</p>" for b in blocks)


@dataclass
class Raw:
    """Готовый блок: rich-вариант и запасной обычный текст."""
    html: str
    fallback: str


@dataclass
class Image:
    """Картинка, встроенная в сообщение (график), вместе с таблицами и текстом."""
    key: str
    data: bytes

    @property
    def html(self) -> str:
        return f'<figure><img src="tg://photo?id={self.key}"/></figure>'


def _li(items: list[str]) -> str:
    return "".join(f"<li>{it}</li>" for it in items)


@dataclass
class Doc:
    parts: list[str | Table | Raw | Image] = field(default_factory=list)

    def text(self, *lines: str) -> "Doc":
        """Абзацы: строки через \\n, пустая строка начинает новый абзац. HTML уже экранирован."""
        joined = "\n".join(lines)
        if joined.strip():
            self.parts.append(joined)
        return self

    def table(self, headers: list[str], rows: list[list[str]], align: str = "",
              total: list[str] | None = None) -> "Doc":
        self.parts.append(Table(headers, rows, align, total))
        return self

    def heading(self, text: str, level: int = 3) -> "Doc":
        self.parts.append(Raw(f"<h{level}>{text}</h{level}>", f"<b>{text}</b>"))
        return self

    def bullets(self, items: list[str]) -> "Doc":
        """Маркированный список; пункты — готовый HTML."""
        if items:
            self.parts.append(Raw(f"<ul>{_li(items)}</ul>", "\n".join(f"• {i}" for i in items)))
        return self

    def quote(self, text: str) -> "Doc":
        """Выделенная цитата-акцент (главная мысль, действие на завтра)."""
        self.parts.append(Raw(f"<blockquote>{text}</blockquote>", text))
        return self

    def details(self, summary: str, items: list[str]) -> "Doc":
        """Сворачиваемый блок: подробности не мешают, пока их не откроют."""
        if items:
            self.parts.append(Raw(
                f"<details><summary>{summary}</summary><ul>{_li(items)}</ul></details>",
                f"<b>{summary}</b>\n" + "\n".join(f"• {i}" for i in items)))
        return self

    def divider(self) -> "Doc":
        self.parts.append(Raw("<hr/>", "──────────"))
        return self

    def image(self, data: bytes | None) -> "Doc":
        """Встроить картинку в текущее место сообщения; None — молча пропустить."""
        if data:
            self.parts.append(Image(f"img{len(self.images()) + 1}", data))
        return self

    def images(self) -> list[Image]:
        return [p for p in self.parts if isinstance(p, Image)]

    def rich(self) -> str:
        out = []
        for p in self.parts:
            out.append(paragraphs(p) if isinstance(p, str)
                       else p.html if isinstance(p, (Raw, Image)) else p.rich())
        return "".join(out)

    def plain(self) -> str:
        out = []
        for p in self.parts:
            if isinstance(p, Image):
                continue
            out.append(p if isinstance(p, str) else p.fallback if isinstance(p, Raw) else p.plain())
        return "\n".join(out)

    def __str__(self) -> str:
        return self.plain()


def enabled() -> bool:
    return get_config().rich_messages


def _input(doc: Doc) -> InputRichMessage:
    images = doc.images()
    media = [InputRichMessageMedia(id=i.key, media=InputMediaPhoto(
        media=BufferedInputFile(i.data, filename=f"{i.key}.png"))) for i in images] or None
    return InputRichMessage(html=doc.rich(), media=media)


async def _send_images_apart(msg: Message, doc: Doc) -> None:
    """Запасной путь без rich: график уходит отдельным фото после текста."""
    for image in doc.images():
        await msg.answer_photo(BufferedInputFile(image.data, filename=f"{image.key}.png"))


async def answer(msg: Message, content: str | Doc, reply_markup=None) -> Message:
    """Отправить ответ: строка идёт как раньше, Doc — rich-сообщением с запасным путём."""
    if isinstance(content, str):
        return await msg.answer(content[:PLAIN_LIMIT], reply_markup=reply_markup)
    if enabled():
        try:
            return await msg.answer_rich(_input(content), reply_markup=reply_markup)
        except (TelegramBadRequest, TelegramNotFound):
            log.warning("rich-сообщение отклонено, отправляю обычным текстом", exc_info=True)
    sent = await msg.answer(content.plain()[:PLAIN_LIMIT], reply_markup=reply_markup)
    await _send_images_apart(msg, content)
    return sent


async def send(bot, chat_id: int, content: str | Doc, reply_markup=None) -> Message:
    """Отправить документ или строку пользователю без входящего сообщения (напоминания)."""
    if isinstance(content, str):
        return await bot.send_message(chat_id, content[:PLAIN_LIMIT], reply_markup=reply_markup)
    if enabled():
        try:
            return await bot.send_rich_message(chat_id, _input(content), reply_markup=reply_markup)
        except (TelegramBadRequest, TelegramNotFound):
            log.warning("rich-сообщение отклонено, отправляю обычным текстом", exc_info=True)
    sent = await bot.send_message(chat_id, content.plain()[:PLAIN_LIMIT], reply_markup=reply_markup)
    for image in content.images():
        await bot.send_photo(chat_id, BufferedInputFile(image.data, filename=f"{image.key}.png"))
    return sent


async def edit(target: Message, content: str | Doc, reply_markup=None):
    """Заменить текст сообщения (в том числе статусного «распознаю…») на ответ."""
    if isinstance(content, str):
        return await target.edit_text(content[:PLAIN_LIMIT], reply_markup=reply_markup)
    if enabled():
        try:
            return await target.bot.edit_message_text(
                chat_id=target.chat.id, message_id=target.message_id,
                rich_message=_input(content), reply_markup=reply_markup)
        except (TelegramBadRequest, TelegramNotFound):
            log.warning("правка в rich отклонена, пишу обычным текстом", exc_info=True)
    return await target.edit_text(content.plain()[:PLAIN_LIMIT], reply_markup=reply_markup)


# ------------------------------------------------- единое оформление для всех сообщений

def strip_tags(text: str) -> str:
    """HTML бота → простой текст: теги убраны, сущности раскрыты (последний запасной вариант отправки)."""
    return htmllib.unescape(re.sub(r"<[^>]+>", "", text or ""))


class RichMiddleware(BaseRequestMiddleware):
    """Любой обычный текст бота (в том числе напоминания планировщика) уходит rich-сообщением.

    Так оформление одинаковое везде, а не только там, где вызван rich.answer. Если Telegram
    отказал (старый Bot API, неподдерживаемая разметка), сообщение уходит как раньше обычным
    текстом: ответ не теряется.
    """

    async def __call__(self, make_request, bot, method):
        converted = _to_rich(method)
        if converted is None:
            return await make_request(bot, method)
        try:
            return await make_request(bot, converted)
        except (TelegramBadRequest, TelegramNotFound):
            log.info("rich отклонён для %s, шлю обычным текстом", type(method).__name__)
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as e:
            if "parse" not in str(e).lower() and "entities" not in str(e).lower():
                raise
            log.warning("HTML не разобран и в обычном сообщении, шлю без разметки: %s", type(method).__name__)
            return await make_request(bot, method.model_copy(update={
                "text": strip_tags(method.text), "parse_mode": None}))


def _plain_markup(markup) -> bool:
    return markup is None or isinstance(markup, InlineKeyboardMarkup)


def _html_text(method) -> bool:
    mode = method.parse_mode
    return mode is None or mode == "HTML" or type(mode).__name__ == "Default"


def _to_rich(method):
    if not enabled():
        return None
    if isinstance(method, SendMessage):
        if (not method.text or method.entities or not _plain_markup(method.reply_markup)
                or not _html_text(method)):
            return None
        return SendRichMessage(
            chat_id=method.chat_id, message_thread_id=method.message_thread_id,
            rich_message=InputRichMessage(html=paragraphs(method.text)),
            disable_notification=method.disable_notification,
            protect_content=method.protect_content,
            reply_parameters=method.reply_parameters, reply_markup=method.reply_markup)
    if isinstance(method, EditMessageText):
        if (not method.text or method.entities or not _plain_markup(method.reply_markup)
                or not _html_text(method) or method.inline_message_id):
            return None
        return EditMessageText(
            chat_id=method.chat_id, message_id=method.message_id,
            rich_message=InputRichMessage(html=paragraphs(method.text)),
            reply_markup=method.reply_markup)
    return None
