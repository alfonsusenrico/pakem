"""Freelance document types for `pakem docs` (ADR 0014): codes, fields, and page layout.

A type's fields are the content the agent writes into each version file. Fixed wording
never lives here: it comes from the store's `wording/<type>.<lang>.md` (owner-approved).
Labels and section titles are structure, so they stay in the public code.
"""

from __future__ import annotations

from dataclasses import dataclass

TEXT = "text"
LIST = "list"
DATE = "date"
AMOUNT = "amount"
ROWS = "rows"

FIXED = "fixed"
WORKSTREAM = "workstream"


@dataclass(frozen=True)
class Field:
    kind: str
    required: bool = True
    # ROWS only: column name -> its kind (TEXT, AMOUNT, or "number" for hours).
    columns: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class DocType:
    name: str
    prefix: str
    title: dict[str, str]
    fields: dict[str, Field]
    wording: tuple[str, ...] = ()
    repeating: bool = False
    # An offer (proposal or quotation), once approved, opens development.
    offer: bool = False
    approvable: bool = False
    # Needs an approved offer first (fixed engagements only).
    after_offer: bool = False
    kinds: tuple[str, ...] = (FIXED,)
    stamp: bool = False
    signers: tuple[str, ...] = ()


def _rows(*columns: tuple[str, str], required: bool = True) -> Field:
    return Field(ROWS, required, columns)


TYPES: dict[str, DocType] = {
    "brief": DocType(
        "brief", "PRJ", {"id": "RINGKASAN KEBUTUHAN", "en": "REQUIREMENTS BRIEF"},
        {"date": Field(DATE), "source": Field(TEXT), "background": Field(TEXT), "needs": Field(LIST),
         "questions": Field(LIST, required=False)},
        wording=("note",),
    ),
    "proposal": DocType(
        "proposal", "PROP", {"id": "PROPOSAL PROYEK", "en": "PROJECT PROPOSAL"},
        {"date": Field(DATE), "valid_until": Field(DATE), "timeline": Field(TEXT), "summary": Field(TEXT),
         "scope": Field(LIST), "out_of_scope": Field(LIST), "assumptions": Field(LIST),
         "prerequisites": Field(LIST), "fee": Field(AMOUNT),
         "milestones": _rows(("name", TEXT), ("delivered", TEXT), ("date", TEXT), ("payment", AMOUNT)),
         "deliverables": Field(LIST)},
        wording=("acceptance", "terms", "agreement"), offer=True, approvable=True,
        signers=("client", "issuer"),
    ),
    "quotation": DocType(
        "quotation", "QUO", {"id": "PENAWARAN HARGA", "en": "QUOTATION"},
        {"date": Field(DATE), "valid_until": Field(DATE), "work": Field(TEXT),
         "items": _rows(("item", TEXT), ("qty", AMOUNT), ("price", AMOUNT)), "terms": Field(LIST, required=False)},
        wording=("note",), offer=True, approvable=True, signers=("client",),
    ),
    "nda": DocType(
        "nda", "NDA", {"id": "PERJANJIAN KERAHASIAAN", "en": "NON-DISCLOSURE AGREEMENT"},
        {"date": Field(DATE), "purpose": Field(TEXT), "term": Field(TEXT)},
        wording=("clauses",), approvable=True, kinds=(FIXED, WORKSTREAM), stamp=True,
        signers=("client", "issuer"),
    ),
    "agreement": DocType(
        "agreement", "AGR", {"id": "PERJANJIAN KERJA", "en": "SERVICE AGREEMENT"},
        {"date": Field(DATE), "client_representative": Field(TEXT)},
        wording=("clauses",), approvable=True, after_offer=True, stamp=True, signers=("client", "issuer"),
    ),
    "change": DocType(
        "change", "CR", {"id": "PERMINTAAN PERUBAHAN", "en": "CHANGE REQUEST"},
        {"date": Field(DATE), "requested_by": Field(TEXT), "category": Field(TEXT), "change": Field(TEXT),
         "cost": Field(TEXT), "timeline_impact": Field(TEXT), "other_scope": Field(TEXT)},
        wording=("note",), repeating=True, approvable=True, after_offer=True, signers=("client", "issuer"),
    ),
    "milestone": DocType(
        "milestone", "MSR", {"id": "LAPORAN MILESTONE & PERSETUJUAN UAT", "en": "MILESTONE REPORT & UAT SIGN-OFF"},
        {"milestone": Field(TEXT), "test_window": Field(TEXT), "environment": Field(TEXT),
         "scenarios": _rows(("scenario", TEXT), ("result", TEXT), ("notes", TEXT))},
        wording=("note",), repeating=True, approvable=True, after_offer=True, signers=("client", "issuer"),
    ),
    "minutes": DocType(
        "minutes", "MOM", {"id": "NOTULEN RAPAT", "en": "MEETING MINUTES"},
        {"date": Field(TEXT), "place": Field(TEXT), "attendees": Field(TEXT), "agenda": Field(LIST),
         "decisions": _rows(("action", TEXT), ("owner", TEXT), ("due", TEXT))},
        repeating=True, kinds=(FIXED, WORKSTREAM),
    ),
    "invoice": DocType(
        "invoice", "INV", {"id": "INVOICE", "en": "INVOICE"},
        {"date": Field(DATE), "due": Field(DATE), "items": _rows(("description", TEXT), ("amount", AMOUNT))},
        wording=("payment_note", "payment_terms"), repeating=True, after_offer=True, kinds=(FIXED, WORKSTREAM),
    ),
    "receipt": DocType(
        "receipt", "KW", {"id": "KWITANSI", "en": "RECEIPT"},
        {"invoice": Field(TEXT), "date": Field(DATE), "method": Field(TEXT), "amount": Field(AMOUNT),
         "description": Field(TEXT)},
        wording=("statement",), repeating=True, kinds=(FIXED, WORKSTREAM), stamp=True, signers=("issuer",),
    ),
    "final": DocType(
        "final", "FINAL", {"id": "LAPORAN AKHIR PROYEK", "en": "FINAL PROJECT REPORT"},
        {"period": Field(TEXT), "summary": Field(TEXT),
         "results": _rows(("item", TEXT), ("result", TEXT), ("status", TEXT)),
         "adjustments": Field(LIST, required=False),
         "added_scope": _rows(("item", TEXT), ("price", AMOUNT), required=False),
         "log": _rows(("date", TEXT), ("summary", TEXT)),
         "deliverables": _rows(("item", TEXT), ("where", TEXT))},
        wording=("terms", "agreement"), approvable=True, after_offer=True, signers=("client", "issuer"),
    ),
    "bast": DocType(
        "bast", "BAST", {"id": "BERITA ACARA SERAH TERIMA", "en": "HANDOVER CERTIFICATE"},
        {"date": Field(DATE), "client_representative": Field(TEXT), "client_title": Field(TEXT),
         "items": _rows(("item", TEXT), ("notes", TEXT))},
        wording=("statement",), approvable=True, after_offer=True, stamp=True, signers=("issuer", "client"),
    ),
    "warranty": DocType(
        "warranty", "WAR", {"id": "KETENTUAN GARANSI & PEMELIHARAAN", "en": "WARRANTY & MAINTENANCE TERMS"},
        {"date": Field(DATE), "channel": Field(TEXT),
         "response": _rows(("severity", TEXT), ("example", TEXT), ("response", TEXT))},
        wording=("terms",), after_offer=True,
    ),
    "workreport": DocType(
        "workreport", "WR", {"id": "LAPORAN KERJA", "en": "WORK REPORT"},
        {"role": Field(TEXT), "period": Field(TEXT),
         "entries": _rows(("date", TEXT), ("work", TEXT), ("hours", "number"))},
        wording=("note",), repeating=True, approvable=True, kinds=(FIXED, WORKSTREAM), signers=("client",),
    ),
}

