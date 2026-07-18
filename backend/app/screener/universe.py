from pathlib import Path

DEFAULT_UNIVERSE = [
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "AMD", "NFLX",
    "CRM", "ORCL", "ADBE", "COST", "JPM", "V", "MA", "UNH", "LLY", "XOM",
    "WMT", "HD", "PG", "KO", "PEP", "BAC", "DIS", "CSCO", "INTC", "QCOM",
]


def load_universe(path=None) -> list:
    """从文件读股票池(每行一个代码,# 开头为注释);path 为 None 用默认池。"""
    if path is None:
        return list(DEFAULT_UNIVERSE)
    lines = Path(path).read_text().splitlines()
    symbols = [ln.strip().upper() for ln in lines]
    symbols = [s for s in symbols if s and not s.startswith("#")]
    if not symbols:
        raise ValueError(f"universe file {path} contains no symbols")
    return symbols
