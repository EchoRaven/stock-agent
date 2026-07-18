import pytest

from app.screener.universe import DEFAULT_UNIVERSE, load_universe


def test_default_universe():
    syms = load_universe(None)
    assert syms == DEFAULT_UNIVERSE
    assert len(syms) >= 20
    assert "AAPL" in syms
    assert syms is not DEFAULT_UNIVERSE  # 返回副本,防止调用方改坏默认池


def test_load_from_file(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("aapl\n# comment\n\nMSFT\n")
    assert load_universe(f) == ["AAPL", "MSFT"]


def test_empty_file_rejected(tmp_path):
    f = tmp_path / "u.txt"
    f.write_text("# only comments\n")
    with pytest.raises(ValueError):
        load_universe(f)
