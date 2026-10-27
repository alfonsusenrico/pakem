"""`pakem docs`: freelance engagement documents kept as plain files (ADR 0014).

The store is one private directory outside every repository, named in
~/.config/pakem/documents.toml. Inside it, `config.toml` holds issuers and numbering,
`wording/` the owner-approved fixed text, and one folder per engagement:
`engagement.toml` (facts the owner gives, written once by pakem and then edited by the
agent), `state.toml` (stage and counters, pakem's), `records.toml` (append-only owner
facts: approvals, payments, handover, voids, renders), and a folder per document with one
content file per version (`v1.toml`) and its rendered `.html` and `.pdf`.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import tempfile
import tomllib
from datetime import date
from pathlib import Path

from .common import (
    EXIT_CHECK_FAILED,
    CheckFailed,
    CommandRefused,
    OwnerActionRequired,
    PakemError,
    Report,
    confirm_steps,
    display_path,
    expand_user_path,
    is_git_work_tree,
    read_text,
    sha256_file,
    toml_string,
    toml_value,
    utc_now_iso,
    write_text_atomically,
)
from .device_config import config_dir
from .docs_render import (
    amount_in_words,
    e,
    facts_block,
    fill,
    find_browser,
    format_date,
    format_money,
    format_number,
    headless_shell_download,
    headless_shell_root,
    install_headless_shell,
    label,
    list_html,
    page,
    parse_date,
    parse_wording,
    print_pdf,
    section,
    signatures,
    table,
    weekday,
    wording_html,
    wording_names,
)
from .docs_types import AMOUNT, DATE, FIXED, LIST, PREFIXES, ROWS, TEXT, TYPES, WORKSTREAM, DocType

STORE_SETTINGS = "documents.toml"
PROJECT_CODE = re.compile(r"^PRJ-(\d{4})-(\d{3})$")
# A value nobody filled yet: a bracketed capital word ([FILL], [NAMA KLIEN]) or an owner
# question ([OWNER: fee]).
PLACEHOLDER = re.compile(r"\[(?:[A-Z][A-Z0-9 _./-]*|OWNER:[^\]]*)\]")
LANGUAGES = ("id", "en")
CURRENCIES = ("IDR", "USD")
FILL_TEXT = "[FILL]"
STAGES = {FIXED: ("discovery", "proposal", "development", "finishing", "closed"), WORKSTREAM: ("active", "closed")}
# A receipt above this many rupiah carries a Rp10,000 duty stamp (UU 10/2020).
STAMP_THRESHOLD_IDR = 5_000_000
ISSUER_FIELDS = ("name", "email", "phone", "city", "address", "bank", "account_number", "account_holder")


# ---------- the store ----------

def settings_path() -> Path:
    return config_dir() / STORE_SETTINGS


def load_toml(path: Path) -> dict:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except FileNotFoundError as exc:
        raise PakemError(f"{display_path(path)} is missing") from exc
    except tomllib.TOMLDecodeError as exc:
        raise PakemError(f"invalid TOML in {display_path(path)}: {exc}") from exc


def store_settings() -> dict:
    if not settings_path().is_file():
        raise PakemError("no documents store yet. Run `pakem docs init --store <dir> --last-project PRJ-YYYY-NNN`")
    return load_toml(settings_path())


def store_root() -> Path:
    root = expand_user_path(str(store_settings().get("root", "")))
    if not (root / "config.toml").is_file():
        raise PakemError(f"the documents store {display_path(root)} has no config.toml. Run `pakem docs init`")
    return root


def inside_git(directory: Path) -> bool:
    probe = directory
    while not probe.exists():
        probe = probe.parent
    return is_git_work_tree(probe)


def engagement_dir(root: Path, code: str) -> Path:
    if not PROJECT_CODE.match(code):
        raise PakemError(f"{code!r} is not a project code (PRJ-YYYY-NNN)")
    matches = sorted(root.glob(f"{code}-*")) + ([root / code] if (root / code).is_dir() else [])
    if not matches:
        raise PakemError(f"no engagement {code} in {display_path(root)}")
    return matches[0]


def project_of(document_code: str) -> str:
    match = re.match(r"^[A-Z]+-(\d{4})-(\d{3})(?:-\d{2})?$", document_code)
    if not match:
        raise PakemError(f"{document_code!r} is not a document code")
    return f"PRJ-{match.group(1)}-{match.group(2)}"


def type_of(document_code: str) -> DocType:
    prefix = document_code.split("-", 1)[0]
    if prefix not in PREFIXES:
        raise PakemError(f"{document_code!r} has an unknown document prefix {prefix!r}")
    return PREFIXES[prefix]


# ---------- engagement state and records ----------

class Engagement:
    def __init__(self, root: Path, code: str) -> None:
        self.root = root
        self.code = code
        self.dir = engagement_dir(root, code)
        self.data = load_toml(self.dir / "engagement.toml")
        self.state = load_toml(self.dir / "state.toml")
        records_path = self.dir / "records.toml"
        self.records = load_toml(records_path).get("record", []) if records_path.is_file() else []

    @property
    def kind(self) -> str:
        return self.data.get("kind", FIXED)

    @property
    def lang(self) -> str:
        return self.data.get("language", "id")

    @property
    def stage(self) -> str:
        return self.state.get("stage", STAGES[self.kind][0])

    def set_stage(self, stage: str) -> None:
        self.state["stage"] = stage
        self.save_state()
        self.add_record("stage", stage=stage)

    def save_state(self) -> None:
        lines = ["# Written by pakem docs. Do not edit by hand.", f"stage = {toml_string(self.stage)}", "", "[counters]"]
        lines += [f"{key} = {value}" for key, value in sorted(self.state.get("counters", {}).items())]
        write_text_atomically(self.dir / "state.toml", "\n".join(lines) + "\n")

    def add_record(self, kind: str, **values: object) -> None:
        entry = {"kind": kind, "at": utc_now_iso(), **{key: value for key, value in values.items() if value is not None}}
        block = "\n[[record]]\n" + "".join(f"{key} = {toml_value(value)}\n" for key, value in entry.items())
        path = self.dir / "records.toml"
        existing = read_text(path) if path.is_file() else "# Append-only record of owner facts, written by pakem docs.\n"
        write_text_atomically(path, existing + block)
        self.records.append(entry)

    def of(self, kind: str, document: str | None = None) -> list[dict]:
        return [record for record in self.records
                if record["kind"] == kind and (document is None or record.get("document") == document)]

    def approved(self, document: str) -> dict | None:
        found = self.of("approval", document)
        return found[-1] if found else None

    def voided(self, document: str) -> bool:
        return bool(self.of("void", document))

    def documents(self) -> list[str]:
        return sorted(path.name for path in self.dir.iterdir() if path.is_dir() and (path / "v1.toml").is_file())

    def approved_offer(self) -> str | None:
        for code in self.documents():
            if type_of(code).offer and self.approved(code) and not self.voided(code):
                return code
        return None

    def approved_of_type(self, name: str) -> str | None:
        for code in self.documents():
            if type_of(code).name == name and self.approved(code) and not self.voided(code):
                return code
        return None

    def paid(self, invoice: str) -> int:
        return sum(int(record["amount"]) for record in self.of("payment", invoice))


def versions(directory: Path) -> list[int]:
    found = [int(match.group(1)) for path in directory.glob("v*.toml") if (match := re.match(r"^v(\d+)\.toml$", path.name))]
    return sorted(found)


def latest_version(document_dir: Path) -> int:
    found = versions(document_dir)
    if not found:
        raise PakemError(f"{document_dir.name} has no version file")
    return found[-1]


def load_content(document_dir: Path, version: int) -> dict:
    return load_toml(document_dir / f"v{version}.toml")


# ---------- skeletons ----------

def skeleton(doc_type: DocType, code: str, version: int, lang: str) -> str:
    lines = [
        f"# {code} v{version} ({doc_type.name}). Fill every {FILL_TEXT} from the owner's answers, never by guessing.",
        f"# Dates are YYYY-MM-DD, amounts are whole numbers. Fixed wording comes from wording/{doc_type.name}.{lang}.md.",
        "",
    ]
    tables = []
    for name, spec in doc_type.fields.items():
        prefix = "" if spec.required else "# optional: "
        if spec.kind == ROWS:
            row = [f"{prefix}[[{name}]]"]
            for column, column_kind in spec.columns:
                value = '"[FILL]"' if column_kind == TEXT else "0"
                row.append(f"{prefix}{column} = {value}")
            tables.append("\n".join(row))
        elif spec.kind == LIST:
            lines.append(f'{prefix}{name} = ["{FILL_TEXT}"]')
        elif spec.kind == AMOUNT:
            lines.append(f'{prefix}{name} = "{FILL_TEXT}"')
        else:
            lines.append(f'{prefix}{name} = "{FILL_TEXT}"')
    return "\n".join(lines) + "\n\n" + "\n\n".join(tables) + ("\n" if tables else "")


# ---------- checks ----------

def _strings(value: object):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def check_fields(doc_type: DocType, content: dict, report: Report) -> None:
    for name, spec in doc_type.fields.items():
        value = content.get(name)
        if value is None:
            if spec.required:
                report.error(f"missing field {name!r}")
            continue
        if spec.kind in (TEXT, DATE) and (not isinstance(value, str) or not value.strip()):
            report.error(f"{name!r} must be non-empty text")
        elif spec.kind == DATE and not PLACEHOLDER.search(value):
            try:
                parse_date(value)
            except PakemError as exc:
                report.error(f"{name!r}: {exc}")
        elif spec.kind == AMOUNT and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            report.error(f"{name!r} must be a whole, non-negative number")
        elif spec.kind == LIST and (not isinstance(value, list) or not value
                                    or not all(isinstance(item, str) and item.strip() for item in value)):
            report.error(f"{name!r} must be a non-empty list of text")
        elif spec.kind == ROWS:
            if not isinstance(value, list) or not value or not all(isinstance(row, dict) for row in value):
                report.error(f"{name!r} must hold at least one [[{name}]] row")
                continue
            for number, row in enumerate(value, start=1):
                for column, column_kind in spec.columns:
                    cell = row.get(column)
                    if column_kind == AMOUNT and (isinstance(cell, bool) or not isinstance(cell, int) or cell < 0):
                        report.error(f"{name}[{number}].{column} must be a whole, non-negative number")
                    elif column_kind == "number" and (isinstance(cell, bool) or not isinstance(cell, (int, float))):
                        report.error(f"{name}[{number}].{column} must be a number")
                    elif column_kind == TEXT and not isinstance(cell, str):
                        report.error(f"{name}[{number}].{column} must be text")
    for text in _strings(content):
        found = PLACEHOLDER.search(text)
        if found:
            report.error(f"unfilled value {found.group(0)} in {text[:60]!r}")
            break


def check_totals(doc_type: DocType, content: dict, engagement: Engagement, code: str, report: Report) -> None:
    if doc_type.name == "proposal" and isinstance(content.get("fee"), int):
        payments = [row.get("payment") for row in content.get("milestones", []) if isinstance(row, dict)]
        if all(isinstance(value, int) for value in payments) and sum(payments) != content["fee"]:
            report.error(f"milestone payments add up to {sum(payments)}, not the fee {content['fee']}")
    if doc_type.name == "invoice":
        amounts = [row.get("amount") for row in content.get("items", []) if isinstance(row, dict)]
        if all(isinstance(value, int) for value in amounts) and sum(amounts) <= 0:
            report.error("the invoice total must be more than zero")
    if doc_type.name == "receipt" and isinstance(content.get("amount"), int):
        invoice = content.get("invoice", "")
        if invoice not in engagement.documents() or type_of(invoice).name != "invoice":
            report.error(f"receipt names invoice {invoice!r}, which is not an invoice of this engagement")
            return
        receipted = 0
        for other in engagement.documents():
            if other == code or type_of(other).name != "receipt" or engagement.voided(other):
                continue
            other_content = load_content(engagement.dir / other, latest_version(engagement.dir / other))
            if other_content.get("invoice") == invoice and isinstance(other_content.get("amount"), int):
                receipted += other_content["amount"]
        unreceipted = engagement.paid(invoice) - receipted
        if content["amount"] > unreceipted:
            report.error(f"receipt amount {content['amount']} is more than the recorded, not yet receipted "
                         f"payments on {invoice} ({unreceipted}). Record the payment first (`pakem docs pay`)")


def check_rendered_integrity(engagement: Engagement, code: str, report: Report) -> None:
    for record in engagement.of("render", code):
        path = engagement.dir / code / f"v{record['version']}.toml"
        if path.is_file() and sha256_file(path) != record["sha256"]:
            report.error(f"v{record['version']} changed after it was rendered. Revise instead (`pakem docs revise {code}`)")


def wording_values(engagement: Engagement, issuer: dict, doc_type: DocType, code: str, content: dict) -> dict[str, str]:
    lang = engagement.lang
    currency = engagement.data.get("currency", "IDR")
    values = {
        "client": engagement.data.get("client", ""),
        "project": engagement.data.get("project", ""),
        "issuer": issuer.get("name", ""),
        "issuer_address": issuer.get("address", ""),
        "code": code,
        "offer": engagement.approved_offer() or "",
        "final": engagement.approved_of_type("final") or "",
    }
    for key, value in engagement.data.get("facts", {}).items():
        values[key] = format_number(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else str(value)
    for name, spec in doc_type.fields.items():
        value = content.get(name)
        if spec.kind == DATE and isinstance(value, str):
            try:
                values[name] = format_date(value, lang)
                values[f"{name}_day"] = weekday(value, lang)
            except PakemError:
                # check_fields reports the malformed date, so rendering never gets here with it.
                values[name] = value
        elif spec.kind == AMOUNT and isinstance(value, int):
            values[name] = format_money(value, currency, lang)
            values[f"{name}_words"] = amount_in_words(value, lang)
        elif spec.kind == TEXT and isinstance(value, str):
            values[name] = value
    return values


def load_wording(root: Path, doc_type: DocType, lang: str, report: Report) -> dict[str, str]:
    if not doc_type.wording:
        return {}
    path = root / "wording" / f"{doc_type.name}.{lang}.md"
    if not path.is_file():
        report.error(f"no approved wording for {doc_type.name} in {lang}: add {display_path(path)}")
        return {}
    sections = parse_wording(read_text(path))
    for key in doc_type.wording:
        if not sections.get(key):
            report.error(f"{display_path(path)} has no `## {key}` section")
    return sections


def check_document(root: Path, engagement: Engagement, code: str) -> tuple[Report, dict, dict[str, str], dict]:
    report = Report()
    doc_type = type_of(code)
    document_dir = engagement.dir / code
    content = load_content(document_dir, latest_version(document_dir))
    check_fields(doc_type, content, report)
    check_totals(doc_type, content, engagement, code, report)
    check_rendered_integrity(engagement, code, report)
    if engagement.voided(code):
        report.error(f"{code} is void")
    config = load_toml(root / "config.toml")
    issuer = config.get("issuers", {}).get(engagement.data.get("issuer", ""))
    if issuer is None:
        report.error(f"engagement issuer {engagement.data.get('issuer')!r} is not in config.toml [issuers]")
        issuer = {}
    needed = ("name", "bank", "account_number", "account_holder") if doc_type.name == "invoice" else ("name",)
    for key in needed:
        if not str(issuer.get(key, "")).strip() or PLACEHOLDER.search(str(issuer.get(key, ""))):
            report.error(f"config.toml issuer {engagement.data.get('issuer')!r} has no {key}")
    for text in _strings(engagement.data):
        if PLACEHOLDER.search(text):
            report.error(f"engagement.toml has an unfilled value: {text[:60]!r}")
            break
    sections = load_wording(root, doc_type, engagement.lang, report)
    values = wording_values(engagement, issuer, doc_type, code, content)
    for key in doc_type.wording:
        missing = sorted(wording_names(sections.get(key, "")) - set(values))
        if missing:
            report.error(f"wording `## {key}` needs {', '.join(missing)}: add them to engagement.toml [facts]")
    filled = {key: wording_html(fill(text, values)) for key, text in sections.items()
              if not (wording_names(text) - set(values))}
    return report, content, filled, issuer


# ---------- page bodies ----------

class Numbered:
    def __init__(self) -> None:
        self.number = 0

    def __call__(self, text: str) -> str:
        self.number += 1
        return f"{self.number}. {text}"


def body_html(engagement: Engagement, code: str, content: dict, wording: dict[str, str], issuer: dict) -> str:
    doc_type = type_of(code)
    lang = engagement.lang
    currency = engagement.data.get("currency", "IDR")

    def L(key: str) -> str:
        return label(lang, key)

    def money(value: int) -> str:
        return format_money(value, currency, lang)

    def day(raw: str) -> str:
        return format_date(raw, lang)

    client = engagement.data.get("client", "")
    issuer_name = issuer.get("name", "")
    n = Numbered()
    parts: list[str] = []
    name = doc_type.name
    if name == "proposal":
        parts.append(facts_block([(L("client"), client), (L("issuer"), issuer_name), (L("fee"), money(content["fee"])),
                                  (L("timeline"), content["timeline"]), (L("valid_until"), day(content["valid_until"]))]))
        parts.append(section(n(L("summary")), f"<p>{e(content['summary'])}</p>"))
        for key in ("scope", "out_of_scope", "assumptions", "prerequisites"):
            parts.append(section(n(L(key)), list_html(content[key])))
        rows = [[row["name"], row["delivered"], row["date"], money(row["payment"]) if row["payment"] else "—"]
                for row in content["milestones"]]
        parts.append(table(n(L("milestones")), [L("milestone"), L("delivered"), L("date"), L("payment")], rows, {3},
                           [L("total"), "", "", money(content["fee"])]))
        parts.append(section(n(L("acceptance")), wording["acceptance"]))
        parts.append(section(n(L("deliverables")), list_html(content["deliverables"])))
        parts.append(section(n(L("terms")), wording["terms"]))
        parts.append(section(n(L("agreement")), wording["agreement"]))
        parts.append(signatures([(client, engagement.data.get("client_representative", "")), (L("issuer"), issuer_name)], None))
    elif name == "quotation":
        parts.append(facts_block([(L("to"), client), (L("work"), content["work"]), (L("date"), day(content["date"])),
                                  (L("valid_until"), day(content["valid_until"]))]))
        rows = [[row["item"], format_number(row["qty"]), money(row["price"]), money(row["qty"] * row["price"])]
                for row in content["items"]]
        total = sum(row["qty"] * row["price"] for row in content["items"])
        parts.append(table(n(L("items")), [L("item"), L("qty"), L("unit_price"), L("subtotal")], rows, {1, 2, 3},
                           [L("total"), "", "", money(total)]))
        if content.get("terms"):
            parts.append(section(n(L("terms")), list_html(content["terms"])))
        parts.append(f'<div class="block">{wording["note"]}</div>')
        parts.append(signatures([(L("approved_by_client"), engagement.data.get("client_representative", client))], None))
    elif name == "brief":
        parts.append(facts_block([(L("client"), client), (L("source"), content["source"]), (L("date"), day(content["date"]))]))
        parts.append(section(n(L("background")), f"<p>{e(content['background'])}</p>"))
        parts.append(section(n(L("needs")), list_html(content["needs"])))
        if content.get("questions"):
            parts.append(section(n(L("questions")), list_html(content["questions"])))
        parts.append(f'<div class="block note">{wording["note"]}</div>')
    elif name in ("nda", "agreement"):
        parts.append(f'<div class="block">{wording["clauses"]}</div>')
        parts.append(signatures([(client, engagement.data.get("client_representative", "")), (L("issuer"), issuer_name)],
                                L("stamp")))
    elif name == "change":
        parts.append(facts_block([(L("offer"), engagement.approved_offer() or ""), (L("requested_by"), content["requested_by"]),
                                  (L("request_date"), day(content["date"])), (L("category"), content["category"])]))
        parts.append(section(n(L("change")), f"<p>{e(content['change'])}</p>"))
        parts.append(table(n(L("impact")), [L("area"), L("impact")],
                           [[L("cost"), content["cost"]], [L("timeline_impact"), content["timeline_impact"]],
                            [L("other_scope"), content["other_scope"]]]))
        parts.append(f'<div class="block">{wording["note"]}</div>')
        parts.append(signatures([(L("approved_by_client"), engagement.data.get("client_representative", client)),
                                 (L("issuer"), issuer_name)], None))
    elif name == "milestone":
        parts.append(facts_block([(L("milestone"), content["milestone"]), (L("test_window"), content["test_window"]),
                                  (L("environment"), content["environment"])]))
        parts.append(table(n(L("scenarios")), [L("scenario"), L("result"), L("notes")],
                           [[row["scenario"], row["result"], row["notes"]] for row in content["scenarios"]]))
        parts.append(f'<div class="block">{wording["note"]}</div>')
        parts.append(signatures([(L("accepted_by_client"), engagement.data.get("client_representative", client)),
                                 (L("issuer"), issuer_name)], None))
    elif name == "minutes":
        parts.append(facts_block([(L("date"), content["date"]), (L("where_held"), content["place"]),
                                  (L("attendees"), content["attendees"])]))
        parts.append(section(n(L("agenda")), list_html(content["agenda"])))
        parts.append(table(n(L("decisions")), [L("action"), L("owner"), L("due")],
                           [[row["action"], row["owner"], row["due"]] for row in content["decisions"]]))
    elif name == "invoice":
        total = sum(row["amount"] for row in content["items"])
        paid = engagement.paid(code)
        status = L("paid") if paid >= total else (L("partly_paid") if paid else L("unpaid"))
        rows = [(L("billed_to"), client), (L("invoice_date"), day(content["date"])), (L("due"), day(content["due"]))]
        if engagement.approved_offer():
            rows.append((L("offer"), engagement.approved_offer()))
        rows.append((L("status"), status))
        parts.append(facts_block(rows))
        parts.append(table(n(L("payment_details")), [L("no"), L("description"), L("amount")],
                           [[str(index), row["description"], money(row["amount"])]
                            for index, row in enumerate(content["items"], start=1)], {2},
                           ["", L("total_due"), money(total)]))
        parts.append(facts_block([(L("bank"), issuer.get("bank", "")), (L("account_number"), issuer.get("account_number", "")),
                                  (L("account_holder"), issuer.get("account_holder", ""))], n(L("payment_instructions"))))
        parts.append(f'<div class="block note">{wording["payment_note"]}</div>')
        parts.append(section(n(L("payment_terms")), wording["payment_terms"]))
    elif name == "receipt":
        parts.append(facts_block([(L("received_from"), client), (L("for_payment"), f"{content['description']} ({content['invoice']})"),
                                  (L("date_received"), day(content["date"])), (L("method"), content["method"])]))
        parts.append(f'<div class="amount"><span>{e(L("amount"))}</span><span>{e(money(content["amount"]))}</span></div>')
        parts.append(f'<div class="block">{wording["statement"]}</div>')
        needs_stamp = currency == "IDR" and content["amount"] > STAMP_THRESHOLD_IDR
        city = issuer.get("city", "")
        parts.append(signatures([(f"{city}, {day(content['date'])} · {L('received_by')}", issuer_name)],
                                L("stamp") if needs_stamp else None))
    elif name == "final":
        offer = engagement.approved_offer() or ""
        approval = engagement.approved(offer) if offer else None
        when = f", {day(approval['date'])}" if approval and approval.get("date") else ""
        parts.append(facts_block([(L("client"), client), (L("offer"), offer + when), (L("period"), content["period"])]))
        parts.append(section(n(L("summary")), f"<p>{e(content['summary'])}</p>"))
        parts.append(table(n(L("results")), [L("item"), L("result"), L("status")],
                           [[row["item"], row["result"], row["status"]] for row in content["results"]]))
        if content.get("adjustments"):
            parts.append(section(n(L("adjustments")), list_html(content["adjustments"])))
        if content.get("added_scope"):
            parts.append(table(n(L("added_scope")), [L("item"), L("price")],
                               [[row["item"], money(row["price"])] for row in content["added_scope"]], {1}))
        parts.append(table(n(L("log")), [L("date"), L("summary_short")], [[row["date"], row["summary"]] for row in content["log"]]))
        parts.append(table(n(L("deliverables")), [L("item"), L("where")],
                           [[row["item"], row["where"]] for row in content["deliverables"]]))
        parts.append(section(n(L("terms")), wording["terms"]))
        parts.append(section(n(L("agreement")), wording["agreement"]))
        parts.append(signatures([(client, engagement.data.get("client_representative", "")), (L("issuer"), issuer_name)], None))
    elif name == "bast":
        parts.append(f'<div class="block">{wording["statement"]}</div>')
        parts.append(table(n(L("handed_over")), [L("no"), L("item"), L("notes")],
                           [[str(index), row["item"], row["notes"]] for index, row in enumerate(content["items"], start=1)]))
        parts.append(signatures([(L("first_party"), issuer_name), (L("second_party"), content["client_representative"])],
                                L("stamp")))
    elif name == "warranty":
        parts.append(facts_block([(L("project"), engagement.data.get("project", "")),
                                  (L("warranty"), str(engagement.data.get("facts", {}).get("warranty", ""))),
                                  (L("channel"), content["channel"])]))
        parts.append(f'<div class="block">{wording["terms"]}</div>')
        parts.append(table(n(L("response_times")), [L("severity"), L("example"), L("response")],
                           [[row["severity"], row["example"], row["response"]] for row in content["response"]]))
    elif name == "workreport":
        parts.append(facts_block([(L("client"), client), (L("role"), content["role"]), (L("period"), content["period"])]))
        hours = sum(row["hours"] for row in content["entries"])
        parts.append(table(n(L("entries")), [L("date"), L("work_done"), L("hours")],
                           [[row["date"], row["work"], format_number(row["hours"])] for row in content["entries"]], {2},
                           ["", L("total_hours"), format_number(hours)]))
        parts.append(f'<div class="block">{wording["note"]}</div>')
        parts.append(signatures([(L("approved"), engagement.data.get("client_representative", client))], None))
    if len(parts) > 1 and parts[-1].startswith('<div class="sign"'):
        # The closing text and its signatures stay on one page.
        parts[-2:] = [f'<div class="keep">{parts[-2]}{parts[-1]}</div>']
    return "\n".join(parts)


# ---------- commands ----------

def emit(args, result: dict, lines: list[str]) -> int:
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        for line in lines:
            print(line)
    return 0


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40].strip("-") or "client"


def run_init(args) -> int:
    store = expand_user_path(args.store)
    match = PROJECT_CODE.match(args.last_project)
    if not match:
        raise PakemError(f"--last-project must look like PRJ-YYYY-NNN, not {args.last_project!r}")
    if inside_git(store):
        raise CommandRefused(f"{display_path(store)} is inside a git work tree. Records must stay outside every repository")
    if settings_path().is_file():
        current = expand_user_path(str(load_toml(settings_path()).get("root", "")))
        if current != store:
            raise CommandRefused(f"a documents store already exists at {display_path(current)}")
    if (store / "config.toml").is_file():
        raise CommandRefused(f"{display_path(store)} already holds a documents store")
    steps = [f"create {display_path(store)} with config.toml and wording/",
             f"numbering continues after {args.last_project}",
             f"record the store in {display_path(settings_path())}"]
    if args.plan:
        return emit(args, {"status": "plan", "steps": steps}, ["Plan only:"] + [f"  {step}" for step in steps])
    (store / "wording").mkdir(parents=True, exist_ok=True)
    issuer = "\n".join(f'{key} = "{FILL_TEXT}"' for key in ISSUER_FIELDS)
    write_text_atomically(store / "config.toml", (
        "# pakem docs store. Issuers are the owner's facts: fill every [FILL] from the owner.\n"
        "[numbering]\n"
        f"year = {int(match.group(1))}\n"
        f"last = {int(match.group(2))}\n\n"
        f"[issuers.{args.issuer}]\n{issuer}\n"))
    write_text_atomically(settings_path(), (
        "# Written by pakem docs init.\n"
        f"root = {toml_string(str(store))}\n"
        "# browser = \"/path/to/a/chromium-family/browser\"  (optional, found automatically)\n"))
    return emit(args, {"status": "done", "store": str(store)}, [f"Created the documents store at {display_path(store)}.",
                "Next: fill config.toml [issuers], add the approved wording files under wording/."])


def allocate_project(root: Path) -> str:
    config_path = root / "config.toml"
    text = read_text(config_path)
    numbering = load_toml(config_path).get("numbering", {})
    year = date.today().year
    last = int(numbering.get("last", 0)) if int(numbering.get("year", year)) == year else 0
    code = f"PRJ-{year}-{last + 1:03d}"
    text = re.sub(r"(?m)^year = \d+$", f"year = {year}", text, count=1)
    text = re.sub(r"(?m)^last = \d+$", f"last = {last + 1}", text, count=1)
    write_text_atomically(config_path, text)
    return code


def run_engagement_new(args) -> int:
    root = store_root()
    config = load_toml(root / "config.toml")
    issuers = config.get("issuers", {})
    issuer = args.issuer or (next(iter(issuers)) if len(issuers) == 1 else None)
    if issuer not in issuers:
        raise PakemError(f"name the issuer with --issuer (config.toml has {', '.join(issuers) or 'none'})")
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: allocate the next project number for {args.client}, "
                                               f"a {args.kind} engagement in {args.lang}, issued by {issuer}."])
    code = allocate_project(root)
    directory = root / f"{code}-{slugify(args.client)}"
    directory.mkdir()
    facts_hint = {FIXED: "uat_days, warranty, approval_method", WORKSTREAM: "none yet"}[args.kind]
    write_text_atomically(directory / "engagement.toml", (
        f"# {code}. The owner's facts for this engagement. Ask for anything missing, never guess.\n"
        f"code = {toml_string(code)}\n"
        f"client = {toml_string(args.client)}\n"
        f"client_representative = {toml_string(args.representative or FILL_TEXT)}\n"
        f"project = {toml_string(args.name)}\n"
        f"kind = {toml_string(args.kind)}\n"
        f"language = {toml_string(args.lang)}\n"
        f"currency = {toml_string(args.currency)}\n"
        f"issuer = {toml_string(issuer)}\n\n"
        f"[facts]\n# Values the wording uses, for example {facts_hint}.\n"))
    engagement_state = {"stage": STAGES[args.kind][0], "counters": {}}
    write_text_atomically(directory / "state.toml",
                          f"# Written by pakem docs. Do not edit by hand.\nstage = {toml_string(engagement_state['stage'])}\n\n[counters]\n")
    Engagement(root, code).add_record("created", client=args.client, project=args.name)
    return emit(args, {"status": "done", "code": code, "dir": str(directory)},
                [f"Created engagement {code} at {display_path(directory)}."])


def run_new(args) -> int:
    root = store_root()
    if args.type not in TYPES:
        raise PakemError(f"unknown document type {args.type!r}. Known: {', '.join(TYPES)}")
    doc_type = TYPES[args.type]
    engagement = Engagement(root, args.project)
    if engagement.stage == "closed":
        raise CommandRefused(f"{engagement.code} is closed")
    if engagement.kind not in doc_type.kinds:
        raise CommandRefused(f"{engagement.code} is a {engagement.kind} engagement, which has no {doc_type.name} documents")
    if engagement.kind == FIXED and doc_type.after_offer and not engagement.approved_offer():
        raise CommandRefused(f"{engagement.code} needs an approved proposal or quotation before any {doc_type.name} (`pakem docs approve`)")
    if doc_type.offer and engagement.approved_offer():
        raise CommandRefused(f"{engagement.approved_offer()} is already approved. Changes go in a change request")
    _, year, number = engagement.code.split("-")
    code = engagement.code if doc_type.name == "brief" else f"{doc_type.prefix}-{year}-{number}"
    counters = engagement.state.setdefault("counters", {})
    if doc_type.repeating:
        count = int(counters.get(doc_type.prefix, 0)) + 1
        code = f"{code}-{count:02d}"
    if (engagement.dir / code).exists():
        raise CommandRefused(f"{code} exists already. Revise it with `pakem docs revise {code}`")
    if args.plan:
        return emit(args, {"status": "plan", "code": code}, [f"Plan only: create {code} v1 in {display_path(engagement.dir)}."])
    if doc_type.repeating:
        counters[doc_type.prefix] = count
    if doc_type.offer and engagement.stage == "discovery":
        engagement.state["stage"] = "proposal"
        engagement.add_record("stage", stage="proposal")
    engagement.save_state()
    write_text_atomically(engagement.dir / code / "v1.toml", skeleton(doc_type, code, 1, engagement.lang))
    engagement.add_record("new", document=code)
    return emit(args, {"status": "done", "code": code, "file": str(engagement.dir / code / "v1.toml")},
                [f"Created {code}. Fill {display_path(engagement.dir / code / 'v1.toml')}."])


def _engagement_for(root: Path, code: str) -> Engagement:
    engagement = Engagement(root, project_of(code) if not PROJECT_CODE.match(code) else code)
    if code not in engagement.documents():
        raise PakemError(f"no document {code} in {engagement.code}")
    return engagement


def run_revise(args) -> int:
    root = store_root()
    engagement = _engagement_for(root, args.code)
    if engagement.approved(args.code):
        raise CommandRefused(f"{args.code} is approved and frozen. Changes go in a change request")
    if engagement.voided(args.code):
        raise CommandRefused(f"{args.code} is void")
    document_dir = engagement.dir / args.code
    version = latest_version(document_dir)
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: copy {args.code} v{version} to v{version + 1}."])
    shutil.copyfile(document_dir / f"v{version}.toml", document_dir / f"v{version + 1}.toml")
    engagement.add_record("revise", document=args.code, version=version + 1)
    return emit(args, {"status": "done", "version": version + 1},
                [f"{args.code} v{version + 1} is a copy of v{version}. Edit it, then check and render."])


def run_check(args) -> int:
    root = store_root()
    engagement = _engagement_for(root, args.code)
    report, _, _, _ = check_document(root, engagement, args.code)
    if args.json:
        print(json.dumps({"status": "failed" if report.errors else "ok", "errors": report.errors}, indent=2))
    else:
        report.print()
    return EXIT_CHECK_FAILED if report.errors else 0


def run_render(args) -> int:
    root = store_root()
    engagement = _engagement_for(root, args.code)
    report, content, wording, issuer = check_document(root, engagement, args.code)
    if report.errors:
        report.print()
        raise CheckFailed(f"{args.code} is not ready to render. Fix the errors above")
    document_dir = engagement.dir / args.code
    version = latest_version(document_dir)
    browser = find_browser(store_settings().get("browser"))
    if browser is None:
        raise OwnerActionRequired("OWNER ACTION REQUIRED: run `pakem docs browser` (it downloads Google's "
                                  "chrome-headless-shell after your confirmation), or name a browser as `browser` in "
                                  + display_path(settings_path()))
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: render {args.code} v{version} to HTML and PDF with {browser}."])
    doc_type = type_of(args.code)
    lang = engagement.lang
    when = content.get("date") if doc_type.fields.get("date") and doc_type.fields["date"].kind == DATE else None
    shown_date = format_date(when, lang) if when else format_date(date.today().isoformat(), lang)
    html_text = page(lang, doc_type.title[lang], engagement.data.get("project", ""), issuer.get("name", ""),
                     args.code, shown_date, body_html(engagement, args.code, content, wording, issuer))
    html_path = document_dir / f"v{version}.html"
    pdf_path = document_dir / f"v{version}.pdf"
    write_text_atomically(html_path, html_text)
    print_pdf(browser, html_path, pdf_path)
    digest = sha256_file(document_dir / f"v{version}.toml")
    if not any(record.get("version") == version for record in engagement.of("render", args.code)):
        engagement.add_record("render", document=args.code, version=version, sha256=digest)
    return emit(args, {"status": "done", "pdf": str(pdf_path), "html": str(html_path)},
                [f"Rendered {args.code} v{version}: {display_path(pdf_path)}"])


def _rendered_latest(engagement: Engagement, code: str) -> int:
    version = latest_version(engagement.dir / code)
    if not any(record.get("version") == version for record in engagement.of("render", code)):
        raise CommandRefused(f"{code} v{version} has not been rendered. Render it before recording this")
    return version


def run_approve(args) -> int:
    root = store_root()
    engagement = _engagement_for(root, args.code)
    doc_type = type_of(args.code)
    if not doc_type.approvable:
        raise CommandRefused(f"{args.code} is issued, not approved")
    if engagement.approved(args.code):
        raise CommandRefused(f"{args.code} is already approved")
    if engagement.voided(args.code):
        raise CommandRefused(f"{args.code} is void")
    report, _, _, _ = check_document(root, engagement, args.code)
    if report.errors:
        report.print()
        raise CheckFailed(f"{args.code} does not pass its checks")
    version = _rendered_latest(engagement, args.code)
    parse_date(args.date)
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: record the approval of {args.code} v{version} by {args.by}."])
    engagement.add_record("approval", document=args.code, version=version, by=args.by, role=args.role,
                          method=args.method, evidence=args.evidence, date=args.date)
    lines = [f"Recorded: {args.code} v{version} approved by {args.by} on {args.date} ({args.method})."]
    if doc_type.offer and engagement.kind == FIXED:
        engagement.set_stage("development")
        lines.append(f"{engagement.code} is now in development.")
    elif doc_type.name == "final":
        engagement.set_stage("finishing")
        lines.append(f"{engagement.code} is now finishing.")
    elif doc_type.name == "bast":
        engagement.add_record("handover", document=args.code, date=args.date, to=args.by, evidence=args.evidence)
        lines.append("Handover recorded through the signed BAST.")
    return emit(args, {"status": "done"}, lines)


def run_pay(args) -> int:
    root = store_root()
    engagement = _engagement_for(root, args.code)
    if type_of(args.code).name != "invoice":
        raise CommandRefused(f"{args.code} is not an invoice")
    if engagement.voided(args.code):
        raise CommandRefused(f"{args.code} is void")
    _rendered_latest(engagement, args.code)
    content = load_content(engagement.dir / args.code, latest_version(engagement.dir / args.code))
    total = sum(row["amount"] for row in content.get("items", []))
    outstanding = total - engagement.paid(args.code)
    if args.amount <= 0 or args.amount > outstanding:
        raise CommandRefused(f"the payment must be between 1 and the outstanding {outstanding}")
    parse_date(args.date)
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: record a payment of {args.amount} on {args.code}."])
    engagement.add_record("payment", document=args.code, amount=args.amount, date=args.date, method=args.method,
                          evidence=args.evidence)
    left = outstanding - args.amount
    return emit(args, {"status": "done", "outstanding": left},
                [f"Recorded a payment of {args.amount} on {args.code}. Outstanding: {left}."])


def run_handover(args) -> int:
    root = store_root()
    engagement = Engagement(root, args.project)
    parse_date(args.date)
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: record the handover of {engagement.code} to {args.to}."])
    engagement.add_record("handover", date=args.date, to=args.to, evidence=args.evidence)
    return emit(args, {"status": "done"}, [f"Recorded the handover of {engagement.code} to {args.to} on {args.date}."])


def run_void(args) -> int:
    root = store_root()
    engagement = _engagement_for(root, args.code)
    if engagement.approved(args.code):
        raise CommandRefused(f"{args.code} is approved. An approved document is never voided")
    if engagement.paid(args.code):
        raise CommandRefused(f"{args.code} has recorded payments")
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: void {args.code}. Its number is never reused."])
    engagement.add_record("void", document=args.code, reason=args.reason)
    return emit(args, {"status": "done"}, [f"{args.code} is void. Its number stays used."])


def close_blockers(engagement: Engagement) -> list[str]:
    blockers = []
    for code in engagement.documents():
        if type_of(code).name != "invoice" or engagement.voided(code):
            continue
        content = load_content(engagement.dir / code, latest_version(engagement.dir / code))
        total = sum(row.get("amount", 0) for row in content.get("items", []) if isinstance(row.get("amount"), int))
        if engagement.paid(code) < total:
            blockers.append(f"{code} has {total - engagement.paid(code)} outstanding")
    if engagement.kind == FIXED:
        offer = engagement.approved_offer()
        if not offer:
            blockers.append("No approved proposal or quotation")
        elif type_of(offer).name == "proposal" and not engagement.approved_of_type("final"):
            blockers.append("No approved final report")
        if not engagement.of("handover"):
            blockers.append("No recorded handover (a signed BAST, or `pakem docs handover`)")
    return blockers


def run_close(args) -> int:
    root = store_root()
    engagement = Engagement(root, args.project)
    if engagement.stage == "closed":
        raise CommandRefused(f"{engagement.code} is already closed")
    blockers = close_blockers(engagement)
    if blockers:
        raise CommandRefused(f"{engagement.code} cannot close yet: " + ". ".join(blockers))
    if args.plan:
        return emit(args, {"status": "plan"}, [f"Plan only: close {engagement.code}."])
    engagement.set_stage("closed")
    return emit(args, {"status": "done"}, [f"{engagement.code} is closed."])


def document_line(engagement: Engagement, code: str) -> dict:
    version = latest_version(engagement.dir / code)
    rendered = any(record.get("version") == version for record in engagement.of("render", code))
    state = "void" if engagement.voided(code) else "approved" if engagement.approved(code) else (
        "rendered" if rendered else "draft")
    line = {"code": code, "type": type_of(code).name, "version": version, "state": state}
    if type_of(code).name == "invoice" and state != "void":
        content = load_content(engagement.dir / code, version)
        total = sum(row.get("amount", 0) for row in content.get("items", []) if isinstance(row.get("amount"), int))
        line["outstanding"] = total - engagement.paid(code)
    return line


def run_status(args) -> int:
    root = store_root()
    if not args.project:
        engagements = []
        for directory in sorted(path for path in root.iterdir() if path.is_dir() and (path / "engagement.toml").is_file()):
            engagement = Engagement(root, directory.name[:12])
            engagements.append({"code": engagement.code, "client": engagement.data.get("client", ""),
                                "project": engagement.data.get("project", ""), "stage": engagement.stage})
        lines = [f"{item['code']}  {item['stage']:<12} {item['client']} · {item['project']}" for item in engagements]
        return emit(args, {"engagements": engagements}, lines or ["No engagements yet."])
    engagement = Engagement(root, args.project)
    documents = [document_line(engagement, code) for code in engagement.documents()]
    blockers = close_blockers(engagement) if engagement.stage != "closed" else []
    lines = [f"{engagement.code} · {engagement.data.get('client', '')} · {engagement.data.get('project', '')}",
             f"Stage: {engagement.stage}"]
    for item in documents:
        extra = f", outstanding {item['outstanding']}" if "outstanding" in item else ""
        lines.append(f"  {item['code']:<18} {item['type']:<10} v{item['version']} {item['state']}{extra}")
    if blockers:
        lines.append("To close: " + ". ".join(blockers) + ".")
    return emit(args, {"code": engagement.code, "stage": engagement.stage, "documents": documents,
                       "close_blockers": blockers}, lines)


def run_browser(args) -> int:
    """The renderer is an install, so it needs the owner's go (kernel §2): --plan, then one confirmation."""
    version, url = headless_shell_download()
    destination = headless_shell_root() / version
    steps = [f"download chrome-headless-shell {version} from {url}",
             f"unpack it into {display_path(destination)} (used only by pakem docs)"]
    if args.plan:
        return emit(args, {"status": "plan", "steps": steps}, ["Plan only:"] + [f"  {step}" for step in steps])
    for step in steps:
        print(f"  {step}")
    confirm_steps(args.yes, "pakem docs browser")
    shell = install_headless_shell(version, url)
    with tempfile.TemporaryDirectory(prefix="pakem-docs-probe-") as directory:
        page_path = Path(directory) / "probe.html"
        page_path.write_text("<!doctype html><p>pakem docs</p>", encoding="utf-8")
        print_pdf(str(shell), page_path, Path(directory) / "probe.pdf")
    return emit(args, {"status": "done", "browser": str(shell)},
                [f"Installed and verified (it printed a test page): {display_path(shell)}"])


