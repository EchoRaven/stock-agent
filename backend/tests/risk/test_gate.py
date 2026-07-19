import dataclasses
import datetime as dt
import logging

import pytest

from app.risk.gate import DEFAULT_RULES, RiskGate, params_from_row
from app.risk.rules import AccountState, OrderRequest, RiskParams
from app.store.models import SettingsRow

D = dt.date(2026, 7, 17)
PARAMS = RiskParams(single_position_cap_pct=0.20, total_position_cap_pct=0.80,
                    max_new_positions_per_day=3, daily_loss_halt_pct=0.05, cooldown_days=5)


def _account(**overrides):
    fields = dict(cash=100_000.0, position_values={}, new_buy_symbols_today=frozenset(),
                  last_sell_dates={}, breaker_tripped=False, stale_priced_symbols=frozenset())
    fields.update(overrides)
    return AccountState(**fields)


def _order(side="buy", shares=10, price=100.0, symbol="AAPL"):
    return OrderRequest(symbol=symbol, side=side, shares=shares, price=price, as_of=D)


def test_default_rules_cover_all_six():
    assert [rule.name for rule in DEFAULT_RULES] == [
        "circuit_breaker", "stale_quote", "single_position_cap", "total_position_cap",
        "max_new_positions", "cooldown"]


def test_allows_normal_buy():
    assert RiskGate().check(_order(), _account(), PARAMS).allowed


def test_rejects_over_cap_and_logs(caplog):
    # 红线:拒绝必须留痕
    with caplog.at_level(logging.WARNING):
        out = RiskGate().check(_order(shares=300), _account(), PARAMS)
    assert not out.allowed and "single-position cap" in out.reason
    assert "risk gate rejected" in caplog.text


def test_default_deny_invalid_side():
    out = RiskGate().check(_order(side="short"), _account(), PARAMS)
    assert not out.allowed and "denied by default" in out.reason


def test_default_deny_nonpositive_shares():
    assert not RiskGate().check(_order(shares=0), _account(), PARAMS).allowed


def test_default_deny_buy_without_price():
    # 缺参考价的买单 fail-safe 拒绝(而不是按 0 元估值放行)
    out = RiskGate().check(_order(price=0.0), _account(), PARAMS)
    assert not out.allowed and "price" in out.reason


def test_first_rejection_wins_breaker_first():
    tripped = _account(breaker_tripped=True)
    out = RiskGate().check(_order(shares=300), tripped, PARAMS)
    assert "circuit breaker" in out.reason


def test_params_from_row_maps_all_fields():
    row = SettingsRow(id=1, single_position_cap_pct=0.1, total_position_cap_pct=0.5,
                      max_new_positions_per_day=1, daily_loss_halt_pct=0.02, cooldown_days=9)
    assert params_from_row(row) == RiskParams(0.1, 0.5, 1, 0.02, 9)


def test_stale_quote_rejects_over_cap_buy_before_cap_rule():
    # 行为验证(而不只是 DEFAULT_RULES 元组顺序的结构断言):同一笔买单
    # 既触发 stale-quote(rule 2)又触发单票上限(rule 3),拒绝原因必须是
    # stale-quote——证明规则顺序在实际执行路径上生效、先拒即止。
    stale_account = _account(stale_priced_symbols=frozenset({"MSFT"}))
    over_cap_order = _order(shares=300)  # 300*100=30000 > cap 100_000*0.20=20000
    out = RiskGate().check(over_cap_order, stale_account, PARAMS)
    assert not out.allowed
    assert "持仓报价缺失" in out.reason
    assert "cap" not in out.reason


def test_risk_dataclasses_are_frozen():
    order = _order()
    with pytest.raises(dataclasses.FrozenInstanceError):
        order.shares = 999
    account = _account()
    with pytest.raises(dataclasses.FrozenInstanceError):
        account.cash = 0.0
    with pytest.raises(dataclasses.FrozenInstanceError):
        PARAMS.cooldown_days = 1
