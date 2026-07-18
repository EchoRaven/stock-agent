from pathlib import Path

OPENCLAW = Path(__file__).resolve().parents[2] / "openclaw"


def test_skill_md_has_committee_and_red_lines():
    text = (OPENCLAW / "skills" / "trading" / "SKILL.md").read_text()
    for marker in ("technical", "fundamental", "sentiment", "bear", "主席",
                   "bear_rebuttal", "不得执行", "run_screener", "get_stock_briefing",
                   "submit_decision"):
        assert marker in text, f"SKILL.md missing marker: {marker}"


def test_setup_md_mentions_mcp_server():
    text = (OPENCLAW / "setup.md").read_text()
    assert "app.mcp.server" in text
    assert "STOCKAGENT_FINNHUB_API_KEY" in text


def test_cron_md_has_premarket_and_postmarket_jobs():
    text = (OPENCLAW / "cron.md").read_text()
    assert "盘前" in text and "盘后" in text
    assert "app.cli report" in text
