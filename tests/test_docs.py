"""pakem docs: the store, numbering, lifecycle guards, checks, and rendering (ADR 0014)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from support import REPO_ROOT, IsolatedTestCase, init_repo, write

FAKE_BROWSER = """#!/usr/bin/env python3
import pathlib, sys
pathlib.Path(sys.argv[0]).with_suffix(".args").write_text("\\n".join(sys.argv[1:]))
for arg in sys.argv[1:]:
    if arg.startswith("--print-to-pdf="):
        open(arg.split("=", 1)[1], "wb").write(b"%PDF-1.4\\n% test\\n")
"""

ISSUER = {
    "name": "Test Developer", "email": "dev@example.com", "phone": "0800", "city": "Surabaya",
    "address": "Jl. Contoh 1", "bank": "Bank Contoh", "account_number": "0001", "account_holder": "Test Developer",
}

WORDING = {
    "proposal.id.md": "## acceptance\nUji dalam {{uat_days}} hari.\n\n## terms\n1. Ruang lingkup tetap.\n\n"
                      "## agreement\nDokumen ini dasar kesepakatan. {{approval_method}}\n",
    "invoice.id.md": "## payment_note\nCantumkan nomor invoice.\n\n## payment_terms\nSesuai proposal.\n",
    "receipt.id.md": "## statement\nTelah diterima dari {{client}} {{amount}} ({{amount_words}} rupiah).\n",
    "final.id.md": "## terms\nGaransi {{warranty}}.\n\n## agreement\nSelesai untuk {{client}}.\n",
    "bast.id.md": "## statement\nPada hari {{date_day}}, {{date}}, {{issuer}} menyerahkan {{project}}.\n",
}

PROPOSAL = """date = "2026-10-27"
valid_until = "2026-11-10"
timeline = "6 minggu"
summary = "Aplikasi reservasi."
scope = ["Pemesanan."]
out_of_scope = ["Pembayaran online."]
assumptions = ["Dua revisi."]
prerequisites = ["Akses server."]
fee = 10000000
deliverables = ["Kode sumber."]

[[milestones]]
name = "Persetujuan"
delivered = "Proposal"
date = "27 Okt"
payment = 4000000

[[milestones]]
name = "Rilis"
delivered = "Aplikasi"
date = "8 Des"
payment = 6000000
"""

INVOICE = """date = "2026-10-30"
due = "2026-11-06"

