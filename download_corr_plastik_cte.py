#!/usr/bin/env python3
"""Download Corr Plastik CT-e attachments from an IMAP mailbox.

The script reads a table with CT-e, NF and PROCESSO columns, searches an IMAP
mailbox for those identifiers, and saves matching XML/PDF/ZIP attachments into
folders organized by process and document number.
"""

from __future__ import annotations

import argparse
import csv
import email
import imaplib
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from typing import Iterable


DEFAULT_INPUT = "cte corr plastik.txt"
DEFAULT_OUTPUT = "downloads/corr-plastik-cte"
DEFAULT_EXTENSIONS = (".xml", ".pdf", ".zip")


@dataclass(frozen=True)
class Target:
    cte: str
    nf: str
    processo: str

    @property
    def folder(self) -> str:
        return f"{self.processo}/cte-{self.cte}_nf-{self.nf}"

    @property
    def search_terms(self) -> tuple[str, ...]:
        return (self.processo, self.nf, self.cte)


def split_row(line: str) -> list[str]:
    line = line.strip()
    if "\t" in line:
        return [part.strip() for part in line.split("\t") if part.strip()]
    if ";" in line:
        return next(csv.reader([line], delimiter=";"))
    if "," in line:
        return next(csv.reader([line], delimiter=","))
    return re.split(r"\s+", line)


def normalize_header(value: str) -> str:
    value = value.strip().lower()
    value = value.replace("-", "")
    value = re.sub(r"[^a-z0-9]+", "", value)
    if value in {"cte", "ct"}:
        return "cte"
    if value in {"nf", "notafiscal"}:
        return "nf"
    if value in {"processo", "process"}:
        return "processo"
    return value


