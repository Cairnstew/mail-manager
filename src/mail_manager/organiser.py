import re
import time
import imaplib
import email

from mail_manager.classifier import ChatModel


class Filter:
    """Common IMAP search filters. Pass a list to run() — they are AND-ed together."""

    UNSEEN = "UNSEEN"
    ALL = "ALL"
    SEEN = "SEEN"

    @staticmethod
    def since(date: str) -> str:
        return f"SINCE {date}"

    @staticmethod
    def before(date: str) -> str:
        return f"BEFORE {date}"

    @staticmethod
    def from_(address: str) -> str:
        return f"FROM {address}"

    @staticmethod
    def subject(keyword: str) -> str:
        return f"SUBJECT {keyword}"

    @staticmethod
    def larger(bytes_: int) -> str:
        return f"LARGER {bytes_}"


class MailOrganiser:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        buckets: dict[str, str],
        confidence_threshold: float = 0.0,
        fallback: str = "Archive",
        dry_run: bool = False,
        device: str = "auto",
        debug: bool = False,
    ):
        self.host = host
        self.username = username
        self.password = password
        self.buckets = buckets
        self.confidence_threshold = confidence_threshold
        self.fallback = fallback
        self.dry_run = dry_run
        self.classifier = ChatModel(device=device, debug=debug)

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Open a fresh authenticated IMAP connection."""
        mail = imaplib.IMAP4_SSL(self.host)
        mail.login(self.username, self.password)
        return mail

    def _reconnect(self, current_mailbox: str) -> imaplib.IMAP4_SSL:
        """Reconnect and reselect the current mailbox after a dropped connection."""
        print("  [RECONNECT] Connection lost, reconnecting...")
        for attempt in range(1, 4):
            try:
                mail = self._connect()
                mail.select(f'"{current_mailbox}"')
                print(f"  [RECONNECT] Reconnected on attempt {attempt}.")
                return mail
            except Exception as e:
                print(f"  [RECONNECT] Attempt {attempt} failed: {e}")
                time.sleep(5 * attempt)
        raise ConnectionError("Failed to reconnect to IMAP server after 3 attempts.")

    # ── Email parsing ─────────────────────────────────────────────────────────

    @staticmethod
    def _get_email_text(raw: bytes) -> str:
        msg = email.message_from_bytes(raw)
        subject = msg.get("Subject", "(no subject)")
        sender  = msg.get("From", "(unknown sender)")
        to      = msg.get("To", "")

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = msg.get_payload(decode=True).decode(errors="ignore")

        return (
            f"From: {sender}\n"
            f"To: {to}\n"
            f"Subject: {subject}\n\n"
            f"{body[:1000]}"
        )

    # ── IMAP helpers ──────────────────────────────────────────────────────────

    def _folder_exists(self, mail: imaplib.IMAP4_SSL, folder: str) -> bool:
        """Check if a folder exists using LIST rather than SELECT."""
        _, listing = mail.list('""', f'"{folder}"')
        return bool(listing and listing[0])

    def _ensure_folder(self, mail: imaplib.IMAP4_SSL, folder: str) -> None:
        """Create the folder if it doesn't exist, creating parent levels first."""
        parts = folder.split("/")
        for i in range(1, len(parts) + 1):
            path = "/".join(parts[:i])
            if not self._folder_exists(mail, path):
                mail.create(f'"{path}"')
                print(f"  Created folder: {path}")
        mail.select('"INBOX"')

    def _move(self, mail: imaplib.IMAP4_SSL, uid: bytes, folder: str) -> None:
        mail.uid("copy", uid, f'"{folder}"')
        mail.uid("store", uid, "+FLAGS", "\\Deleted")

    def _process_mailbox(
        self,
        mail: imaplib.IMAP4_SSL,
        mailbox: str,
        search_query: str,
    ) -> tuple[dict[str, int], imaplib.IMAP4_SSL]:
        """Process a single mailbox. Returns (summary, mail) — mail may be a new
        connection if the original dropped and was reconnected."""
        summary: dict[str, int] = {}

        status, _ = mail.select(f'"{mailbox}"')
        if status != "OK":
            print(f"  [SKIP] Could not select '{mailbox}'.")
            return summary, mail

        _, msg_ids = mail.uid("search", None, search_query)
        uids = msg_ids[0].split()
        print(f"  Found {len(uids)} message(s).")

        total = len(uids)
        for i, uid in enumerate(uids, start=1):
            # Fetch
            try:
                _, data = mail.uid("fetch", uid, "(RFC822)")
            except (imaplib.IMAP4.abort, OSError):
                mail = self._reconnect(mailbox)
                _, data = mail.uid("fetch", uid, "(RFC822)")

            if data is None or data[0] is None:
                print(f"  [{i}/{total}] [SKIP] Could not fetch UID {uid.decode()}.")
                continue

            raw: bytes = data[0][1]
            email_text = self._get_email_text(raw)

            # Classify (this is where the connection is most likely to time out)
            bucket, confidence = self.classifier.classify(
                email_text,
                self.buckets,
                confidence_threshold=self.confidence_threshold,
                fallback=self.fallback,
            )
            preview = email_text.splitlines()[0][:72]
            print(f"  [{i}/{total}] [{bucket}] ({confidence:.2f}) {preview!r}")
            summary[bucket] = summary.get(bucket, 0) + 1

            # Move — reconnect if needed
            if not self.dry_run:
                try:
                    self._move(mail, uid, bucket)
                except (imaplib.IMAP4.abort, OSError):
                    mail = self._reconnect(mailbox)
                    self._move(mail, uid, bucket)

        if not self.dry_run:
            try:
                mail.expunge()
            except (imaplib.IMAP4.abort, OSError):
                mail = self._reconnect(mailbox)
                mail.expunge()

        return summary, mail

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        mailbox: str = "INBOX",
        filters: list[str] | None = None,
    ) -> dict[str, int]:
        """Classify and organise emails in a single mailbox."""
        if filters is None:
            filters = [Filter.UNSEEN]

        search_query = " ".join(filters)
        mail = self._connect()
        try:
            for bucket in set(list(self.buckets) + [self.fallback]):
                self._ensure_folder(mail, bucket)
            summary, mail = self._process_mailbox(mail, mailbox, search_query)
        finally:
            try:
                mail.logout()
            except Exception:
                pass
        return summary

    def list_folders(self, mail: imaplib.IMAP4_SSL) -> list[str]:
        """Return all folder names using an existing connection."""
        folders = []
        _, folder_list = mail.list()
        for item in folder_list:
            decoded = item.decode()
            match = re.search(r'"([^"]+)"\s*$', decoded)
            name = match.group(1).strip() if match else decoded.split()[-1].strip()
            if name:
                folders.append(name)
        return folders

    def run_all(
        self,
        skip: list[str] | None = None,
        filters: list[str] | None = None,
    ) -> dict[str, dict[str, int]]:
        """Re-classify emails across all folders using a single connection."""
        if filters is None:
            filters = [Filter.ALL]

        search_query = " ".join(filters)

        default_skip = {
            "[Gmail]/All Mail",
            "[Gmail]/Sent Mail",
            "[Gmail]/Drafts",
            "[Gmail]/Trash",
            "[Gmail]/Starred",
            "[Gmail]/Important",
            "Drafts",
            "Sent",
            "Trash",
            "Junk",
        }
        skip_set = default_skip | set(skip or [])
        results: dict[str, dict[str, int]] = {}

        mail = self._connect()
        try:
            for bucket in set(list(self.buckets) + [self.fallback]):
                self._ensure_folder(mail, bucket)

            all_folders = self.list_folders(mail)
            to_process = [f for f in all_folders if f not in skip_set]
            print(f"Found {len(all_folders)} folders, processing {len(to_process)}.")

            for folder in to_process:
                print(f"\n── {folder} ──")
                summary, mail = self._process_mailbox(mail, folder, search_query)
                results[folder] = summary
        finally:
            try:
                mail.logout()
            except Exception:
                pass

        return results