COMMANDS = {
    "init": run_init, "new": run_new, "revise": run_revise, "check": run_check, "render": run_render,
    "approve": run_approve, "pay": run_pay, "handover": run_handover, "void": run_void, "status": run_status,
    "close": run_close, "browser": run_browser,
}


def run_docs(args) -> int:
    if args.docs_command == "engagement":
        return run_engagement_new(args)
    return COMMANDS[args.docs_command](args)


def add_parser(commands) -> None:
    docs = commands.add_parser("docs", help="freelance engagement documents: write, check, render, record")
    sub = docs.add_subparsers(dest="docs_command", required=True)

    def common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        parser.add_argument("--plan", action="store_true", help="print what would happen. Change nothing")
        parser.add_argument("--json", action="store_true", help="machine-readable result")
        return parser

    init = common(sub.add_parser("init", help="create the documents store once (outside every repository)"))
    init.add_argument("--store", required=True, help="the private directory for all engagement records")
    init.add_argument("--last-project", required=True, help="your last issued project number, PRJ-YYYY-NNN")
    init.add_argument("--issuer", default="owner", help="key of the first issuer in config.toml (default: owner)")
    engagement = sub.add_parser("engagement", help="start an engagement")
    engagement_sub = engagement.add_subparsers(dest="engagement_command", required=True)
    new_engagement = common(engagement_sub.add_parser("new", help="allocate the next project number and its folder"))
    new_engagement.add_argument("--client", required=True)
    new_engagement.add_argument("--name", required=True, help="the project name shown on every document")
    new_engagement.add_argument("--kind", required=True, choices=(FIXED, WORKSTREAM))
    new_engagement.add_argument("--lang", required=True, choices=LANGUAGES)
    new_engagement.add_argument("--currency", required=True, choices=CURRENCIES)
    new_engagement.add_argument("--issuer", help="issuer key in config.toml (default: the only one)")
    new_engagement.add_argument("--representative", help="the client's representative who signs")
    new_document = common(sub.add_parser("new", help="allocate a document code and write its v1 to fill"))
    new_document.add_argument("type", choices=tuple(TYPES))
    new_document.add_argument("project", help="PRJ-YYYY-NNN")
    for name, text in (("revise", "copy the latest version to a new one (unapproved documents only)"),
                       ("check", "check a document's latest version"),
                       ("render", "write the latest version's HTML and PDF after its checks pass")):
        common(sub.add_parser(name, help=text)).add_argument("code")
    approve = common(sub.add_parser("approve", help="record the owner's statement that the client approved"))
    approve.add_argument("code")
    approve.add_argument("--by", required=True, help="who approved")
    approve.add_argument("--role", help="their role")
    approve.add_argument("--method", required=True, help="how: signed document, email, chat")
    approve.add_argument("--evidence", required=True, help="where the evidence is")
    approve.add_argument("--date", required=True, help="YYYY-MM-DD")
    pay = common(sub.add_parser("pay", help="record a payment the owner received on an invoice"))
    pay.add_argument("code")
    pay.add_argument("--amount", required=True, type=int)
    pay.add_argument("--date", required=True, help="YYYY-MM-DD")
    pay.add_argument("--method", required=True)
    pay.add_argument("--evidence", required=True)
    handover = common(sub.add_parser("handover", help="record a handover without a BAST"))
    handover.add_argument("project")
    handover.add_argument("--date", required=True, help="YYYY-MM-DD")
    handover.add_argument("--to", required=True, help="who received it")
    handover.add_argument("--evidence", required=True)
    void = common(sub.add_parser("void", help="void an unapproved, unpaid document. Its number stays used"))
    void.add_argument("code")
    void.add_argument("--reason", required=True)
    status = common(sub.add_parser("status", help="engagements, or one engagement's documents and what blocks closing"))
    status.add_argument("project", nargs="?")
    close = common(sub.add_parser("close", help="close an engagement once its guards hold"))
    close.add_argument("project")
    browser = common(sub.add_parser("browser", help="install the PDF renderer (chrome-headless-shell), after confirmation"))
    browser.add_argument("--yes", action="store_true", help="install without asking (after the owner agreed)")
