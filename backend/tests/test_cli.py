import datetime as dt

from app.cli import build_parser, cmd_backtest, cmd_screen
from app.data.base import PriceProvider
from tests.helpers import make_bars


class FakeProvider(PriceProvider):
    def get_daily_bars(self, symbol, start, end):
        base = 100.0 if symbol == "AAA" else 50.0
        bars = make_bars(start="2024-01-01", days=120, base=base)
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
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([])
