"""
Phase 0 schema reconciliation tool (Master Plan §7 / Module 0 / gap-fix
#5 in the backend remediation plan).

This is the tool the Master Plan's Phase 0 calls for — run this against
your REAL Supabase Postgres connection string (not the disposable dev/
test DB this repo's default .env points at) to find every place the
live schema disagrees with what this backend's SQLAlchemy models
assume, and every database function ("RPC") that exists live but has no
equivalent in this codebase's migrations.

What it does NOT do: change anything. This is read-only reflection +
comparison. It produces a markdown report; a human decides what to do
about each finding.

Usage:
    # Point at your REAL Supabase project's connection string —
    # ideally a read-only role, since this only needs SELECT access to
    # information_schema/pg_catalog.
    python scripts/phase0_schema_reconciliation.py \\
        --db-url "postgresql://readonly_user:pass@db.<project>.supabase.co:5432/postgres" \\
        --output docs/database/SCHEMA_RECONCILIATION.md

Requires: sqlalchemy, psycopg2-binary (sync driver — this uses
SQLAlchemy's `inspect()` reflection API, which is sync-only).
"""
import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

import app.models  # noqa: E402,F401 — registers every model's metadata
from app.core.database import Base  # noqa: E402

# Tables/RPCs the Master Plan already identified as undocumented in the
# original Backend_Technical_Specification.md DDL (§1/§1.6) — surfaced
# distinctly in the report so a re-run can confirm whether they're still
# present, changed shape, or (for the RPCs) were finally ported.
KNOWN_UNDOCUMENTED_TABLES = {
    "profiles", "warehouse_slots", "seed_warehouses", "crop_inspections", "otps", "distributor_orders",
}
KNOWN_UNDOCUMENTED_RPCS = {
    "approve_farmer", "review_bank_request", "review_grain_sale", "pay_grain_sale",
    "get_farmer_dashboard", "get_admin_dashboard", "get_public_stats",
}


def _our_tables() -> dict[str, set[str]]:
    """table_name -> set of column names, as this backend's SQLAlchemy
    models declare them."""
    return {table.name: {c.name for c in table.columns} for table in Base.metadata.sorted_tables}


def _live_tables(inspector) -> dict[str, set[str]]:
    result = {}
    for table_name in inspector.get_table_names(schema="public"):
        columns = {col["name"] for col in inspector.get_columns(table_name, schema="public")}
        result[table_name] = columns
    return result


