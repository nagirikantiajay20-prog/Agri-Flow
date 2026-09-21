"""
Integration test for scripts/phase0_schema_reconciliation.py — proves
the diff logic actually detects mismatches (not just that the script
runs without crashing), by injecting a throwaway extra table/column/
function/policy, running the tool, then cleaning up.

This is a script-testing test, not an app test — it uses a sync
psycopg2 connection directly, matching what the script itself does.
"""
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app.core.config import settings

pytestmark = pytest.mark.asyncio

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "phase0_schema_reconciliation.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location("phase0_schema_reconciliation", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_url() -> str:
    return settings.DATABASE_URL_SYNC or settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")


async def test_reconciliation_report_matches_when_schemas_agree():
    module = _load_script_module()
    report = module.build_report(_sync_url())
    from app.core.database import Base

    assert f"Tables compared: {len(Base.metadata.tables)} matched" in report
    assert "Column mismatches on matched tables: 0" in report


async def test_reconciliation_detects_injected_mismatches():
    module = _load_script_module()
    engine = create_engine(_sync_url())

    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE seeds ADD COLUMN IF NOT EXISTS legacy_test_column text"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS legacy_test_table (id uuid PRIMARY KEY DEFAULT gen_random_uuid())"))
        conn.execute(text(
            "CREATE OR REPLACE FUNCTION test_legacy_rpc(x uuid) RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql"
        ))

    try:
        report = module.build_report(_sync_url())
        assert "legacy_test_table" in report
        assert "legacy_test_column" in report
        assert "test_legacy_rpc" in report
        assert "Column mismatches on matched tables: 1" in report
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS legacy_test_table"))
            conn.execute(text("ALTER TABLE seeds DROP COLUMN IF EXISTS legacy_test_column"))
            conn.execute(text("DROP FUNCTION IF EXISTS test_legacy_rpc(uuid)"))
    engine.dispose()


async def test_known_undocumented_rpc_is_flagged_distinctly():
    module = _load_script_module()
    engine = create_engine(_sync_url())

    with engine.begin() as conn:
        conn.execute(text(
            "CREATE OR REPLACE FUNCTION get_public_stats() RETURNS void AS $$ BEGIN END; $$ LANGUAGE plpgsql"
        ))
    try:
        report = module.build_report(_sync_url())
        assert "get_public_stats" in report
        assert "flagged in Master Plan §1.3" in report
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP FUNCTION IF EXISTS get_public_stats()"))
    engine.dispose()
