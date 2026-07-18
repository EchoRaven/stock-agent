import datetime as dt

import pytest

from app.risk.rules import (ALLOW, AccountState, CircuitBreakerRule, CooldownRule,
                            MaxNewPositionsRule, OrderRequest, RiskCheck, RiskParams,
                            SinglePositionCapRule, StaleQuoteRule, TotalPositionCapRule)

D = dt.date(2026, 7, 17)
PARAMS = RiskParams(single_position_cap_pct=0.20, total_position_cap_pct=0.80,
                    max_new_positions_per_day=2, daily_loss_halt_pct=0.05, cooldown_days=5)


def _account(**overrides):
    fields = dict(cash=100_000.0, position_values={}, new_buy_symbols_today=frozenset(),
                  last_sell_dates={}, breaker_tripped=False, stale_priced_symbols=frozenset())
    fields.update(overrides)
    return AccountState(**fields)


def _buy(symbol="AAPL", shares=10, price=100.0):
    return OrderRequest(symbol=symbol, side="buy", shares=shares, price=price, as_of=D)


def _sell(symbol="AAPL", shares=10, price=100.0):
    return OrderRequest(symbol=symbol, side="sell", shares=shares, price=price, as_of=D)


def test_equity_sums_cash_and_positions():
    acct = _account(cash=1000.0, position_values={"AAPL": 500.0, "MSFT": 250.0})
    assert acct.equity() == pytest.approx(1750.0)


def test_circuit_breaker_blocks_buys_allows_sells():
    # 红线:熔断触发后当日只允许卖出
    rule = CircuitBreakerRule()
    tripped = _account(breaker_tripped=True)
    out = rule.check(_buy(), tripped, PARAMS)
    assert not out.allowed and "circuit breaker" in out.reason
    assert rule.check(_sell(), tripped, PARAMS).allowed
    assert rule.check(_buy(), _account(), PARAMS) is ALLOW


def test_single_position_cap():
    # equity=10 万,cap 20% = 2 万:1.9 万过、2.1 万拒
    rule = SinglePositionCapRule()
    assert rule.check(_buy(shares=190, price=100.0), _account(), PARAMS).allowed
    out = rule.check(_buy(shares=210, price=100.0), _account(), PARAMS)
    assert not out.allowed and "single-position cap" in out.reason


def test_single_position_cap_counts_existing_position():
    # equity=10 万,已持 1.5 万,再买 6 千 → 2.1 万 > 2 万
    acct = _account(cash=85_000.0, position_values={"AAPL": 15_000.0})
    assert not SinglePositionCapRule().check(_buy(shares=60, price=100.0), acct, PARAMS).allowed


def test_total_position_cap():
    # equity=10 万,总仓 cap 80% = 8 万:已持 7.5 万,再买 6 千拒、4 千过
    acct = _account(cash=25_000.0, position_values={"MSFT": 75_000.0})
    out = TotalPositionCapRule().check(_buy(shares=60, price=100.0), acct, PARAMS)
    assert not out.allowed and "total-position cap" in out.reason
    assert TotalPositionCapRule().check(_buy(shares=40, price=100.0), acct, PARAMS).allowed


def test_max_new_positions():
    rule = MaxNewPositionsRule()
    acct = _account(new_buy_symbols_today=frozenset({"MSFT", "NVDA"}))
    out = rule.check(_buy("GOOG"), acct, PARAMS)  # 第 3 个新开仓,超上限 2
    assert not out.allowed and "max new positions" in out.reason
    # 已持有标的加仓不算新开仓
    held = _account(position_values={"AAPL": 5_000.0},
                    new_buy_symbols_today=frozenset({"MSFT", "NVDA"}))
    assert rule.check(_buy("AAPL"), held, PARAMS).allowed
    # 当日已有该标的买单(重复计数保护)不再计新
    assert rule.check(_buy("MSFT"), acct, PARAMS).allowed


def test_cooldown_blocks_rebuy_within_window():
    rule = CooldownRule()
    acct = _account(last_sell_dates={"AAPL": D - dt.timedelta(days=3)})
    out = rule.check(_buy("AAPL"), acct, PARAMS)
    assert not out.allowed and "cooldown" in out.reason
    ok = _account(last_sell_dates={"AAPL": D - dt.timedelta(days=5)})
    assert rule.check(_buy("AAPL"), ok, PARAMS).allowed


def test_sells_bypass_buy_only_rules():
    acct = _account(cash=1_000.0, position_values={"AAPL": 99_000.0},
                    new_buy_symbols_today=frozenset({"A", "B", "C"}),
                    last_sell_dates={"AAPL": D})
    for rule in (SinglePositionCapRule(), TotalPositionCapRule(),
                 MaxNewPositionsRule(), CooldownRule()):
        assert rule.check(_sell("AAPL"), acct, PARAMS).allowed


def test_stale_quote_rule_blocks_buys_allows_sells():
    # 红线加固(finding #6):持仓报价缺失 → 权益不可信 → 保守降级为仅允许卖出。
    # 删掉 StaleQuoteRule 或其 buy 分支,此测试即 fail。
    rule = StaleQuoteRule()
    stale = _account(position_values={"AAPL": 5_000.0},
                     stale_priced_symbols=frozenset({"AAPL"}))
    out = rule.check(_buy("MSFT"), stale, PARAMS)   # 任何标的的买单都拒
    assert not out.allowed and "报价缺失" in out.reason
    assert rule.check(_sell("AAPL"), stale, PARAMS).allowed  # 卖出仍放行
    assert rule.check(_buy("MSFT"), _account(), PARAMS) is ALLOW  # 无 stale 不干预


def test_risk_check_is_frozen():
    with pytest.raises(Exception):
        RiskCheck(True, "").allowed = False
