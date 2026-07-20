"""Futu OpenD 交易适配器(moomoo/富途牛牛)。

- 默认模拟盘(TrdEnv.SIMULATE)。REAL 硬门:除非同时设置了
  STOCKAGENT_FUTU_ALLOW_REAL=true 且 STOCKAGENT_FUTU_UNLOCK_PWD 非空,
  一律拒绝 REAL 下单——拒绝发生在任何下单动作之前,从不静默降级。
- `futu`(futu-api)是可选依赖,不在核心 dependencies 里:本模块只在方法内部
  惰性 `import futu`,不装 futu 时本模块本身也能正常 import(见
  test_module_imports_without_futu_installed)。
- Broker 接口只公开 submit/process_fills(app/execution/base.py +
  tests/execution/test_no_fund_egress.py 的红线);连接/解锁/下单/查询/关闭/
  符号映射全部私有方法,本文件没有、也永远不会有任何转账/出金/提现路径。
- 解锁密码只从 Settings(env/.env)读取,绝不硬编码,也绝不出现在日志或异常信息里。
- process_fills 用券商(deal_list_query)上报的真实成交价/量对账,`open_prices`
  参数只是为了和 Broker 接口签名保持一致,这里不使用(纸面开盘价对真实成交无意义)。
  对账按 order_id 精确匹配成交(submit 时把券商 order id 存进 order.reason),
  不按 symbol 猜——同标的的另一笔人工/机器人订单不会被错误地对到本单上。
  每笔对账成功的成交都会原样镜像进本地 cash/position 账本(与 PaperBroker._execute
  同样的记账口径,但不做纸面截断)——position-cap / daily-loss 熔断读的就是这本账,
  账本不同步风控就是瞎子。
- 未经真实 OpenD 校验:本文件配套测试全部 mock SDK;上线前请按
  docs/futu_setup.md 用你自己的模拟盘账户实测。
"""
import datetime as dt
import logging
import re

from sqlalchemy.orm import Session

from app.config import get_settings
from app.execution.base import Broker
from app.store.models import OrderRow
from app.store.repos import order_repo, paper_repo
from app.store.repos.settings_repo import get_app_settings

logger = logging.getLogger(__name__)

_REASON_ORDER_ID_RE = re.compile(r"^futu:[A-Za-z_]+:(.+)$")