def _live_functions(engine) -> list[dict]:
    """User-defined functions in the public schema — the RPCs. Filters
    out Postgres/Supabase-internal helper functions (extension-owned,
    trigger-only plumbing) as best-effort; always eyeball the output."""
    query = text(
        """
        SELECT p.proname AS name,
               pg_get_function_identity_arguments(p.oid) AS arguments,
               pg_get_function_result(p.oid) AS return_type,
               l.lanname AS language
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        JOIN pg_language l ON l.oid = p.prolang
        WHERE n.nspname = 'public'
        ORDER BY p.proname
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(query).mappings().all()
    return [dict(row) for row in rows]


def _live_rls_policies(engine) -> list[dict]:
    """RLS policies on live tables — these encode authorization rules
    (Master Plan §7 point 3) that must be replicated in this backend's
    RBAC layer (app.core.dependencies) since the backend connects with a
    role that bypasses RLS."""
    query = text(
        """
        SELECT schemaname, tablename, policyname, permissive, roles, cmd, qual, with_check
        FROM pg_policies
        WHERE schemaname = 'public'
        ORDER BY tablename, policyname
        """
    )
    try:
        with engine.connect() as conn:
            rows = conn.execute(query).mappings().all()
        return [dict(row) for row in rows]
    except Exception as exc:  # noqa: BLE001
        return [{"error": f"Could not read pg_policies (check role permissions): {exc}"}]


def build_report(db_url: str) -> str:
    engine = create_engine(db_url)
    inspector = inspect(engine)

    ours = _our_tables()
    live = _live_tables(inspector)

    tables_only_live = sorted(set(live) - set(ours))
    tables_only_ours = sorted(set(ours) - set(live))
    tables_in_both = sorted(set(ours) & set(live))

    column_diffs = {}
    for table in tables_in_both:
        only_live = sorted(live[table] - ours[table])
        only_ours = sorted(ours[table] - live[table])
        if only_live or only_ours:
            column_diffs[table] = {"only_in_live_db": only_live, "only_in_our_models": only_ours}

    functions = _live_functions(engine)
    our_function_names = set()  # this backend has no server-side functions of its own by design (Master Plan §45)
    live_function_names = {f["name"] for f in functions}
    rpcs_we_have_not_ported = sorted(live_function_names - our_function_names)

    policies = _live_rls_policies(engine)

    lines = []
    lines.append("# Schema Reconciliation Report")
    lines.append("")
    lines.append(f"Generated {datetime.now(timezone.utc).isoformat()} by `scripts/phase0_schema_reconciliation.py`.")
    lines.append("")
    lines.append(
        "This is a mechanical diff between the live database and this backend's SQLAlchemy models — "
        "it does not judge correctness, it lists disagreements for a human to resolve (Master Plan §7)."
    )
    lines.append("")

    lines.append("## Tables present in the live DB but NOT in this backend's models")
    lines.append("")
    if tables_only_live:
        for t in tables_only_live:
            flag = " ⚠️ (flagged in Master Plan §1/§1.6 as previously undocumented)" if t in KNOWN_UNDOCUMENTED_TABLES else ""
            lines.append(f"- `{t}`{flag}")
    else:
        lines.append("_None — every live table has a corresponding model._")
    lines.append("")

    lines.append("## Tables in this backend's models but NOT found in the live DB")
    lines.append("")
    if tables_only_ours:
        for t in tables_only_ours:
            lines.append(f"- `{t}` — either not yet migrated live, or this backend introduced it fresh")
    else:
        lines.append("_None._")
    lines.append("")

    lines.append("## Column-level differences on tables present in both")
    lines.append("")
    if column_diffs:
        for table, diff in column_diffs.items():
            lines.append(f"### `{table}`")
            if diff["only_in_live_db"]:
                lines.append(f"- Columns only in the live DB: {', '.join(f'`{c}`' for c in diff['only_in_live_db'])}")
            if diff["only_in_our_models"]:
                lines.append(f"- Columns only in our models: {', '.join(f'`{c}`' for c in diff['only_in_our_models'])}")
            lines.append("")
    else:
        lines.append("_None — matching tables have identical column sets._")
        lines.append("")

    lines.append("## Live database functions (RPCs)")
    lines.append("")
    lines.append(
        "This backend deliberately does not call server-side functions for new business logic "
        "(Master Plan §45/§46 — resource-oriented REST + service layer instead of `{action: ...}` "
        "dispatch or an equivalent RPC-per-operation pattern), so every live function below is either "
        "dead code to confirm-and-drop, or logic this backend's service layer needs to have ported "
        "faithfully. Cross-check each against the matching service module before assuming either."
    )
    lines.append("")
    if functions:
        for f in functions:
            flag = " ⚠️ (flagged in Master Plan §1.3 as not present in any shipped migration — verify it was ported)" if f["name"] in KNOWN_UNDOCUMENTED_RPCS else ""
            lines.append(f"- `{f['name']}({f['arguments']})` -> `{f['return_type']}` [{f['language']}]{flag}")
    else:
        lines.append("_None found (or insufficient privileges to list `pg_proc`)._")
    lines.append("")
    if rpcs_we_have_not_ported:
        lines.append(f"**{len(rpcs_we_have_not_ported)} live function(s) have no corresponding logic call site in this backend** — "
                      "review each against app/services/*.py before decommissioning the legacy Edge Functions.")
        lines.append("")

    lines.append("## Row-Level Security policies on live tables")
    lines.append("")
    lines.append(
        "This backend connects with a role that bypasses RLS (Master Plan §7 point 3) — every policy "
        "below encodes an authorization rule that must have an equivalent check in "
        "`app/core/dependencies.py` (ROLE_PERMISSIONS) or the relevant service function, or it's a rule "
        "this backend is currently NOT enforcing."
    )
    lines.append("")
    if policies:
        for p in policies:
            if "error" in p:
                lines.append(f"_{p['error']}_")
                continue
            lines.append(
                f"- `{p['tablename']}.{p['policyname']}` — cmd={p['cmd']}, roles={p['roles']}, "
                f"using=`{p['qual']}`, with_check=`{p['with_check']}`"
            )
    else:
        lines.append("_None found (or insufficient privileges to list `pg_policies`)._")
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Tables compared: {len(tables_in_both)} matched, {len(tables_only_live)} live-only, {len(tables_only_ours)} model-only")
    lines.append(f"- Column mismatches on matched tables: {len(column_diffs)}")
    lines.append(f"- Live functions found: {len(functions)}")
    lines.append(f"- RLS policies found: {len([p for p in policies if 'error' not in p])}")
    lines.append("")
    lines.append(
        "**This report replaces guesswork with a checklist — it does not replace judgment.** "
        "Every flagged item above needs a human decision (port it, confirm it's dead, or "
        "intentionally diverge and document why), not an automated fix."
    )

    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-url", required=True, help="Sync SQLAlchemy URL, e.g. postgresql://user:pass@host:5432/postgres")
    parser.add_argument("--output", default="docs/database/SCHEMA_RECONCILIATION.md")
    args = parser.parse_args()

    report = build_report(args.db_url)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report)
    print(f"Wrote {output_path} ({len(report)} bytes)")
