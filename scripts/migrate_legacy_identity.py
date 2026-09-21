"""
Legacy identity migration (Master Plan §6, §9 Phase 3-4 / gap-fix #4 in
the backend remediation plan).

Migrates real farmer/manager accounts out of the legacy dual-identity
model:

    auth.users (Supabase Auth, UUID, encrypted_password = bcrypt)
        -> profiles (id = auth.users.id, app_user_id -> legacy users.id)
              -> legacy users (BIGSERIAL id, name/phone/email/role/status)
                    -> farmer_profiles / admin_profiles (keyed on legacy users.id)

into this backend's canonical model:

    users (UUID id, password_hash, role, status, legacy_app_user_id bridge)
        -> farmer_profile / staff_profile (1:1, keyed on the new UUID)

Key design decisions (read before running against real data):

1. **The new user's UUID = the legacy profiles.id (= auth.users.id).**
   This is deliberate, not incidental: if any part of the legacy system
   ever needs to be cross-referenced against real Supabase Auth records
   during a phased cutover, the identity stays the same UUID throughout.
   It is NOT required by this backend (which no longer uses Supabase
   Auth), but preserving it costs nothing and removes a class of
   confusion during the transition.

2. **Passwords are carried over as-is (bcrypt), not reset.** The
   password lives in `auth.users.encrypted_password` in the real system
   (bcrypt, via Supabase's GoTrue), not in the legacy `users` table.
   This script reads it from wherever your Phase 0 reconciliation
   confirms it actually lives (see --auth-password-table /
   --auth-password-column) and stores it verbatim as this backend's
   password_hash. app.core.security.verify_password /
   app.services.auth_service.authenticate already handle bcrypt
   verification and lazily rehash to Argon2id on the account's next
   successful login — see tests/unit/test_password_migration_compat.py.
   If a legacy user cannot be found in the password source at all, the
   script generates an unusable random Argon2 hash and flags the
   account in the report as "needs password reset" rather than
   silently leaving it without a working password.

3. **Idempotent.** Every migrated row's `legacy_app_user_id` is set to
   the legacy integer `users.id`. Re-running skips anything with a
   matching `legacy_app_user_id` already present, so this is safe to
   run repeatedly (e.g. once for a dry run, again for real, again later
   to pick up accounts created in the legacy system after the first
   pass but before full cutover).

4. **This script's legacy-side SQL assumes the table/column shapes
   reconstructed in Master Plan §1/§6/§7 from Backend_Technical_Specification.md.**
   If your Phase 0 reconciliation report (scripts/phase0_schema_reconciliation.py)
   shows different column names, EDIT THE QUERIES BELOW — this is
   explicitly not a zero-config tool, because the legacy schema's exact
   shape was never fully verified against your live database (that's
   the whole reason Phase 0 exists).

Usage:
    # Dry run first. Always.
    python scripts/migrate_legacy_identity.py \\
        --legacy-db-url "postgresql://user:pass@legacy-host:5432/postgres" \\
        --auth-password-table auth_users_sim --auth-password-column encrypted_password \\
        --dry-run

    # Then for real:
    python scripts/migrate_legacy_identity.py \\
        --legacy-db-url "postgresql://user:pass@legacy-host:5432/postgres" \\
        --auth-password-table auth_users_sim --auth-password-column encrypted_password
"""
import argparse
import asyncio
import secrets
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, select, text  # noqa: E402

from app.core.database import session_scope  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.enums import UserRole, UserStatus  # noqa: E402
from app.models.user import FarmerProfile, StaffProfile, User  # noqa: E402


@dataclass
class LegacyAccount:
    legacy_user_id: int
    auth_uuid: uuid.UUID | None
    name: str
    phone: str
    email: str | None
    role: str
    status: str
    password_hash: str | None
    farmer_fields: dict = field(default_factory=dict)
    staff_fields: dict = field(default_factory=dict)


@dataclass
class MigrationReport:
    total_legacy_accounts: int = 0
    migrated: int = 0
    skipped_already_migrated: int = 0
    needs_password_reset: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def render(self) -> str:
        lines = [
            "# Legacy Identity Migration Report",
            "",
            f"- Legacy accounts found: {self.total_legacy_accounts}",
            f"- Migrated this run: {self.migrated}",
            f"- Already migrated (skipped): {self.skipped_already_migrated}",
            f"- Flagged for forced password reset (no source hash found): {len(self.needs_password_reset)}",
            f"- Errors: {len(self.errors)}",
        ]
        if self.needs_password_reset:
            lines.append("")
            lines.append("## Accounts needing a forced password reset")
            lines.extend(f"- {p}" for p in self.needs_password_reset)
        if self.errors:
            lines.append("")
            lines.append("## Errors")
            lines.extend(f"- {e}" for e in self.errors)
        return "\n".join(lines)


