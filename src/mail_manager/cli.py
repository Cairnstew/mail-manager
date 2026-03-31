"""
mail-manager CLI

Usage examples:
  mail-manager run
  mail-manager run --mailbox INBOX --filter all
  mail-manager run --mailbox INBOX --filter unseen --dry-run
  mail-manager run-all
  mail-manager run-all --filter all --skip Archive --skip Charity
  mail-manager folders
"""

import argparse
import os
import sys

# Load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from mail_manager.organiser import Filter, MailOrganiser

# ── Default bucket config ─────────────────────────────────────────────────────
# Users can override this by editing the config file at ~/.mail_manager/buckets.py
# or by passing --config <path>

DEFAULT_BUCKETS: dict[str, str] = {
    "Work/Inbox":        "active conversations, meetings, ongoing work threads",
    "Work/Archive":      "completed or inactive work conversations",
    "Work/Github":       "GitHub notifications, PRs, issues, CI/CD alerts",
    "Work/Infra":        "cloud, terraform, nix, servers, deployments, alerts",
    "Work/Applications": "job applications, recruiter emails, interview scheduling",

    "Education/Inbox":      "active course emails, lecturers, group work",
    "Education/Coursework": "assignments, deadlines, submissions, feedback",
    "Education/Admin":      "university admin, enrolment, timetables, policies",
    "Education/Resources":  "learning materials, papers, tools, datasets",

    "Personal/Inbox":    "friends, family, direct personal messages",
    "Personal/Events":   "tickets, bookings, social plans",

    "Health":            "appointments, prescriptions, test results, health services",
    "Health/Fitness":    "gym, fitness apps, activity tracking, wellness",

    "Finance":           "banking, invoices, receipts, subscriptions, billing",
    "Finance/Orders":    "online purchases, delivery updates, order confirmations",

    "Travel":            "flight bookings, hotels, transport, itineraries",
    "Travel/Updates":    "delays, check-ins, boarding passes, travel alerts",

    "Gaming":            "game purchases, updates, platform notifications",
    "Gaming/Community":  "forums, Discord, events, gaming newsletters",

    "Security":          "account security alerts, password resets, login attempts, 2FA",

    "Notifications":     "app alerts, automated notifications, account activity",
    "Newsletters":       "mailing lists, digests, marketing content",

    "Archive": "emails that don't fit any other category, low-confidence classifications, general archive",
    "ToReview":          "uncertain classification, needs manual check",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

FILTER_MAP = {
    "all":    Filter.ALL,
    "unseen": Filter.UNSEEN,
    "seen":   Filter.SEEN,
}


def load_config(path: str | None) -> dict[str, str]:
    """Load buckets from an external Python config file if provided."""
    if path is None:
        # Check for a default config at ~/.mail_manager/buckets.py
        default = os.path.expanduser("~/.mail_manager/buckets.py")
        if os.path.exists(default):
            path = default
        else:
            return DEFAULT_BUCKETS

    namespace: dict = {}
    with open(path) as f:
        exec(f.read(), namespace)  # noqa: S102

    if "BUCKETS" not in namespace:
        print(f"Error: config file '{path}' must define a BUCKETS dict.", file=sys.stderr)
        sys.exit(1)

    return namespace["BUCKETS"]


def build_organiser(args: argparse.Namespace, buckets: dict[str, str]) -> MailOrganiser:
    username = args.username or os.getenv("GMAIL_USER")
    password = args.password or os.getenv("GMAIL_PASSWORD")

    if not username or not password:
        print(
            "Error: username and password are required.\n"
            "Set GMAIL_USER and GMAIL_PASSWORD env vars, or pass --username / --password.",
            file=sys.stderr,
        )
        sys.exit(1)

    return MailOrganiser(
        host=args.host,
        username=username,
        password=password,
        buckets=buckets,
        confidence_threshold=args.confidence,
        fallback=args.fallback,
        dry_run=args.dry_run,
        device=args.device,
        debug=getattr(args, "debug", False),
    )


def resolve_filters(filter_args: list[str]) -> list[str]:
    """Convert filter name strings to IMAP filter strings."""
    filters = []
    for f in filter_args:
        if f in FILTER_MAP:
            filters.append(FILTER_MAP[f])
        elif f.startswith("since:"):
            filters.append(Filter.since(f.removeprefix("since:")))
        elif f.startswith("before:"):
            filters.append(Filter.before(f.removeprefix("before:")))
        elif f.startswith("from:"):
            filters.append(Filter.from_(f.removeprefix("from:")))
        elif f.startswith("subject:"):
            filters.append(Filter.subject(f.removeprefix("subject:")))
        else:
            print(f"Warning: unknown filter '{f}', ignoring.", file=sys.stderr)
    return filters or [Filter.UNSEEN]


def print_summary(summary: dict[str, int]) -> None:
    if not summary:
        print("  (nothing moved)")
        return
    total = sum(summary.values())
    for bucket, count in sorted(summary.items(), key=lambda x: -x[1]):
        print(f"  {bucket:<40} {count:>4}")
    print(f"  {'TOTAL':<40} {total:>4}")


# ── Subcommands ───────────────────────────────────────────────────────────────

def cmd_run(args: argparse.Namespace) -> None:
    buckets = load_config(args.config)
    organiser = build_organiser(args, buckets)
    filters = resolve_filters(args.filter)

    print(f"Running on mailbox '{args.mailbox}' with filters: {filters}")
    summary = organiser.run(mailbox=args.mailbox, filters=filters)

    print("\nSummary:")
    print_summary(summary)


def cmd_run_all(args: argparse.Namespace) -> None:
    buckets = load_config(args.config)
    organiser = build_organiser(args, buckets)
    filters = resolve_filters(args.filter)

    print(f"Running across all folders with filters: {filters}")
    results = organiser.run_all(skip=args.skip or [], filters=filters)

    print("\nFull summary:")
    for folder, summary in results.items():
        if summary:
            print(f"\n  {folder}:")
            for bucket, count in sorted(summary.items(), key=lambda x: -x[1]):
                print(f"    {bucket:<38} {count:>4}")


def cmd_folders(args: argparse.Namespace) -> None:
    import imaplib
    import re

    username = args.username or os.getenv("GMAIL_USER")
    password = args.password or os.getenv("GMAIL_PASSWORD")

    if not username or not password:
        print("Error: username and password required.", file=sys.stderr)
        sys.exit(1)

    with imaplib.IMAP4_SSL(args.host) as mail:
        mail.login(username, password)
        _, folder_list = mail.list()

        print(f"{'Folder':<50} {'Total':>7} {'Unread':>7}")
        print("-" * 66)

        for item in folder_list:
            decoded = item.decode()
            match = re.search(r'"([^"]+)"\s*$', decoded)
            name = match.group(1).strip() if match else decoded.split()[-1].strip()
            if not name:
                continue

            status, data = mail.select(f'"{name}"', readonly=True)
            if status != "OK":
                print(f"{name:<50} {'N/A':>7}")
                continue

            total = int(data[0]) if data[0] else 0
            _, unread_data = mail.search(None, "UNSEEN")
            unread = len(unread_data[0].split()) if unread_data[0] else 0
            print(f"{name:<50} {total:>7} {unread:>7}")


def cmd_logs(args: argparse.Namespace) -> None:
    """Print recent classification log entries."""
    import json

    log_path = os.path.expanduser("~/.mail_manager/classifications.jsonl")
    if not os.path.exists(log_path):
        print("No log file found. Run the organiser first.")
        return

    with open(log_path) as f:
        entries = [json.loads(line) for line in f if line.strip()]

    # Apply filters
    if args.fallbacks:
        entries = [e for e in entries if e.get("fell_back")]
    if args.bucket:
        entries = [e for e in entries if e.get("bucket") == args.bucket]

    # Most recent first, limited to --limit
    entries = entries[-args.limit:][::-1]

    if not entries:
        print("No matching log entries.")
        return

    print(f"{'Time':<22} {'Bucket':<25} {'Conf':>5}  {'Preview'}")
    print("-" * 100)
    for e in entries:
        time   = e.get("timestamp", "")[:19].replace("T", " ")
        bucket = e.get("bucket", "")
        conf   = e.get("confidence", 0.0)
        prev   = e.get("preview", "")[:55]
        flag   = " !" if e.get("fell_back") else "  "
        print(f"{time:<22} {bucket:<25} {conf:>5.2f}{flag} {prev}")

    print(f"{len(entries)} entries shown.")


# ── Argument parser ───────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail-manager",
        description="AI-powered IMAP email organiser",
    )

    # Connection options (global — before subcommand)
    parser.add_argument("--host",     default="imap.gmail.com", help="IMAP host (default: imap.gmail.com)")
    parser.add_argument("--username", default=None,             help="Email address (or set GMAIL_USER)")
    parser.add_argument("--password", default=None,             help="App password (or set GMAIL_PASSWORD)")
    parser.add_argument("--config",   default=None,             help="Path to a Python config file defining BUCKETS dict")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Shared options added to both run and run-all
    def add_run_options(p):
        p.add_argument("--confidence", default=0.75, type=float,
                       help="Confidence threshold 0.0-1.0 (default: 0.75)")
        p.add_argument("--fallback",   default="Archive",
                       help="Fallback bucket for low-confidence emails (default: Archive)")
        p.add_argument("--dry-run",    action="store_true",
                       help="Preview classifications without moving emails")
        p.add_argument("--device",     default="auto",
                       help="Model device: cpu, cuda, auto (default: auto)")
        p.add_argument("--debug",      action="store_true",
                       help="Print raw model output for each email")

    # run
    run_parser = subparsers.add_parser("run", help="Organise a single mailbox")
    run_parser.add_argument("--mailbox", default="INBOX",
                            help="Mailbox to process (default: INBOX)")
    run_parser.add_argument("--filter",  default=["unseen"], nargs="+",
                            help="Filters: all, unseen, seen, since:DD-Mon-YYYY, before:DD-Mon-YYYY, from:addr, subject:word (default: unseen)")
    add_run_options(run_parser)
    run_parser.set_defaults(func=cmd_run)

    # run-all
    run_all_parser = subparsers.add_parser("run-all", help="Organise all folders")
    run_all_parser.add_argument("--filter", default=["all"], nargs="+",
                                help="Filters to apply in every folder (default: all)")
    run_all_parser.add_argument("--skip", nargs="+", default=[],
                                help="Folder names to skip")
    add_run_options(run_all_parser)
    run_all_parser.set_defaults(func=cmd_run_all)

    # folders
    folders_parser = subparsers.add_parser("folders", help="List all folders with email counts")
    folders_parser.set_defaults(func=cmd_folders)

    # logs
    logs_parser = subparsers.add_parser("logs", help="Review classification log")
    logs_parser.add_argument("--limit",     default=50,   type=int,  help="Number of entries to show (default: 50)")
    logs_parser.add_argument("--fallbacks", action="store_true",     help="Show only emails that fell back to the fallback bucket")
    logs_parser.add_argument("--bucket",    default=None,            help="Filter by bucket name")
    logs_parser.set_defaults(func=cmd_logs)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()