def load_targets(path: Path) -> list[Target]:
    lines = [
        line.strip()
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    if not lines:
        raise ValueError(f"Input file is empty: {path}")

    headers = [normalize_header(part) for part in split_row(lines[0])]
    index_by_header = {header: index for index, header in enumerate(headers)}
    required = ("cte", "nf", "processo")
    missing = [header for header in required if header not in index_by_header]
    if missing:
        raise ValueError(
            f"Missing required columns in {path}: {', '.join(missing)}"
        )

    targets: list[Target] = []
    for line_number, line in enumerate(lines[1:], start=2):
        columns = split_row(line)
        try:
            target = Target(
                cte=columns[index_by_header["cte"]].strip(),
                nf=columns[index_by_header["nf"]].strip(),
                processo=columns[index_by_header["processo"]].strip().upper(),
            )
        except IndexError as exc:
            raise ValueError(f"Invalid row {line_number}: {line}") from exc
        targets.append(target)
    return targets


def decode_mime(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def message_text(message: Message) -> str:
    pieces: list[str] = [
        decode_mime(message.get("Subject")),
        decode_mime(message.get("From")),
        decode_mime(message.get("To")),
        decode_mime(message.get("Date")),
    ]
    for part in message.walk():
        filename = decode_mime(part.get_filename())
        if filename:
            pieces.append(filename)
        content_type = part.get_content_type()
        if content_type not in {"text/plain", "text/html"}:
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            pieces.append(payload.decode(charset, errors="replace"))
        except LookupError:
            pieces.append(payload.decode("utf-8", errors="replace"))
    return "\n".join(pieces).lower()


def safe_name(value: str, fallback: str) -> str:
    value = decode_mime(value).strip() or fallback
    value = value.replace("\\", "_").replace("/", "_")
    value = re.sub(r"[^A-Za-z0-9._ -]+", "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or fallback


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 2
    while True:
        candidate = parent / f"{stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def imap_date(value: str) -> str:
    parsed = datetime.strptime(value, "%Y-%m-%d")
    return parsed.strftime("%d-%b-%Y")


def build_search_criteria(term: str, since: str | None) -> list[str]:
    criteria: list[str] = []
    if since:
        criteria.extend(["SINCE", imap_date(since)])
    criteria.extend(["TEXT", f'"{term}"'])
    return criteria


def search_uids(
    client: imaplib.IMAP4_SSL,
    terms: Iterable[str],
    since: str | None,
) -> set[bytes]:
    uids: set[bytes] = set()
    for term in terms:
        status, data = client.uid("search", None, *build_search_criteria(term, since))
        if status != "OK":
            print(f"warning: search failed for term {term!r}: {status}", file=sys.stderr)
            continue
        for chunk in data:
            if chunk:
                uids.update(chunk.split())
    return uids


def fetch_message(client: imaplib.IMAP4_SSL, uid: bytes) -> Message | None:
    status, data = client.uid("fetch", uid, "(RFC822)")
    if status != "OK":
        print(f"warning: fetch failed for UID {uid.decode()}: {status}", file=sys.stderr)
        return None
    for item in data:
        if isinstance(item, tuple) and item[1]:
            return email.message_from_bytes(item[1])
    return None


def attachment_parts(message: Message) -> Iterable[tuple[str, bytes]]:
    for part in message.walk():
        filename = decode_mime(part.get_filename())
        disposition = (part.get_content_disposition() or "").lower()
        if not filename and disposition != "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        yield filename, payload


def attachment_allowed(filename: str, extensions: tuple[str, ...]) -> bool:
    if not filename:
        return True
    return filename.lower().endswith(extensions)


def manifest_writer(path: Path) -> tuple[csv.DictWriter, object]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(
        handle,
        fieldnames=[
            "cte",
            "nf",
            "processo",
            "uid",
            "subject",
            "from",
            "date",
            "filename",
            "saved_path",
            "status",
        ],
    )
    writer.writeheader()
    return writer, handle


def connect(host: str, user: str, password: str, mailbox: str) -> imaplib.IMAP4_SSL:
    client = imaplib.IMAP4_SSL(host)
    client.login(user, password)
    status, _ = client.select(mailbox)
    if status != "OK":
        raise RuntimeError(f"Could not select mailbox {mailbox!r}: {status}")
    return client


def print_plan(targets: list[Target]) -> None:
    processes = sorted({target.processo for target in targets})
    print(f"Targets: {len(targets)}")
    print(f"Processes: {', '.join(processes)}")
    print("Example email search terms:")
    for target in targets[:10]:
        print(
            f"- processo:{target.processo} nf:{target.nf} cte:{target.cte}"
        )
    if len(targets) > 10:
        print(f"... {len(targets) - 10} more")


def run(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    output_dir = Path(args.output)
    targets = load_targets(input_path)

    if args.plan_only:
        print_plan(targets)
        return 0

    host = args.host or os.environ.get("IMAP_HOST")
    user = args.user or os.environ.get("IMAP_USER")
    password = os.environ.get(args.password_env)
    mailbox = args.mailbox or os.environ.get("IMAP_MAILBOX", "INBOX")
    if not host or not user or not password:
        print(
            "Missing IMAP credentials. Set IMAP_HOST, IMAP_USER and "
            f"{args.password_env}, or pass --host/--user and set the password env var.",
            file=sys.stderr,
        )
        return 2

    extensions = tuple(extension.lower() for extension in args.extensions)
    manifest_path = output_dir / "manifest.csv"
    writer, manifest_handle = manifest_writer(manifest_path)
    client = connect(host, user, password, mailbox)
    saved_count = 0
    matched_targets = 0

    try:
        for index, target in enumerate(targets, start=1):
            terms = tuple(dict.fromkeys((*target.search_terms, args.company)))
            print(f"[{index}/{len(targets)}] Searching {target.folder}")
            uids = search_uids(client, terms, args.since)
            target_matched = False

            for uid in sorted(uids, key=lambda value: int(value)):
                message = fetch_message(client, uid)
                if message is None:
                    continue
                haystack = message_text(message)
                if not any(term.lower() in haystack for term in target.search_terms):
                    continue

                attachments = list(attachment_parts(message))
                if not attachments:
                    continue

                target_matched = True
                target_dir = output_dir / target.folder
                if not args.dry_run:
                    target_dir.mkdir(parents=True, exist_ok=True)

                for position, (filename, payload) in enumerate(attachments, start=1):
                    if not attachment_allowed(filename, extensions):
                        continue
                    fallback = f"attachment-{uid.decode()}-{position}.bin"
                    safe_filename = safe_name(filename, fallback)
                    saved_path = target_dir / safe_filename
                    if not args.dry_run:
                        saved_path = unique_path(saved_path)
                        saved_path.write_bytes(payload)
                        saved_count += 1
                    writer.writerow(
                        {
                            "cte": target.cte,
                            "nf": target.nf,
                            "processo": target.processo,
                            "uid": uid.decode(),
                            "subject": decode_mime(message.get("Subject")),
                            "from": decode_mime(message.get("From")),
                            "date": decode_mime(message.get("Date")),
                            "filename": safe_filename,
                            "saved_path": str(saved_path),
                            "status": "dry-run" if args.dry_run else "saved",
                        }
                    )

            if target_matched:
                matched_targets += 1
            else:
                writer.writerow(
                    {
                        "cte": target.cte,
                        "nf": target.nf,
                        "processo": target.processo,
                        "uid": "",
                        "subject": "",
                        "from": "",
                        "date": "",
                        "filename": "",
                        "saved_path": "",
                        "status": "not-found",
                    }
                )
    finally:
        manifest_handle.close()
        try:
            client.logout()
        except Exception:
            pass

    print(f"Matched targets: {matched_targets}/{len(targets)}")
    print(f"Saved attachments: {saved_count}")
    print(f"Manifest: {manifest_path}")
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Corr Plastik CT-e attachments from IMAP email."
    )
    parser.add_argument("--input", default=DEFAULT_INPUT, help="Input CT-e table")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Download folder")
    parser.add_argument("--host", help="IMAP host, or IMAP_HOST env var")
    parser.add_argument("--user", help="IMAP user, or IMAP_USER env var")
    parser.add_argument(
        "--password-env",
        default="IMAP_PASSWORD",
        help="Environment variable that contains the IMAP password/app password",
    )
    parser.add_argument(
        "--mailbox",
        help="Mailbox/folder to search, or IMAP_MAILBOX env var. Default: INBOX",
    )
    parser.add_argument(
        "--company",
        default="corr plastik",
        help="Extra company term included in each search",
    )
    parser.add_argument(
        "--since",
        help="Only search messages since YYYY-MM-DD",
    )
    parser.add_argument(
        "--extensions",
        nargs="+",
        default=list(DEFAULT_EXTENSIONS),
        help="Attachment extensions to save",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Connect and search, but do not write attachment files",
    )
    parser.add_argument(
        "--plan-only",
        action="store_true",
        help="Read the list and print search terms without connecting to email",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args(sys.argv[1:])))
