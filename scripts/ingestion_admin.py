"""Provision Core ingestion sources, rotate/revoke keys, and map source-local asset IDs.

The generated API key is printed once by create-source/rotate-key. Store it in a secret manager.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.adapters.database import db_adapter as da


def connect():
    return da.connect(
        host=os.getenv("PGHOST", "localhost"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "anzma"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", ""),
    )


def issue_key(cur, source_id: int) -> str:
    secret = secrets.token_urlsafe(32)
    prefix = secrets.token_hex(6)
    token = f"azk_{prefix}_{secret}"
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cur.execute(
        """INSERT INTO source_credentials (source_id, key_prefix, key_hash)
           VALUES (%s,%s,%s) RETURNING credential_id""", (source_id, prefix, digest)
    )
    cur.fetchone()
    return token


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-source", help="register a source and issue its first key")
    create.add_argument("--source-code", required=True)
    create.add_argument("--building-code", required=True)
    create.add_argument("--connector-name", required=True)
    create.add_argument("--connector-version", required=True)
    building = commands.add_parser("register-building", help="assign an external-safe code to an existing building")
    building.add_argument("--building-id", type=int, required=True)
    building.add_argument("--building-code", required=True)
    rotate = commands.add_parser("rotate-key", help="issue a second active key; revoke the old key separately")
    rotate.add_argument("--source-code", required=True)
    revoke = commands.add_parser("revoke-key", help="revoke a credential immediately by source and prefix")
    revoke.add_argument("--source-code", required=True)
    revoke.add_argument("--key-prefix", required=True)
    mapping = commands.add_parser("map-asset", help="add a source-scoped external asset mapping")
    mapping.add_argument("--source-code", required=True)
    mapping.add_argument("--external-id", required=True)
    mapping.add_argument("--asset-code", required=True)
    backdate = commands.add_parser("set-backdate-limit", help="set or clear a source's maximum accepted back-date")
    backdate.add_argument("--source-code", required=True)
    backdate_limit = backdate.add_mutually_exclusive_group(required=True)
    backdate_limit.add_argument("--max-backdate-days", type=int, choices=range(0, 36501))
    backdate_limit.add_argument("--use-default", action="store_true", help="clear the override; default is 90 days")
    args = parser.parse_args()

    conn = connect()
    try:
        with conn.cursor() as cur:
            if args.command == "create-source":
                cur.execute("SELECT building_id FROM buildings WHERE building_code = %s", (args.building_code,))
                building = cur.fetchone()
                if not building:
                    raise ValueError("Unknown building. --building-code must match an existing building name.")
                cur.execute(
                    """INSERT INTO data_sources (source_code, building_id, connector_name, connector_version)
                       VALUES (%s,%s,%s,%s) RETURNING source_id""",
                    (args.source_code, building["building_id"], args.connector_name, args.connector_version),
                )
                source_id = cur.fetchone()["source_id"]
                key = issue_key(cur, source_id)
                conn.commit()
                print(f"Source created: {args.source_code}")
                print(f"API key (shown once): {key}")
                print("Store this value in a secret manager; only its SHA-256 hash is stored in PostgreSQL.")
            elif args.command == "register-building":
                cur.execute("""UPDATE buildings SET building_code=%s
                               WHERE building_id=%s AND (building_code IS NULL OR building_code=%s)
                               RETURNING building_id""",
                            (args.building_code, args.building_id, args.building_code))
                if not cur.fetchone():
                    raise ValueError("Building not found or already has a different building_code; no change made.")
                conn.commit()
                print(f"Registered building {args.building_id} as {args.building_code}.")
            elif args.command == "rotate-key":
                cur.execute("SELECT source_id FROM data_sources WHERE source_code = %s", (args.source_code,))
                source = cur.fetchone()
                if not source:
                    raise ValueError("Unknown source code.")
                key = issue_key(cur, source["source_id"])
                conn.commit()
                print(f"New API key for {args.source_code} (shown once): {key}")
                print("The previous credential remains active until explicitly revoked.")
            elif args.command == "revoke-key":
                cur.execute(
                    """UPDATE source_credentials c SET revoked_at = now()
                       FROM data_sources s
                       WHERE c.source_id = s.source_id AND s.source_code = %s
                         AND c.key_prefix = %s AND c.revoked_at IS NULL
                       RETURNING c.credential_id""", (args.source_code, args.key_prefix),
                )
                if not cur.fetchone():
                    raise ValueError("Active credential not found for that source and prefix.")
                conn.commit()
                print(f"Revoked key prefix {args.key_prefix} for {args.source_code}.")
            elif args.command == "set-backdate-limit":
                cur.execute("""UPDATE data_sources SET max_backdate_days=%s WHERE source_code=%s
                               RETURNING max_backdate_days""",
                            (None if args.use_default else args.max_backdate_days, args.source_code))
                row = cur.fetchone()
                if row is None:
                    raise ValueError("Unknown source code.")
                conn.commit()
                value = "default (90 days)" if row["max_backdate_days"] is None else f"{row['max_backdate_days']} days"
                print(f"Back-date limit for {args.source_code}: {value}.")
            else:
                cur.execute("SELECT source_id, building_id FROM data_sources WHERE source_code = %s", (args.source_code,))
                source = cur.fetchone()
                if not source:
                    raise ValueError("Unknown source code.")
                cur.execute(
                    """SELECT a.asset_id FROM assets a JOIN locations l USING (location_id)
                       WHERE a.asset_code = %s AND l.building_id = %s""",
                    (args.asset_code, source["building_id"]),
                )
                asset = cur.fetchone()
                if not asset:
                    raise ValueError("Unknown asset code in the source's building.")
                cur.execute(
                    """INSERT INTO asset_external_ids(source_id, external_id, asset_id)
                       VALUES (%s,%s,%s) ON CONFLICT (source_id, external_id) DO NOTHING
                       RETURNING asset_id""", (source["source_id"], args.external_id, asset["asset_id"]),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute("SELECT asset_id FROM asset_external_ids WHERE source_id=%s AND external_id=%s",
                                (source["source_id"], args.external_id))
                    prior = cur.fetchone()
                    if not prior or prior["asset_id"] != asset["asset_id"]:
                        raise ValueError("External ID is already mapped to a different asset; mapping was not changed.")
                    print("Mapping already exists; no change made.")
                else:
                    print(f"Mapped {args.source_code}:{args.external_id} to {args.asset_code}.")
                conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