class FutuBroker(Broker):
    """Futu OpenD 适配器:submit 下单、process_fills 对账真实成交。"""

    def __init__(self, settings=None):
        settings = settings or get_settings()
        self._host = settings.futu_host
        self._port = settings.futu_port
        self._market = settings.futu_market
        self._unlock_pwd = settings.futu_unlock_pwd
        self._allow_real = settings.futu_allow_real
        self._trd_env_name = (settings.futu_trd_env or "SIMULATE").upper()

    def submit(self, session: Session, order: OrderRow) -> OrderRow:
        if order.side not in ("buy", "sell"):
            raise ValueError(f"invalid side: {order.side}")
        if order.shares <= 0:
            raise ValueError("shares must be positive")
        self._guard_env()
        return self._place_order(session, order)

    def process_fills(self, session: Session, fill_date: dt.date, open_prices: dict) -> list:
        """对账 STATUS_SUBMITTED 订单;open_prices 未使用(见模块 docstring)。"""
        self._guard_env()
        futu_mod = self._import_sdk()
        ctx = self._connect(futu_mod)
        try:
            deals = self._query_deals(futu_mod, ctx)
        finally:
            self._close(ctx)
        return self._reconcile(session, fill_date, deals)

    # ------------------------------------------------------------------
    # 私有:安全门
    # ------------------------------------------------------------------

    def _guard_env(self) -> None:
        if self._trd_env_name not in ("SIMULATE", "REAL"):
            raise ValueError(f"invalid futu_trd_env: {self._trd_env_name}")
        if self._trd_env_name == "REAL" and not (self._allow_real and self._unlock_pwd):
            raise RuntimeError(
                "REAL 交易未启用:需 STOCKAGENT_FUTU_ALLOW_REAL=true 且设置解锁密码;"
                "默认仅模拟盘")

    # ------------------------------------------------------------------
    # 私有:SDK 惰性导入 / 连接 / 解锁 / 关闭
    # ------------------------------------------------------------------

    def _import_sdk(self):
        import futu  # 惰性导入:可选依赖,不装时其它路径完全不受影响
        return futu

    def _trd_env_enum(self, futu_mod):
        return futu_mod.TrdEnv.REAL if self._trd_env_name == "REAL" else futu_mod.TrdEnv.SIMULATE

    def _connect(self, futu_mod):
        ctx = futu_mod.OpenSecTradeContext(
            filter_trdmarket=futu_mod.TrdMarket.US, host=self._host, port=self._port)
        if self._trd_env_name == "REAL":
            try:
                self._unlock(futu_mod, ctx)
            except Exception:
                # 解锁失败也不能漏关 ctx(socket 泄漏);异常本身已经不含密码,原样重抛。
                self._close(ctx)
                raise
        return ctx

    def _unlock(self, futu_mod, ctx) -> None:
        ret, _data = ctx.unlock_trade(password=self._unlock_pwd)
        if ret != futu_mod.RET_OK:
            # 绝不把密码或原始 data(可能回显密码相关信息)带进异常/日志
            raise RuntimeError("futu unlock_trade failed: 解锁交易密码校验未通过")

    def _close(self, ctx) -> None:
        try:
            ctx.close()
        except Exception:
            logger.warning("futu OpenSecTradeContext.close() 失败,忽略", exc_info=True)

    def _code(self, symbol: str) -> str:
        return f"{self._market}.{symbol}"

    # ------------------------------------------------------------------
    # 私有:下单
    # ------------------------------------------------------------------

    def _place_order(self, session: Session, order: OrderRow) -> OrderRow:
        futu_mod = self._import_sdk()
        ctx = self._connect(futu_mod)  # 连接/解锁失败在此抛出,不吞
        try:
            trd_side = futu_mod.TrdSide.BUY if order.side == "buy" else futu_mod.TrdSide.SELL
            ret, data = ctx.place_order(
                price=0, qty=order.shares, code=self._code(order.symbol),
                trd_side=trd_side, order_type=futu_mod.OrderType.MARKET,
                trd_env=self._trd_env_enum(futu_mod))
        finally:
            self._close(ctx)

        if ret != futu_mod.RET_OK:
            logger.warning("futu place_order 被拒绝(order_id=%s)", order.id)
            return order_repo.update_status(
                session, order.id, order_repo.STATUS_CANCELLED,
                reason=f"futu place_order rejected: {data}")

        broker_order_id = self._extract_order_id(data)
        return order_repo.update_status(
            session, order.id, order_repo.STATUS_SUBMITTED,
            reason=f"futu:{self._trd_env_name}:{broker_order_id}")

    def _extract_order_id(self, data) -> str:
        try:
            return str(data["order_id"].iloc[0])
        except Exception:
            return "unknown"

    # ------------------------------------------------------------------
    # 私有:对账成交
    # ------------------------------------------------------------------

    def _query_deals(self, futu_mod, ctx):
        ret, data = ctx.deal_list_query(trd_env=self._trd_env_enum(futu_mod))
        if ret != futu_mod.RET_OK:
            logger.warning("futu deal_list_query 失败")
            return []
        return data

    def _index_deals_by_order_id(self, deals) -> dict:
        """券商 order_id -> 该单下的所有成交记录(同一单可能有多笔部分成交)。"""
        try:
            records = deals.to_dict("records")
        except AttributeError:
            records = list(deals or [])
        out: dict = {}
        for rec in records:
            order_id = rec.get("order_id")
            if order_id is None:
                continue
            out.setdefault(str(order_id), []).append(rec)
        return out

    def _parse_broker_order_id(self, order: OrderRow) -> str | None:
        """从 submit 时写入的 reason(`futu:<ENV>:<broker_order_id>`)解析出券商 order id;
        格式对不上一律返回 None——绝不用别的信息(比如 symbol)瞎猜。"""
        match = _REASON_ORDER_ID_RE.match(order.reason or "")
        if not match:
            return None
        broker_order_id = match.group(1)
        return broker_order_id or None

    def _apply_fill_to_ledger(self, session: Session, order: OrderRow, qty: int,
                              price: float) -> None:
        """把券商上报的真实成交镜像进本地 cash/position 账本——RiskGate(仓位上限/
        日内熔断)读的就是 paper_repo 这本账。字段/更新方式与 PaperBroker._execute
        完全一致,但不做纸面截断:成交已经在券商侧真实发生,原样应用 (side, qty, price)。
        """
        account = paper_repo.get_account(session, get_app_settings(session).initial_cash)
        held = paper_repo.get_position(session, order.symbol)
        held_shares = held.shares if held is not None else 0
        if order.side == "buy":
            account.cash -= qty * price
            prev_cost = held.avg_cost * held_shares if held is not None else 0.0
            total = held_shares + qty
            paper_repo.set_position(session, order.symbol, total,
                                    (prev_cost + qty * price) / total)
        else:
            account.cash += qty * price
            paper_repo.set_position(session, order.symbol, held_shares - qty,
                                    held.avg_cost if held is not None else 0.0)

    def _reconcile_one(self, session: Session, fill_date: dt.date, order: OrderRow,
                       by_order_id: dict):
        broker_order_id = self._parse_broker_order_id(order)
        if broker_order_id is None:
            return None  # 没存下券商 order id:保持 submitted,不猜测
        order_deals = by_order_id.get(broker_order_id)
        if not order_deals:
            return None  # 券商未报告这张单的成交:保持 submitted,不猜测
        parsed = [(int(d["qty"]), float(d["price"])) for d in order_deals]
        qty = sum(q for q, _ in parsed)
        if qty <= 0:
            return None
        price = sum(q * p for q, p in parsed) / qty  # 多笔部分成交按量加权均价
        self._apply_fill_to_ledger(session, order, qty, price)
        order_repo.update_status(
            session, order.id, order_repo.STATUS_FILLED,
            reason=f"futu filled {qty}@{price:.4f} on {fill_date}")
        return paper_repo.add_fill(
            session, order.id, fill_date, order.symbol, order.side, qty, price)

    def _reconcile(self, session: Session, fill_date: dt.date, deals) -> list:
        by_order_id = self._index_deals_by_order_id(deals)
        fills = []
        for order in order_repo.get_orders_by_status(session, order_repo.STATUS_SUBMITTED):
            try:
                fill = self._reconcile_one(session, fill_date, order, by_order_id)
            except Exception:
                # 一笔畸形成交(缺字段/NaN)不能拖垮整批对账;不泄露成交明细,只记 order.id。
                logger.warning("futu 对账失败,order_id=%s 保持 submitted,跳过", order.id,
                               exc_info=True)
                continue
            if fill is not None:
                fills.append(fill)
        return fills
