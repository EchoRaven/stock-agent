# Futu OpenD 券商适配器 —— 配置与验证指南

日期:2026-07-19
状态:**模拟盘(SIMULATE)默认,REAL 实盘硬门 OFF**;**未经真实 OpenD 校验**

## 这是什么

`app/execution/futu_broker.py` 的 `FutuBroker`,实现和 `PaperBroker` 一样的
`Broker` 接口(`submit`/`process_fills`),但把订单真正发给 Futu(富途/moomoo)
的 **OpenD 网关**,而不是本地纸面撮合。

- **默认模拟盘**:`futu_trd_env` 默认 `SIMULATE`,不用做任何额外配置就是模拟交易。
- **REAL(实盘)是硬门**:必须同时满足 `STOCKAGENT_FUTU_ALLOW_REAL=true` **且**
  设置了非空的解锁密码,否则任何 REAL 下单请求在触碰网关之前就会被拒绝
  (`RuntimeError`)。默认状态下 REAL 100% 拒绝。
- FutuBroker **只是一个执行后端**——不绕过、不重新实现 RiskGate / 交易模式 /
  人工确认;这些控制层保持在它之上。它没有、也不会有任何转账/出金/提现方法
  (与 `tests/execution/test_no_fund_egress.py` 的红线一致)。
- **本地风控账本必须和券商保持同步**:仓位上限(position-cap)和日内熔断
  (daily-loss circuit-breaker)的判定全部读本地 `paper_repo`(cash/positions),
  不直接问券商。`process_fills` 现在每对账成功一笔真实成交,就把它(按券商
  `order_id` 精确匹配到具体订单,而不是按 symbol 猜)原样镜像进本地账本
  ——这样 RiskGate 才不会对真实持仓视而不见。但这仍然是"跟着本地记录走",
  不是每次都问券商要权威数据;**如果本地账本因为任何原因(漏对账、程序重启
  丢单、账本被人手动改过)和券商侧产生漂移,RiskGate 判定的就是错的敞口**。

## 前置条件

1. 安装 SDK(可选依赖,核心依赖里没有,不装也不影响其它任何功能):
   ```bash
   uv pip install futu-api
   # 或者用本项目的 optional extra:
   uv pip install -e ".[live]"
   ```
2. 下载并运行 **Futu OpenD** 网关程序(需要 moomoo / 富途牛牛账号登录)。
3. 在 OpenD / App 里确认你有一个**模拟交易(模拟盘)账户**并已启用——先用模拟盘,
   不要一上来就碰实盘。

## 配置(`backend/.env`,不提交到 git)

```bash
STOCKAGENT_FUTU_HOST=127.0.0.1
STOCKAGENT_FUTU_PORT=11111        # OpenD 默认端口
STOCKAGENT_FUTU_TRD_ENV=SIMULATE  # 模拟盘,保持默认即可
STOCKAGENT_FUTU_MARKET=US         # 美股
```

只做模拟盘验证的话,以上几行(甚至完全不配置,靠默认值)就够了。

## 如何开启 REAL(大写警告)

**先在模拟盘跑够久、确认行为符合预期之后再考虑这一步。**

```bash
STOCKAGENT_FUTU_ALLOW_REAL=true
STOCKAGENT_FUTU_UNLOCK_PWD=<你的富途交易解锁密码>
```

- 两者缺一不可:只设 `ALLOW_REAL=true` 不设密码,或只设密码不设
  `ALLOW_REAL=true`,`submit`/`process_fills` 都会在下单前直接抛
  `RuntimeError`,不会有任何订单发出去。
- 密码只从 `backend/.env`(已在 `.gitignore` 里)或环境变量读取,代码里绝不
  硬编码、绝不打印、绝不写进日志或异常信息(见
  `tests/execution/test_futu_broker.py` 里对 caplog / 异常文本的断言)。
- 即使开启了 REAL,`FutuBroker` 本身仍然只是执行后端:是否真的会对某个
  标的下单,取决于它之上的 RiskGate、交易模式(是否 `full_auto`,是否需要
  人工二次确认)——这些控制不会因为 REAL 开关而被绕过。

## 未经真实 OpenD 校验(必须显著声明)

这个适配器目前**只跑过针对 mock SDK 的离线单测**(`tests/execution/test_futu_broker.py`
用 `monkeypatch.setitem(sys.modules, "futu", ...)` 注入一个假的 `futu` 模块,
没有连过真实网关)。在本环境里没有可达的 OpenD 网关/账户,无法做端到端校验。

在信任它之前,你需要:
1. 自己启动 OpenD,连到**你自己的模拟盘账户**,用小额/小股数订单跑一遍
   `submit` → `process_fills`,确认在富途 App/OpenD 里能看到对应的模拟单
   和成交,且 `PaperFillRow`/订单状态**以及本地 `paper_repo` 的
   cash/position(RiskGate 读的那本账)**与富途侧一致。
2. 观察一段时间(比如至少几个交易日的模拟盘运行),确认没有异常(网络断连、
   RET 非 OK 时的取消逻辑、成交对账、本地账本与券商是否持续吻合等)。
3. 只有在这之后,才考虑按上面的步骤开启 REAL——而且开启后也建议先用最小仓位
   验证,不要直接托管给 `full_auto`。
4. **对接真实资金前建议的进一步加固(本次改动未做)**:目前本地账本只在
   `process_fills` 对账成功时被动更新,没有任何机制发现"账本和券商实际
   持仓/现金不一致"这件事本身。上线真实资金前,建议加一个周期性任务,用
   Futu 的权威查询接口(`accinfo_query` 查资金、`position_list_query` 查持仓)
   主动拉取券商侧真相,和本地账本做一致性核对/告警(而不是无条件覆盖本地
   记录,以免掩盖对账逻辑本身的 bug)。
5. **不要在同一个 REAL 账户里手动下单**(App/网页端直接操作),除非你已经
   接了上面第 4 点的定期核对——手动下单不会经过 `FutuBroker.process_fills`
   的对账镜像,会让本地账本和券商侧静默漂移,而 RiskGate 的仓位上限/熔断
   判定全部基于本地账本,漂移越大,风控越形同虚设。

## 接入 order_manager(后续步骤,本次未自动做)

现有 `order_manager` 默认用 `PaperBroker`。把 `FutuBroker` 换进去(替换
`PaperBroker` 实例)是一个独立的、有意**没有**在这次改动里自动完成的配置步骤——
避免"装了适配器 = 自动开始真实下单"这种意外。切换时同样遵守：先模拟盘验证，
REAL 需要显式满足上面两个条件。