def fetch_legacy_accounts(legacy_db_url: str, auth_password_table: str | None, auth_password_column: str | None) -> list[LegacyAccount]:
    """Reads the legacy dual-identity model. EDIT THIS if your Phase 0
    reconciliation report shows a different shape (see module docstring
    point 4)."""
    engine = create_engine(legacy_db_url)

    query = text(
        """
        SELECT u.id AS legacy_user_id, p.id AS auth_uuid, u.name, u.phone, u.email, u.role, u.status,
               fp.address, fp.acres_of_land, fp.bank_name, fp.account_number, fp.ifsc_code, fp.upi_id, fp.bank_status,
               ap.assigned_region, ap.department
        FROM users u
        LEFT JOIN profiles p ON p.app_user_id = u.id
        LEFT JOIN farmer_profiles fp ON fp.user_id = u.id
        LEFT JOIN admin_profiles ap ON ap.user_id = u.id
        ORDER BY u.id
        """
    )

    password_lookup: dict[uuid.UUID, str] = {}
    if auth_password_table and auth_password_column:
        pw_query = text(f"SELECT id, {auth_password_column} FROM {auth_password_table}")  # noqa: S608 — operator-controlled, not user input
        with engine.connect() as conn:
            for row in conn.execute(pw_query).mappings():
                password_lookup[row["id"]] = row[auth_password_column]

    accounts = []
    with engine.connect() as conn:
        for row in conn.execute(query).mappings():
            auth_uuid = row["auth_uuid"]
            accounts.append(
                LegacyAccount(
                    legacy_user_id=row["legacy_user_id"],
                    auth_uuid=auth_uuid,
                    name=row["name"],
                    phone=row["phone"],
                    email=row["email"],
                    role=row["role"],
                    status=row["status"],
                    password_hash=password_lookup.get(auth_uuid) if auth_uuid else None,
                    farmer_fields={
                        "address": row["address"],
                        "acres_of_land": row["acres_of_land"],
                        "bank_name": row["bank_name"],
                        "account_number": row["account_number"],
                        "ifsc_code": row["ifsc_code"],
                        "upi_id": row["upi_id"],
                        "bank_status": row["bank_status"],
                    } if row["address"] is not None or row["bank_name"] is not None else {},
                    staff_fields={
                        "assigned_region": row["assigned_region"],
                        "department": row["department"],
                    } if row["assigned_region"] is not None or row["department"] is not None else {},
                )
            )
    engine.dispose()
    return accounts


async def migrate(accounts: list[LegacyAccount], *, dry_run: bool) -> MigrationReport:
    report = MigrationReport(total_legacy_accounts=len(accounts))

    async with session_scope() as db:
        for legacy in accounts:
            try:
                existing = await db.execute(select(User).where(User.legacy_app_user_id == legacy.legacy_user_id))
                if existing.scalar_one_or_none() is not None:
                    report.skipped_already_migrated += 1
                    continue

                if legacy.password_hash:
                    password_hash = legacy.password_hash
                else:
                    # No source hash found — generate an unusable random
                    # Argon2 hash (nobody knows this password) and flag
                    # the account for a forced reset rather than leaving
                    # it silently broken or, worse, guessing a default.
                    password_hash = hash_password(secrets.token_urlsafe(32))
                    report.needs_password_reset.append(f"{legacy.phone} (legacy id {legacy.legacy_user_id})")

                new_id = legacy.auth_uuid or uuid.uuid4()
                try:
                    role = UserRole(legacy.role)
                except ValueError:
                    report.errors.append(f"Unknown role '{legacy.role}' for legacy id {legacy.legacy_user_id} — skipped")
                    continue
                try:
                    status = UserStatus(legacy.status)
                except ValueError:
                    status = UserStatus.PENDING

                user = User(
                    id=new_id,
                    name=legacy.name,
                    phone=legacy.phone,
                    email=legacy.email,
                    password_hash=password_hash,
                    role=role,
                    status=status,
                    first_login=False,  # migrated accounts have logged in before
                    legacy_app_user_id=legacy.legacy_user_id,
                )
                db.add(user)
                await db.flush()

                if legacy.farmer_fields:
                    db.add(FarmerProfile(user_id=user.id, **legacy.farmer_fields))
                if legacy.staff_fields:
                    db.add(StaffProfile(user_id=user.id, **legacy.staff_fields))

                report.migrated += 1
            except Exception as exc:  # noqa: BLE001
                report.errors.append(f"legacy id {legacy.legacy_user_id}: {exc}")

        if dry_run:
            await db.rollback()
        # else: session_scope() commits on clean exit

    return report


async def main(args: argparse.Namespace) -> None:
    accounts = fetch_legacy_accounts(args.legacy_db_url, args.auth_password_table, args.auth_password_column)
    print(f"Found {len(accounts)} legacy account(s).")
    if args.dry_run:
        print("DRY RUN — no changes will be committed.")

    report = await migrate(accounts, dry_run=args.dry_run)
    text_report = report.render()
    print(text_report)

    if args.output:
        Path(args.output).write_text(text_report)
        print(f"\nReport written to {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-db-url", required=True)
    parser.add_argument("--auth-password-table", default=None, help="Table holding bcrypt password hashes, e.g. auth.users")
    parser.add_argument("--auth-password-column", default=None, help="Column name, e.g. encrypted_password")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", default=None, help="Write the report to this path in addition to stdout")
    asyncio.run(main(parser.parse_args()))
