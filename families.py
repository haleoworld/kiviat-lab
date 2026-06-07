#!/usr/bin/env python3
"""Manage families and their members (multi-tenant scaffolding).

    python families.py list
    python families.py create <id> --name "My Household" [--currency CAD] [--activate]
    python families.py add-member <family-id> <member-id> --name "Alice" [--role earner]
    python families.py members <family-id>
    python families.py activate <family-id>

A "family" is a top-level tenant: its own folder, config, members, inbox, and
event log. Everything downstream (extraction, review, dashboard) is scoped to a
family. Plain files, no database.
"""
from __future__ import annotations

import argparse
import re
import sys

import yaml

import config
import paths

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _valid_id(value: str, what: str) -> None:
    if not ID_RE.match(value):
        die(f"invalid {what} id: {value!r} — use lowercase letters, digits, '-' or '_'")


def _set_active_family(family_id: str) -> None:
    """Write active_family into app-config.yaml, preserving other keys."""
    data = {}
    if paths.APP_CONFIG_FILE.exists():
        with open(paths.APP_CONFIG_FILE) as f:
            data = yaml.safe_load(f) or {}
    data["active_family"] = family_id
    paths.DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with open(paths.APP_CONFIG_FILE, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def cmd_list(_: argparse.Namespace) -> None:
    families = paths.list_families()
    if not families:
        print("no families yet. Create one:\n  python families.py create <id> --name \"...\"")
        return
    active = config.load_app_config().get("active_family")
    for fid in families:
        fcfg = config.load_family_config(fid)
        members = config.load_members(fid)
        star = " *" if fid == active else "  "
        name = fcfg.get("name") or "(unnamed)"
        print(f"{star} {fid:<20} {name:<24} {len(members)} member(s)")
    if active:
        print("\n  * = active family")


def cmd_create(args: argparse.Namespace) -> None:
    _valid_id(args.id, "family")
    if paths.family_exists(args.id):
        die(f"family already exists: {args.id}")
    paths.ensure_family_dirs(args.id)
    family_cfg = {
        "name": args.name or args.id,
        "currency": args.currency,
        "household": {},
        "review_thresholds": {"high_amount": 1000, "anomaly_multiplier": 2.0},
        "stress_test": {},
    }
    with open(paths.family_config_file(args.id), "w") as f:
        yaml.safe_dump(family_cfg, f, sort_keys=False)
    with open(paths.members_file(args.id), "w") as f:
        yaml.safe_dump({"members": []}, f, sort_keys=False)

    activated = ""
    if args.activate or not config.load_app_config().get("active_family"):
        _set_active_family(args.id)
        activated = " (now active)"
    print(f"✓ created family '{args.id}' — {family_cfg['name']}{activated}")
    print(f"  {paths.family_dir(args.id)}")


def cmd_add_member(args: argparse.Namespace) -> None:
    if not paths.family_exists(args.family):
        die(f"no such family: {args.family}")
    _valid_id(args.member, "member")
    members = config.load_members(args.family)
    if any(m.get("id") == args.member for m in members):
        die(f"member already exists in {args.family}: {args.member}")
    members.append({"id": args.member, "name": args.name or args.member, "role": args.role})
    with open(paths.members_file(args.family), "w") as f:
        yaml.safe_dump({"members": members}, f, sort_keys=False)
    print(f"✓ added member '{args.member}' ({args.name or args.member}) to {args.family}")


def cmd_members(args: argparse.Namespace) -> None:
    if not paths.family_exists(args.family):
        die(f"no such family: {args.family}")
    members = config.load_members(args.family)
    if not members:
        print(f"{args.family}: no members yet")
        return
    for m in members:
        role = f" [{m['role']}]" if m.get("role") else ""
        print(f"  {m.get('id'):<16} {m.get('name', ''):<24}{role}")


def cmd_activate(args: argparse.Namespace) -> None:
    if not paths.family_exists(args.family):
        die(f"no such family: {args.family}")
    _set_active_family(args.family)
    print(f"✓ active family set to '{args.family}'")


def main() -> None:
    ap = argparse.ArgumentParser(description="Manage families and members.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="list families").set_defaults(func=cmd_list)

    p = sub.add_parser("create", help="create a family")
    p.add_argument("id", help="short id, e.g. 'household' or 'parents'")
    p.add_argument("--name", help="display name")
    p.add_argument("--currency", default="CAD")
    p.add_argument("--activate", action="store_true", help="make this the active family")
    p.set_defaults(func=cmd_create)

    p = sub.add_parser("add-member", help="add a member to a family")
    p.add_argument("family")
    p.add_argument("member", help="short id, e.g. 'alice'")
    p.add_argument("--name")
    p.add_argument("--role", help="e.g. earner, dependent")
    p.set_defaults(func=cmd_add_member)

    p = sub.add_parser("members", help="list a family's members")
    p.add_argument("family")
    p.set_defaults(func=cmd_members)

    p = sub.add_parser("activate", help="set the active family")
    p.add_argument("family")
    p.set_defaults(func=cmd_activate)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
