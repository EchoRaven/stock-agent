import logging

import pytest
from sqlalchemy import select

from app.store.db import init_db, make_engine, make_session_factory
from app.store.models import SettingsRow
from app.store.repos.settings_repo import (MODE_ADVISORY, MODE_FULL_AUTO,
                                           MODE_SEMI_AUTO, MODES, get_app_settings,
                                           get_mode, set_mode, update_risk_params)


@pytest.fixture
def session():
    engine = make_engine(":memory:")
    init_db(engine)
    with make_session_factory(engine)() as s:
        yield s


def test_modes_constant():
    assert MODES == ("advisory", "semi_auto", "full_auto")


def test_get_app_settings_creates_singleton(session):
    row1 = get_app_settings(session)
    row2 = get_app_settings(session)
    assert row1.id == 1 and row2.id == 1
    assert len(session.scalars(select(SettingsRow)).all()) == 1


def test_fresh_db_mode_is_advisory(session):
    # 红线:未设置 → advisory(fail-safe)
    assert get_mode(session) == MODE_ADVISORY


def test_unknown_db_mode_fails_safe(session, caplog):
    # 红线:DB 里出现未知值(手改/脏数据)也必须回落 advisory
    get_app_settings(session).mode = "turbo"
    session.flush()
    with caplog.at_level(logging.WARNING):
        assert get_mode(session) == MODE_ADVISORY
    assert "fail-safe" in caplog.text


def test_empty_and_none_mode_fail_safe(session, caplog):
    # 红线:mode 被直接置空("")/置 None(ORM 脏写,不经 set_mode())也必须
    # fail-safe 为 advisory——钉住 get_mode() 里的 `(mode or "").strip()` 空值
    # 合并;如果未来"简化"成裸 `mode.strip()`,None 输入会直接抛 AttributeError,
    # 这条测试会变红。
    row = get_app_settings(session)
    row.mode = ""
    session.flush()
    with caplog.at_level(logging.WARNING):
        assert get_mode(session) == MODE_ADVISORY
    # 注意:mode 列在 schema 里是 NOT NULL,故意不 flush(会撞 DB 层约束,
    # 那是另一层、无关的防御,不是这里要钉的 get_mode() 空值合并逻辑)——
    # 只验证 get_mode() 本身对内存中 None 值的处理,不做 DB round-trip。
    row.mode = None
    with caplog.at_level(logging.WARNING):
        assert get_mode(session) == MODE_ADVISORY


def test_set_mode_semi_and_rejects_unknown(session):
    set_mode(session, MODE_SEMI_AUTO)
    assert get_mode(session) == MODE_SEMI_AUTO
    with pytest.raises(ValueError):
        set_mode(session, "yolo")


def test_full_auto_requires_explicit_confirm(session):
    # 红线:全自动开启需显式
    with pytest.raises(ValueError):
        set_mode(session, MODE_FULL_AUTO)
    assert get_mode(session) == MODE_ADVISORY
    set_mode(session, MODE_FULL_AUTO, confirm_full_auto=True)
    assert get_mode(session) == MODE_FULL_AUTO


def test_update_risk_params_whitelist(session):
    update_risk_params(session, cooldown_days=9, initial_cash=50_000.0)
    row = get_app_settings(session)
    assert row.cooldown_days == 9 and row.initial_cash == 50_000.0
    with pytest.raises(ValueError):
        update_risk_params(session, mode="full_auto")  # mode 不许走参数通道
    with pytest.raises(ValueError):
        update_risk_params(session, evil_field=1)