PREFIXES = {doc_type.prefix: doc_type for doc_type in TYPES.values()}

# Labels shown on the page, per language.
LABELS = {
    "id": {
        "client": "Klien", "issuer": "Pengembang", "fee": "Nilai proyek", "timeline": "Estimasi waktu",
        "valid_until": "Berlaku hingga", "date": "Tanggal", "summary": "Ringkasan Proyek", "scope": "Ruang Lingkup",
        "out_of_scope": "Di Luar Lingkup", "assumptions": "Asumsi", "prerequisites": "Kebutuhan dari Klien",
        "milestones": "Jadwal dan Pembayaran", "acceptance": "Penerimaan Hasil (UAT)", "deliverables": "Hasil Serahan",
        "terms": "Syarat dan Ketentuan", "agreement": "Persetujuan", "milestone": "Milestone", "delivered": "Hasil",
        "payment": "Pembayaran", "total": "Total", "billed_to": "Ditagihkan kepada", "invoice_date": "Tanggal invoice",
        "due": "Jatuh tempo", "offer": "Proposal acuan", "status": "Status", "unpaid": "Belum dibayar",
        "partly_paid": "Dibayar sebagian", "paid": "Lunas", "payment_details": "Rincian Tagihan", "no": "No",
        "description": "Deskripsi", "amount": "Jumlah", "total_due": "Total tagihan",
        "payment_instructions": "Instruksi Pembayaran", "bank": "Bank", "account_number": "Nomor rekening",
        "account_holder": "Atas nama", "payment_terms": "Ketentuan Pembayaran", "period": "Periode pengerjaan",
        "results": "Ruang Lingkup", "item": "Item", "result": "Hasil", "adjustments": "Penyesuaian Lingkup",
        "added_scope": "Tambahan Lingkup", "price": "Harga", "log": "Log Pengembangan", "where": "Diserahkan melalui",
        "summary_short": "Ringkasan", "source": "Disusun dari", "background": "Latar Belakang",
        "needs": "Kebutuhan Utama", "questions": "Pertanyaan Terbuka", "to": "Kepada", "work": "Pekerjaan",
        "items": "Rincian", "qty": "Jumlah", "unit_price": "Harga satuan", "subtotal": "Subtotal",
        "first_party": "Pihak Pertama", "second_party": "Pihak Kedua", "handed_over": "Yang Diserahkan",
        "notes": "Keterangan", "requested_by": "Diminta oleh", "request_date": "Tanggal permintaan",
        "category": "Kategori", "change": "Perubahan", "impact": "Dampak", "area": "Aspek", "cost": "Biaya",
        "timeline_impact": "Waktu", "other_scope": "Lingkup lain", "test_window": "Periode uji",
        "environment": "Lingkungan", "scenarios": "Skenario Uji", "scenario": "Skenario",
        "where_held": "Tempat", "attendees": "Peserta", "agenda": "Agenda",
        "decisions": "Keputusan dan Tindak Lanjut", "action": "Keputusan / tindakan", "owner": "Penanggung jawab",
        "role": "Peran", "entries": "Rincian", "work_done": "Pekerjaan", "hours": "Jam", "total_hours": "Total jam",
        "received_from": "Sudah terima dari", "for_payment": "Untuk pembayaran", "date_received": "Tanggal terima",
        "method": "Metode", "in_words": "Terbilang", "project": "Proyek", "warranty": "Garansi",
        "channel": "Saluran laporan", "response_times": "Waktu Respons", "severity": "Tingkat", "example": "Contoh",
        "response": "Respons", "page": "Halaman {page} dari {pages}", "name": "Nama", "signature": "Tanda tangan",
        "stamp": "Meterai Rp10.000", "approved_by_client": "Disetujui klien", "received_by": "Penerima",
        "accepted_by_client": "Diterima oleh klien", "approved": "Disetujui", "purpose": "Tujuan",
        "term": "Jangka waktu",
    },
    "en": {
        "client": "Client", "issuer": "Developer", "fee": "Project value", "timeline": "Estimated timeline",
        "valid_until": "Valid until", "date": "Date", "summary": "Project Summary", "scope": "Project Scope",
        "out_of_scope": "Out of Scope", "assumptions": "Assumptions", "prerequisites": "Client Prerequisites",
        "milestones": "Timeline and Payments", "acceptance": "Acceptance (UAT)", "deliverables": "Deliverables",
        "terms": "Terms and Conditions", "agreement": "Agreement", "milestone": "Milestone", "delivered": "Delivered",
        "payment": "Payment", "total": "Total", "billed_to": "Billed to", "invoice_date": "Invoice date",
        "due": "Due date", "offer": "Proposal", "status": "Status", "unpaid": "Unpaid", "partly_paid": "Partly paid",
        "paid": "Paid", "payment_details": "Payment Details", "no": "No", "description": "Description",
        "amount": "Amount", "total_due": "Total due", "payment_instructions": "Payment Instructions", "bank": "Bank",
        "account_number": "Account number", "account_holder": "Account holder", "payment_terms": "Payment Terms",
        "period": "Development period", "results": "Project Scope", "item": "Item", "result": "Result",
        "adjustments": "Scope Adjustments", "added_scope": "Added Scope", "price": "Price",
        "log": "Development Log", "where": "Delivered through", "summary_short": "Summary", "source": "Based on",
        "background": "Background", "needs": "Main Needs", "questions": "Open Questions", "to": "To", "work": "Work",
        "items": "Items", "qty": "Qty", "unit_price": "Unit price", "subtotal": "Subtotal",
        "first_party": "First Party", "second_party": "Second Party", "handed_over": "Handed Over", "notes": "Notes",
        "requested_by": "Requested by", "request_date": "Request date", "category": "Category", "change": "Change",
        "impact": "Impact", "area": "Area", "cost": "Cost", "timeline_impact": "Timeline",
        "other_scope": "Other scope", "test_window": "Test window", "environment": "Environment",
        "scenarios": "Test Scenarios", "scenario": "Scenario", "where_held": "Where",
        "attendees": "Attendees", "agenda": "Agenda", "decisions": "Decisions and Actions",
        "action": "Decision / action", "owner": "Owner", "role": "Role", "entries": "Log", "work_done": "Work",
        "hours": "Hours", "total_hours": "Total hours", "received_from": "Received from",
        "for_payment": "In payment of", "date_received": "Date received", "method": "Method", "in_words": "In words",
        "project": "Project", "warranty": "Warranty period", "channel": "Reporting channel",
        "response_times": "Response Times", "severity": "Severity", "example": "Example", "response": "Response",
        "page": "Page {page} of {pages}", "name": "Name", "signature": "Signature", "stamp": "Duty stamp Rp10,000",
        "approved_by_client": "Approved by the client", "received_by": "Received by",
        "accepted_by_client": "Accepted by the client", "approved": "Approved by", "purpose": "Purpose",
        "term": "Term",
    },
}
