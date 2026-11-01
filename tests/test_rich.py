"""Rich-сообщения: таблицы, абзацы и запасной путь, если Telegram отказал."""
from __future__ import annotations

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendRichMessage

from app import rich
from app.config import get_config
from app.rich import Doc, Table, paragraphs
from tests.test_routers_smoke import FakeSession, build, db, onboard, send  # noqa: F401


def test_paragraphs_turn_newlines_into_br_and_blank_lines_into_paragraphs():
    assert paragraphs("a\nb\n\nc") == "<p>a<br>b</p><p>c</p>"
    assert paragraphs("\n\n") == ""


def test_table_rich_escapes_and_aligns():
    html = Table(["Продукт", "ккал"], [["Суп <b>&", "120"]], "lr", total=["Итого", "120"]).rich()
    assert html.startswith("<table bordered striped compact>")
    assert "Суп &lt;b&gt;&amp;" in html and "<b>&" not in html
    assert '<td align="right">120</td>' in html
    assert '<td align="left"><b>Итого</b></td>' in html


def test_table_plain_is_monospace_block_without_raw_html():
    plain = Table(["Продукт", "ккал"], [["Суп <b>&", "120"], ["x" * 40, "5"]], "lr",
                  total=["Итого", "125"]).plain()
    assert plain.startswith("<pre>") and plain.endswith("</pre>")
    assert "Суп &lt;b&gt;&amp;" in plain
    assert "…" in plain                                   # длинное имя обрезано
    assert all(len(line) <= 40 for line in plain[5:-6].split("\n"))


def test_doc_renders_both_ways():
    doc = Doc().text("📊 Итоги", "строка 2").table(["A"], [["1"]]).text("", "хвост")
    assert doc.rich() == ('<p>📊 Итоги<br>строка 2</p><table bordered striped compact><tr>'
                          '<th align="left">A</th></tr><tr><td align="left">1</td></tr></table>'
                          '<p>хвост</p>')
    assert "📊 Итоги\nстрока 2" in doc.plain() and "<pre>" in doc.plain()
    assert str(doc) == doc.plain()


@pytest.mark.asyncio
async def test_day_summary_is_sent_as_rich_table(db):               # noqa: F811
    bot, dp, session = build(db)
    await onboard(dp, bot)
    session.methods.clear(); session.rich.clear()
    await send(dp, bot, "/today")
    assert "SendRichMessage" in session.methods
    assert "Сегодня" in session.rich[-1]


@pytest.mark.asyncio
async def test_flag_off_sends_plain_text(db, monkeypatch):           # noqa: F811
    monkeypatch.setattr(get_config(), "rich_messages", False)
    bot, dp, session = build(db)
    await onboard(dp, bot)
    session.methods.clear(); session.rich.clear()
    await send(dp, bot, "/today")
    assert "SendRichMessage" not in session.methods and not session.rich
    assert any("Сегодня" in t for t in session.texts)


@pytest.mark.asyncio
async def test_telegram_refusal_falls_back_to_plain(db, monkeypatch):  # noqa: F811
    bot, dp, session = build(db)
    await onboard(dp, bot)
    original = session.make_request

    async def refuse(bot_, method, timeout=None):
        if isinstance(method, SendRichMessage):
            raise TelegramBadRequest(method=method, message="Bad Request: method is not supported")
        return await original(bot_, method, timeout)
    monkeypatch.setattr(session, "make_request", refuse)
    session.texts.clear()
    await send(dp, bot, "/today")
    assert any("Сегодня" in t and "<p>" not in t for t in session.texts)     # ушло обычным текстом
    assert "SendMessage" in session.names()[-3:]
    assert not any("<p>" in t for t in session.texts[-1:])