[[items]]
description = "Uang muka"
amount = 4000000
"""


def load_render_module():
    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from pakem_lib import docs_render
    return docs_render


class DocsTestCase(IsolatedTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.store = self.base / "store"
        browser = write(self.base / "fake-browser", FAKE_BROWSER)
        browser.chmod(0o755)
        self.browser = browser

    def docs(self, *args: str):
        return self.pakem("docs", *args)

    def init_store(self) -> None:
        self.assert_ok(self.docs("init", "--store", str(self.store), "--last-project", "PRJ-2026-006"))
        settings = self.home / ".config" / "pakem" / "documents.toml"
        settings.write_text(settings.read_text() + f'browser = "{self.browser}"\n')
        config = self.store / "config.toml"
        text = config.read_text()
        for key, value in ISSUER.items():
            text = text.replace(f'{key} = "[FILL]"', f'{key} = "{value}"', 1)
        config.write_text(text)
        for name, text in WORDING.items():
            write(self.store / "wording" / name, text)

    def engagement(self) -> Path:
        completed = self.docs("engagement", "new", "--client", "Klinik Contoh", "--name", "Reservasi", "--kind", "fixed",
                              "--lang", "id", "--currency", "IDR", "--representative", "Budi", "--json")
        self.assert_ok(completed)
        result = json.loads(completed.stdout)
        directory = Path(result["dir"])
        engagement = directory / "engagement.toml"
        engagement.write_text(engagement.read_text() + 'uat_days = 7\nwarranty = "30 hari"\napproval_method = "Tanda tangan."\n')
        return directory

    def approved_proposal(self) -> Path:
        directory = self.engagement()
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        (directory / "PROP-2026-007" / "v1.toml").write_text(PROPOSAL)
        self.assert_ok(self.docs("render", "PROP-2026-007"))
        self.assert_ok(self.docs("approve", "PROP-2026-007", "--by", "Budi", "--method", "signed", "--evidence", "scan",
                                 "--date", "2026-10-30"))
        return directory


class StoreAndNumberingTests(DocsTestCase):
    def test_init_refuses_a_store_inside_a_git_work_tree(self) -> None:
        repo = init_repo(self.base / "repo", self.home)
        self.assert_fails(self.docs("init", "--store", str(repo / "documents"), "--last-project", "PRJ-2026-001"), 6)

    def test_numbering_continues_from_the_last_project(self) -> None:
        self.init_store()
        first = self.engagement()
        self.assertEqual(first.name, "PRJ-2026-007-klinik-contoh")
        self.assert_ok(self.docs("engagement", "new", "--client", "Toko B", "--name", "Toko", "--kind", "workstream",
                                 "--lang", "en", "--currency", "USD"))
        self.assertTrue((self.store / "PRJ-2026-008-toko-b").is_dir())

    def test_plan_changes_nothing(self) -> None:
        self.init_store()
        before = self.snapshot(self.store)
        self.assert_ok(self.docs("engagement", "new", "--client", "X", "--name", "Y", "--kind", "fixed", "--lang", "id",
                                 "--currency", "IDR", "--plan"))
        self.assertEqual(before, self.snapshot(self.store))

    def test_repeating_documents_get_running_suffixes(self) -> None:
        self.init_store()
        self.approved_proposal()
        self.assert_ok(self.docs("new", "invoice", "PRJ-2026-007"))
        self.assert_ok(self.docs("new", "invoice", "PRJ-2026-007"))
        directory = self.store / "PRJ-2026-007-klinik-contoh"
        self.assertTrue((directory / "INV-2026-007-01").is_dir())
        self.assertTrue((directory / "INV-2026-007-02").is_dir())


class LifecycleTests(DocsTestCase):
    def test_documents_after_the_offer_wait_for_its_approval(self) -> None:
        self.init_store()
        self.engagement()
        refused = self.docs("new", "invoice", "PRJ-2026-007")
        self.assert_fails(refused, 6)
        self.assertIn("needs an approved proposal or quotation", refused.stderr)

    def test_an_approved_proposal_is_frozen(self) -> None:
        self.init_store()
        self.approved_proposal()
        self.assert_fails(self.docs("revise", "PROP-2026-007"), 6)
        self.assert_fails(self.docs("new", "proposal", "PRJ-2026-007"), 6)

    def test_approval_needs_a_rendered_version(self) -> None:
        self.init_store()
        directory = self.engagement()
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        (directory / "PROP-2026-007" / "v1.toml").write_text(PROPOSAL)
        refused = self.docs("approve", "PROP-2026-007", "--by", "Budi", "--method", "signed", "--evidence", "scan",
                            "--date", "2026-10-30")
        self.assert_fails(refused, 6)
        self.assertIn("has not been rendered", refused.stderr)

    def test_payments_cannot_exceed_the_outstanding_amount(self) -> None:
        self.init_store()
        directory = self.approved_proposal()
        self.assert_ok(self.docs("new", "invoice", "PRJ-2026-007"))
        (directory / "INV-2026-007-01" / "v1.toml").write_text(INVOICE)
        self.assert_ok(self.docs("render", "INV-2026-007-01"))
        self.assert_fails(self.docs("pay", "INV-2026-007-01", "--amount", "5000000", "--date", "2026-11-01",
                                    "--method", "transfer", "--evidence", "slip"), 6)
        self.assert_ok(self.docs("pay", "INV-2026-007-01", "--amount", "4000000", "--date", "2026-11-01",
                                 "--method", "transfer", "--evidence", "slip"))
        status = json.loads(self.docs("status", "PRJ-2026-007", "--json").stdout)
        invoice = next(item for item in status["documents"] if item["code"] == "INV-2026-007-01")
        self.assertEqual(invoice["outstanding"], 0)

    def test_close_needs_final_report_payments_and_handover(self) -> None:
        self.init_store()
        directory = self.approved_proposal()
        refused = self.docs("close", "PRJ-2026-007")
        self.assert_fails(refused, 6)
        self.assertIn("No approved final report", refused.stderr)
        self.assertIn("No recorded handover", refused.stderr)
        self.assert_ok(self.docs("new", "final", "PRJ-2026-007"))
        (directory / "FINAL-2026-007" / "v1.toml").write_text(
            'period = "Nov"\nsummary = "Selesai."\n\n[[results]]\nitem = "A"\nresult = "B"\nstatus = "Selesai"\n\n'
            '[[log]]\ndate = "1 Nov"\nsummary = "Mulai"\n\n[[deliverables]]\nitem = "Kode"\nwhere = "Repo"\n')
        self.assert_ok(self.docs("render", "FINAL-2026-007"))
        self.assert_ok(self.docs("approve", "FINAL-2026-007", "--by", "Budi", "--method", "signed", "--evidence", "scan",
                                 "--date", "2026-12-12"))
        self.assert_ok(self.docs("handover", "PRJ-2026-007", "--date", "2026-12-14", "--to", "Budi", "--evidence", "email"))
        self.assert_ok(self.docs("close", "PRJ-2026-007"))
        self.assertIn('stage = "closed"', (directory / "state.toml").read_text())


class CheckAndRenderTests(DocsTestCase):
    def test_skeleton_fails_until_filled(self) -> None:
        self.init_store()
        self.engagement()
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        failed = self.docs("check", "PROP-2026-007")
        self.assert_fails(failed, 5)
        self.assertIn("unfilled value [FILL]", failed.stdout)

    def test_owner_questions_count_as_unfilled(self) -> None:
        self.init_store()
        directory = self.engagement()
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        (directory / "PROP-2026-007" / "v1.toml").write_text(PROPOSAL.replace('"6 minggu"', '"[OWNER: timeline]"'))
        failed = self.docs("check", "PROP-2026-007")
        self.assert_fails(failed, 5)
        self.assertIn("unfilled value [OWNER: timeline]", failed.stdout)

    def test_milestone_payments_must_add_up_to_the_fee(self) -> None:
        self.init_store()
        directory = self.engagement()
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        (directory / "PROP-2026-007" / "v1.toml").write_text(PROPOSAL.replace("payment = 6000000", "payment = 5000000"))
        failed = self.docs("check", "PROP-2026-007")
        self.assert_fails(failed, 5)
        self.assertIn("milestone payments add up to 9000000, not the fee 10000000", failed.stdout)

    def test_wording_facts_must_exist(self) -> None:
        self.init_store()
        self.assert_ok(self.docs("engagement", "new", "--client", "C", "--name", "N", "--kind", "fixed", "--lang", "id",
                                 "--currency", "IDR", "--representative", "R"))
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        (self.store / "PRJ-2026-007-c" / "PROP-2026-007" / "v1.toml").write_text(PROPOSAL)
        failed = self.docs("check", "PROP-2026-007")
        self.assertIn("wording `## acceptance` needs uat_days", failed.stdout)

    def test_render_writes_html_and_pdf_in_layout_d(self) -> None:
        self.init_store()
        directory = self.approved_proposal()
        html = (directory / "PROP-2026-007" / "v1.html").read_text()
        self.assertTrue((directory / "PROP-2026-007" / "v1.pdf").read_bytes().startswith(b"%PDF"))
        self.assertIn('@top-left { content: "Test Developer"', html)
        self.assertIn('content: "Halaman " counter(page) " dari " counter(pages) ""', html)
        self.assertIn("<h2>7. Penerimaan Hasil (UAT)</h2>", html)
        self.assertIn("Uji dalam 7 hari.", html)
        self.assertIn('<td class="num">Rp10.000.000</td>', html)
        arguments = (self.base / "fake-browser.args").read_text().splitlines()
        self.assertIn("--use-mock-keychain", arguments, "printing must never ask macOS for a keychain")
        self.assertTrue(any(argument.startswith("--user-data-dir=") for argument in arguments))

    def test_a_rendered_version_must_not_change(self) -> None:
        self.init_store()
        directory = self.approved_proposal()
        content = directory / "PROP-2026-007" / "v1.toml"
        content.write_text(content.read_text() + "# edited\n")
        failed = self.docs("check", "PROP-2026-007")
        self.assert_fails(failed, 5)
        self.assertIn("v1 changed after it was rendered", failed.stdout)

    def test_receipt_needs_a_recorded_payment(self) -> None:
        self.init_store()
        directory = self.approved_proposal()
        self.assert_ok(self.docs("new", "invoice", "PRJ-2026-007"))
        (directory / "INV-2026-007-01" / "v1.toml").write_text(INVOICE)
        self.assert_ok(self.docs("render", "INV-2026-007-01"))
        self.assert_ok(self.docs("new", "receipt", "PRJ-2026-007"))
        (directory / "KW-2026-007-01" / "v1.toml").write_text(
            'invoice = "INV-2026-007-01"\ndate = "2026-11-02"\nmethod = "Transfer"\namount = 4000000\n'
            'description = "uang muka"\n')
        failed = self.docs("check", "KW-2026-007-01")
        self.assertIn("Record the payment first", failed.stdout)
        self.assert_ok(self.docs("pay", "INV-2026-007-01", "--amount", "4000000", "--date", "2026-11-02",
                                 "--method", "transfer", "--evidence", "slip"))
        self.assert_ok(self.docs("render", "KW-2026-007-01"))
        self.assertIn("empat juta rupiah", (directory / "KW-2026-007-01" / "v1.html").read_text())

    def test_render_without_a_browser_is_an_owner_action(self) -> None:
        self.init_store()
        settings = self.home / ".config" / "pakem" / "documents.toml"
        settings.write_text(settings.read_text().replace(str(self.browser), str(self.base / "missing-browser")))
        directory = self.engagement()
        self.assert_ok(self.docs("new", "proposal", "PRJ-2026-007"))
        (directory / "PROP-2026-007" / "v1.toml").write_text(PROPOSAL)
        refused = self.docs("render", "PROP-2026-007")
        self.assert_fails(refused, 3)
        self.assertIn("run `pakem docs browser`", refused.stderr)


class PrintTests(unittest.TestCase):
    def test_a_stuck_browser_ends_with_its_helpers(self) -> None:
        render = load_render_module()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            marker = base / "helper-alive"
            browser = base / "stuck-browser"
            browser.write_text(f"#!/bin/sh\n(sleep 30; touch '{marker}') &\nsleep 30\n")
            browser.chmod(0o755)
            html = base / "page.html"
            html.write_text("<p>x</p>")
            original = render.PRINT_TIMEOUT_SECONDS
            render.PRINT_TIMEOUT_SECONDS = 1
            try:
                with self.assertRaises(render.PakemError):
                    render.print_pdf(str(browser), html, base / "page.pdf")
            finally:
                render.PRINT_TIMEOUT_SECONDS = original
            completed = subprocess.run(["pgrep", "-f", str(marker)], capture_output=True, text=True)
            self.assertEqual(completed.stdout.strip(), "", "a helper outlived the timeout")


class RendererTests(unittest.TestCase):
    def test_the_newest_installed_headless_shell_is_preferred(self) -> None:
        render = load_render_module()
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            for version in ("155.0.7727.100", "156.0.8078.47"):
                shell = (home / ".local" / "share" / "pakem" / "chrome-headless-shell" / version
                         / "chrome-headless-shell-mac-arm64" / "chrome-headless-shell")
                write(shell, "#!/bin/sh\n").chmod(0o755)
            previous = os.environ.get("HOME")
            os.environ["HOME"] = str(home)
            try:
                found = render.find_browser(None)
            finally:
                os.environ["HOME"] = previous
            self.assertIn("156.0.8078.47", found)
            self.assertEqual(render.find_browser(str(home / "missing")), None)

    def test_unzipping_keeps_the_executable_bit(self) -> None:
        render = load_render_module()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "shell.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                info = zipfile.ZipInfo("shell-mac/chrome-headless-shell")
                info.external_attr = 0o755 << 16
                bundle.writestr(info, "#!/bin/sh\n")
            render.extract_zip(archive, base / "out")
            self.assertTrue(os.access(base / "out" / "shell-mac" / "chrome-headless-shell", os.X_OK))

    def test_unzipping_refuses_paths_outside_the_folder(self) -> None:
        render = load_render_module()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            archive = base / "bad.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("../escape", "x")
            with self.assertRaises(render.PakemError):
                render.extract_zip(archive, base / "out")
            self.assertFalse((base / "escape").exists())


class WordsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.render = load_render_module()

    def test_indonesian_amounts_in_words(self) -> None:
        cases = {1: "satu", 11: "sebelas", 15: "lima belas", 100: "seratus", 115: "seratus lima belas",
                 1000: "seribu", 1500: "seribu lima ratus", 7_500_000: "tujuh juta lima ratus ribu",
                 1_000_000: "satu juta", 2_000_000_000: "dua miliar", 100_000: "seratus ribu"}
        for amount, words in cases.items():
            with self.subTest(amount=amount):
                self.assertEqual(self.render.amount_in_words(amount, "id"), words)

    def test_english_amounts_in_words(self) -> None:
        self.assertEqual(self.render.amount_in_words(7_500_000, "en"), "seven million five hundred thousand")
        self.assertEqual(self.render.amount_in_words(1_005, "en"), "one thousand and five")
        self.assertEqual(self.render.amount_in_words(42, "en"), "forty-two")

    def test_money_and_dates(self) -> None:
        self.assertEqual(self.render.format_money(15_000_000, "IDR", "id"), "Rp15.000.000")
        self.assertEqual(self.render.format_money(15_000_000, "IDR", "en"), "IDR 15,000,000")
        self.assertEqual(self.render.format_date("2026-10-27", "id"), "27 Oktober 2026")
        self.assertEqual(self.render.weekday("2026-12-14", "id"), "Senin")


if __name__ == "__main__":
    unittest.main()
