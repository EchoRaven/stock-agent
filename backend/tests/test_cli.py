import datetime as dt

import pytest

from app.cli import build_parser, cmd_backtest, cmd_report, cmd_screen
from app.data.base import PriceProvider
from app.services.decision_service import submit_decision
from app.store.db import init_db, make_engine, make_session_factory
from tests.helpers import make_bars, make_decision_payload


def _bars_covering(end: dt.date, base: float):
    # 锚定在请求的 end 附近往回生成,不用固定历史日期,避免随真实"今天"推移
    # 而与 cmd_screen 用 dt.date.today() 算出的请求区间失去交集(空 df 被
    # fetch_bars 判定为 empty 并跳过)。
    return make_bars(start=(end - dt.timedelta(days=250)).isoformat(), days=250, base=base)


class FakeProvider(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        base = 100.0 if symbol == "AAA" else 50.0
        bars = _bars_covering(end, base)
        mask = (bars.index.date >= start) & (bars.index.date <= end)
        return bars.loc[mask]


class PartialFailProvider(PriceProvider):
    """BBB 抓取直接抛异常,验证单标的失败不影响其余标的与整体命令结果。"""

    def get_daily_bars(self, symbol, start, end):
        if symbol == "BBB":
            raise RuntimeError("boom")
        bars = _bars_covering(end, 100.0)
        mask = (bars.index.date >= start) & (bars.index.date <= end)
        return bars.loc[mask]


def _universe_file(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("AAA\nBBB\n")
    return f


def test_screen_writes_report(tmp_path, capsys):
    args = build_parser().parse_args(
        ["screen", "--universe", str(_universe_file(tmp_path)),
         "--top", "2", "--reports-dir", str(tmp_path / "reports")]
    )
    assert cmd_screen(args, provider=FakeProvider()) == 0
    reports = list((tmp_path / "reports").glob("screen_*.md"))
    assert len(reports) == 1
    out = capsys.readouterr().out
    assert "AAA" in out


def test_backtest_writes_report_and_curve(tmp_path, capsys):
    args = build_parser().parse_args(
        ["backtest", "--start", "2024-04-01", "--end", "2024-05-31",
         "--universe", str(_universe_file(tmp_path)),
         "--reports-dir", str(tmp_path / "reports")]
    )
    assert cmd_backtest(args, provider=FakeProvider()) == 0
    md = list((tmp_path / "reports").glob("backtest_*.md"))
    csv = list((tmp_path / "reports").glob("backtest_*.csv"))
    assert len(md) == 1 and len(csv) == 1
    assert "回测报告" in capsys.readouterr().out


def test_parser_requires_command():
    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_screen_warns_and_still_succeeds_on_partial_fetch_failure(tmp_path, capsys):
    args = build_parser().parse_args(
        ["screen", "--universe", str(_universe_file(tmp_path)),
         "--top", "2", "--reports-dir", str(tmp_path / "reports")]
    )
    assert cmd_screen(args, provider=PartialFailProvider()) == 0
    reports = list((tmp_path / "reports").glob("screen_*.md"))
    assert len(reports) == 1
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "BBB" in out
    assert "boom" in out


def test_backtest_warns_and_still_succeeds_on_partial_fetch_failure(tmp_path, capsys):
    args = build_parser().parse_args(
        ["backtest", "--start", "2024-04-01", "--end", "2024-05-31",
         "--universe", str(_universe_file(tmp_path)),
         "--reports-dir", str(tmp_path / "reports")]
    )
    assert cmd_backtest(args, provider=PartialFailProvider()) == 0
    out = capsys.readouterr().out
    assert "[warn]" in out
    assert "BBB" in out


def test_top_zero_is_rejected_by_parser():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["screen", "--top", "0"])


def test_top_negative_is_rejected_by_parser():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["screen", "--top", "-1"])


def test_report_command(tmp_path, capsys):
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as session:
        submit_decision(session, make_decision_payload())
        args = build_parser().parse_args(
            ["report", "--date", "2026-07-17", "--reports-dir", str(tmp_path)])
        assert cmd_report(args, session=session) == 0
    files = list(tmp_path.glob("daily_*.md"))
    assert len(files) == 1
    out = capsys.readouterr().out
    assert "AAPL" in out and "[report saved]" in out


def test_report_rejects_bad_date():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args(["report", "--date", "not-a-date"])
