"""FastAPI 装配:纯组装,业务全在 services/ 与 app/api/routes_*.py 薄壳里。

本地开发运行(仅监听 127.0.0.1——单用户场景无认证层,绝不绑 0.0.0.0/公网暴露):
    uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
或直接:
    uv run python -m app.main
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes_backtest import router as backtest_router
from app.api.routes_dashboard import router as dashboard_router
from app.api.routes_execution import router as execution_router
from app.api.routes_orders import router as orders_router
from app.api.routes_sentiment import router as sentiment_router
from app.api.routes_settings import router as settings_router
from app.api.routes_signals import router as signals_router
from app.api.routes_trade import router as trade_router
from app.api.routes_watchdog import router as watchdog_router

app = FastAPI(title="stock-agent API")

# 只允许本地 Next.js dev origin;绝不用 allow_origins=["*"]。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(settings_router, prefix="/api")
app.include_router(orders_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(signals_router, prefix="/api")
app.include_router(backtest_router, prefix="/api")
app.include_router(execution_router, prefix="/api")
app.include_router(sentiment_router, prefix="/api")
app.include_router(watchdog_router, prefix="/api")
app.include_router(trade_router, prefix="/api")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


def run() -> None:
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
