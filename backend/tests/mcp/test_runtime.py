import app.mcp.runtime as runtime
from app.data.cache import CachedPriceProvider
from app.data.fundamentals_edgar import EdgarFundamentalsProvider
from app.data.news_yahoo import YahooNewsProvider


def test_default_wiring(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKAGENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("STOCKAGENT_DB_PATH", str(tmp_path / "app.db"))
    monkeypatch.delenv("STOCKAGENT_FINNHUB_API_KEY", raising=False)
    assert isinstance(runtime.get_price_provider(), CachedPriceProvider)
    # 无 Finnhub key 时默认走免key的 Yahoo 新闻源(见 app.data.news_factory)。
    assert isinstance(runtime.get_news_provider(), YahooNewsProvider)
    assert isinstance(runtime.get_fundamentals_provider(), EdgarFundamentalsProvider)
    with runtime.open_session() as session:
        assert session.bind is not None
    assert (tmp_path / "app.db").exists()
