"""app.cli 的 reflect 子命令:装配薄壳测试(注入 fake session/gemini,离线)。
编排逻辑本身在 tests/services/test_reflection_service.py 已覆盖,这里只测 CLI
参数解析 + 装配 + 摘要打印。
"""
import datetime as dt

import pytest

from app.cli import build_parser, cmd_reflect
from app.store.db import init_db, make_engine, make_session_factory
from app.store.repos.paper_repo import add_fill

D1 = dt.date(2026, 6, 1)
D2 = dt.date(2026, 6, 2)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_cmd_reflect_prints_zero_when_nothing_closed(session, capsys):
    args = build_parser().parse_args(["reflect"])
    rc = cmd_reflect(args, session=session, gemini_client=None)
    assert rc == 0
    out = capsys.readouterr().out
    assert "新增 0 条" in out


def test_cmd_reflect_prints_created_review_titles(session, capsys):
    add_fill(session, 1, D1, "AAPL", "buy", 100, 10.0)
    add_fill(session, 2, D2, "AAPL", "sell", 100, 15.0)
    session.commit()

    args = build_parser().parse_args(["reflect"])
    rc = cmd_reflect(args, session=session, gemini_client=None)

    assert rc == 0
    out = capsys.readouterr().out
    assert "新增 1 条" in out
    assert "AAPL" in out


def test_build_parser_reflect_has_no_required_args():
    args = build_parser().parse_args(["reflect"])
    assert args.command == "reflect"
