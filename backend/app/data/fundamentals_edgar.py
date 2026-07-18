import datetime as dt
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass

import httpx

from app.data.sanitize import sanitize_text

logger = logging.getLogger(__name__)

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssuranceType", "Revenues")
NET_INCOME_TAGS = ("NetIncomeLoss",)
EPS_TAGS = ("EarningsPerShareDiluted", "EarningsPerShareBasic")
VALID_FORMS = ("10-K", "10-Q")


@dataclass(frozen=True)
class FundamentalPoint:
    end: dt.date
    value: float
    fiscal: str  # 如 "Q1-2026" / "FY-2025"


@dataclass(frozen=True)
class FundamentalsSummary:
    symbol: str
    revenue: tuple = ()
    net_income: tuple = ()
    eps: tuple = ()


class FundamentalsProvider(ABC):
    """财报要点来源抽象。"""

    @abstractmethod
    def get_fundamentals(self, symbol: str) -> FundamentalsSummary:
        """最近几期营收/净利/EPS 摘要(新→旧)。失败或无数据返回空 summary,不抛。"""


class EdgarFundamentalsProvider(FundamentalsProvider):
    """SEC EDGAR company facts。SEC 要求 User-Agent 标识请求方(含联系方式)。"""

    def __init__(self, user_agent: str, timeout: float = 20.0, periods: int = 4):
        if not (user_agent or "").strip():
            raise ValueError(
                "edgar_user_agent 必须设置(SEC 要求,如 'stock-agent your@email')")
        self._headers = {"User-Agent": user_agent}
        self._timeout = timeout
        self._periods = periods

    def get_fundamentals(self, symbol: str) -> FundamentalsSummary:
        sym = symbol.strip().upper()
        try:
            cik = self._lookup_cik(sym)
            if cik is None:
                logger.warning("EDGAR 找不到 %s 的 CIK,返回空摘要", sym)
                return FundamentalsSummary(sym)
            facts = self._get_json(FACTS_URL.format(cik=cik))
        except httpx.HTTPError as exc:
            logger.warning("EDGAR 抓取失败(%s),返回空摘要", exc)
            return FundamentalsSummary(sym)
        gaap = facts.get("facts", {}).get("us-gaap", {})
        return FundamentalsSummary(
            sym,
            revenue=self._extract(gaap, REVENUE_TAGS, "USD"),
            net_income=self._extract(gaap, NET_INCOME_TAGS, "USD"),
            eps=self._extract(gaap, EPS_TAGS, "USD/shares"),
        )

    def _get_json(self, url: str):
        resp = httpx.get(url, headers=self._headers, timeout=self._timeout)
        resp.raise_for_status()
        return resp.json()

    def _lookup_cik(self, symbol: str):
        for entry in self._get_json(TICKERS_URL).values():
            if str(entry.get("ticker", "")).upper() == symbol:
                return int(entry["cik_str"])
        return None

    def _extract(self, gaap: dict, tags: tuple, unit: str) -> tuple:
        for tag in tags:
            entries = gaap.get(tag, {}).get("units", {}).get(unit, [])
            points = {}
            for e in entries:
                if e.get("form") not in VALID_FORMS or "end" not in e or "val" not in e:
                    continue
                end = dt.date.fromisoformat(e["end"])
                fiscal = sanitize_text(f"{e.get('fp') or '?'}-{e.get('fy') or '?'}", 20)
                points[end] = FundamentalPoint(end, float(e["val"]), fiscal)  # 后出现覆盖(修正报)
            if points:
                ordered = sorted(points.values(), key=lambda p: p.end, reverse=True)
                return tuple(ordered[: self._periods])
        return ()
