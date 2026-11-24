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


def test_expandable_contains_table_and_has_readable_plain_fallback():
    details = Doc().table(['День', 'ккал'], [['Пн', '1200'], ['Вт', '1300']], align='lr')
    doc = Doc().text('Итог недели').quote('Главный совет').expandable('📋 По дням <&>', details)
    rendered = doc.rich()
    assert '<blockquote expandable><p><b>📋 По дням &lt;&amp;&gt;</b></p><table' in rendered
    assert rendered.index('Главный совет') < rendered.index('<blockquote expandable>')
    plain = doc.plain()
    assert 'Пн' in plain and '1200' in plain and '1300' in plain
    assert '<blockquote' not in plain and '<table' not in plain and '<pre>' in plain


async def test_week_report_puts_main_advice_before_collapsed_daily_table(db, monkeypatch):
    from datetime import date
    from types import SimpleNamespace
    from app.services import insights, report
    from app.storage import repo
    bot, dp, session = build(db)
    await onboard(dp, bot)
    user = repo.get_user_by_tg(db, 1)
    monkeypatch.setattr(insights, 'week_tips', lambda facts: (['получилось'], [
        SimpleNamespace(html='Один главный совет'), SimpleNamespace(html='Дополнительный совет')]))
    doc = report.week_doc(db, user, date(2026, 11, 24), ['Неделя', 'Вес: есть динамика'])
    html = doc.rich()
    collapsed = html[html.index('<blockquote expandable>'):]
    assert html.index('Главный шаг') < html.index('<blockquote expandable>')
    assert 'Один главный совет' in html and html.count('Один главный совет') == 1
    assert 'Показать по дням' in collapsed and '<table' in collapsed and 'Шаги' in collapsed
    assert 'Подробности' in html
    assert 'Показать по дням' in doc.plain() and '<blockquote expandable>' not in doc.plain()


async def test_week_report_api_rejection_preserves_daily_data_in_plain_text(db, monkeypatch):
    bot, dp, session = build(db)
    await onboard(dp, bot)
    original = session.make_request
    async def refuse(bot_, method, timeout=None):
        if isinstance(method, SendRichMessage):
            raise TelegramBadRequest(method=method, message='unsupported blocks')
        return await original(bot_, method, timeout)
    monkeypatch.setattr(session, 'make_request', refuse)
    session.texts.clear()
    await send(dp, bot, '/week')
    assert any('Показать по дням' in text and '<pre>' in text for text in session.texts)
