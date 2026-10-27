"""Rendering for `pakem docs`: layout D in HTML, then PDF through a Chromium-family browser.

Layout D (owner, 2026-10-27): the classic header (issuer and code in the running header,
centred title) over ruled content (no frames or fills, hairlines between rows). The
running header and the page numbers are the browser's @page margin boxes, so every page
carries them.
"""

from __future__ import annotations

import html
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import date
from pathlib import Path
from string import Template

from .common import PakemError, home_dir
from .docs_types import LABELS

MONTHS = {
    "id": ("Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
           "November", "Desember"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"),
}
WEEKDAYS = {
    "id": ("Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
# Desktop browsers on macOS ask for camera, microphone, and input monitoring when a terminal
# starts them, and hang headless while the prompt waits. Google's chrome-headless-shell is
# built for automated printing and asks for none of that, so it is the default renderer.
HEADLESS_SHELL = "chrome-headless-shell"
CFT_VERSIONS_URL = "https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json"
CFT_PLATFORMS = {("darwin", "arm64"): "mac-arm64", ("darwin", "x86_64"): "mac-x64", ("linux", "x86_64"): "linux64"}
BROWSER_COMMANDS = ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")
PRINT_TIMEOUT_SECONDS = 120


def label(lang: str, key: str) -> str:
    return LABELS[lang][key]


def parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise PakemError(f"{raw!r} is not a date in YYYY-MM-DD form") from exc


def format_date(raw: str, lang: str) -> str:
    day = parse_date(raw)
    return f"{day.day} {MONTHS[lang][day.month - 1]} {day.year}"


def weekday(raw: str, lang: str) -> str:
    return WEEKDAYS[lang][parse_date(raw).weekday()]


def format_money(amount: int, currency: str, lang: str) -> str:
    grouped = f"{amount:,}"
    if lang == "id":
        grouped = grouped.replace(",", ".")
    if currency == "IDR":
        return f"Rp{grouped}" if lang == "id" else f"IDR {grouped}"
    return f"{currency} {grouped}"


def format_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


ID_UNITS = ("", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh",
            "sebelas")
EN_UNITS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
            "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
EN_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
SCALES = ((10**12, "triliun", "trillion"), (10**9, "miliar", "billion"), (10**6, "juta", "million"),
          (10**3, "ribu", "thousand"))


def _id_words(number: int) -> str:
    if number < 12:
        return ID_UNITS[number]
    if number < 20:
        return f"{ID_UNITS[number - 10]} belas"
    if number < 100:
        rest = number % 10
        return f"{ID_UNITS[number // 10]} puluh" + (f" {ID_UNITS[rest]}" if rest else "")
    if number < 200:
        return "seratus" + (f" {_id_words(number - 100)}" if number > 100 else "")
    if number < 1000:
        rest = number % 100
        return f"{ID_UNITS[number // 100]} ratus" + (f" {_id_words(rest)}" if rest else "")
    if number < 2000:
        return "seribu" + (f" {_id_words(number - 1000)}" if number > 1000 else "")
    for scale, name, _ in SCALES:
        if number >= scale:
            rest = number % scale
            return f"{_id_words(number // scale)} {name}" + (f" {_id_words(rest)}" if rest else "")
    raise AssertionError(number)


def _en_words(number: int) -> str:
    if number < 20:
        return EN_UNITS[number]
    if number < 100:
        rest = number % 10
        return EN_TENS[number // 10] + (f"-{EN_UNITS[rest]}" if rest else "")
    if number < 1000:
        rest = number % 100
        return f"{EN_UNITS[number // 100]} hundred" + (f" and {_en_words(rest)}" if rest else "")
    for scale, _, name in SCALES:
        if number >= scale:
            rest = number % scale
            tail = ""
            if rest:
                tail = f" and {_en_words(rest)}" if rest < 100 else f" {_en_words(rest)}"
            return f"{_en_words(number // scale)} {name}" + tail
    raise AssertionError(number)


def amount_in_words(amount: int, lang: str) -> str:
    if amount < 0:
        raise PakemError("an amount in words needs a positive amount")
    if amount == 0:
        return "nol" if lang == "id" else "zero"
    return _id_words(amount) if lang == "id" else _en_words(amount)


def parse_wording(text: str) -> dict[str, str]:
    """`## key` headings split a wording file into its sections."""
    sections: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+([a-z_]+)\s*$", line)
        if heading:
            current = heading.group(1)
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def wording_names(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))


def fill(text: str, values: dict[str, str]) -> str:
    return PLACEHOLDER.sub(lambda match: values[match.group(1)], text)


def _inline(text: str) -> str:
    escaped = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def wording_html(text: str) -> str:
    """A small Markdown subset: paragraphs, `1.` numbered items, and `-` bullets."""
    parts = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = chunk.splitlines()
        if re.match(r"^\d+\.\s", lines[0]) or lines[0].startswith("- "):
            ordered = bool(re.match(r"^\d+\.\s", lines[0]))
            items: list[str] = []
            for line in lines:
                if re.match(r"^(\d+\.|-)\s", line):
                    items.append(re.sub(r"^(\d+\.|-)\s+", "", line))
                elif items:
                    items[-1] += " " + line.strip()
            tag = "ol" if ordered else "ul"
            parts.append(f"<{tag}>" + "".join(f"<li>{_inline(item)}</li>" for item in items) + f"</{tag}>")
        else:
            parts.append(f"<p>{_inline(' '.join(line.strip() for line in lines))}</p>")
    return "".join(parts)


def css_string(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ") + '"'


CSS = Template("""
@page { size: A4; margin: 30mm 18mm 22mm 18mm;
  @top-left { content: $issuer; font: 700 9.5pt Arial, Helvetica, sans-serif; letter-spacing: .04em;
    color: #4d4d4d; vertical-align: bottom; padding-bottom: 2.5mm; border-bottom: 1px solid #bfbfbf; margin-bottom: 6mm; }
  @top-center { content: ""; vertical-align: bottom; border-bottom: 1px solid #bfbfbf; margin-bottom: 6mm; }
  @top-right { content: $head; font: 9.5pt Arial, Helvetica, sans-serif; color: #4d4d4d;
    vertical-align: bottom; padding-bottom: 2.5mm; border-bottom: 1px solid #bfbfbf; margin-bottom: 6mm; }
  @bottom-left { content: $code; font: 9pt Arial, Helvetica, sans-serif; color: #4d4d4d;
    vertical-align: top; padding-top: 2.5mm; border-top: 1px solid #bfbfbf; margin-top: 6mm; }
  @bottom-center { content: ""; vertical-align: top; border-top: 1px solid #bfbfbf; margin-top: 6mm; }
  @bottom-right { content: $page; font: 9pt Arial, Helvetica, sans-serif; color: #4d4d4d;
    vertical-align: top; padding-top: 2.5mm; border-top: 1px solid #bfbfbf; margin-top: 6mm; } }
* { box-sizing: border-box; }
html { background: #fff; }
body { margin: 0; font-family: Arial, Helvetica, sans-serif; color: #111; font-size: 12pt; line-height: 1.5; }
@media screen { body { max-width: 210mm; margin: 0 auto; padding: 18mm; } }
p, h1, h2, ul, ol, dl { margin: 0; }
.title { display: flex; flex-direction: column; align-items: center; gap: 6pt; margin-top: 0; text-align: center; }
.title h1 { font-size: 14pt; letter-spacing: .06em; }
.title p { font-size: 12.5pt; font-weight: 700; }
.block { display: flex; flex-direction: column; gap: 4pt; margin-top: 16pt; }
h2 { font-size: 12.5pt; font-weight: 700; break-after: avoid; }
ul, ol { padding-left: 18pt; display: flex; flex-direction: column; gap: 2pt; }
.facts { display: grid; grid-template-columns: 34% 66%; font-size: 11.25pt; line-height: 1.4; border-top: 1px solid #111; }
.facts > * { padding: 6pt 0; border-bottom: 1px solid #d9d9d9; }
.facts dt { font-weight: 700; }
.facts dd { margin: 0; }
table { width: 100%; border-collapse: collapse; font-size: 11.25pt; line-height: 1.35; font-variant-numeric: tabular-nums; }
th, td { padding: 5pt 8pt; text-align: left; vertical-align: top; }
th:first-child, td:first-child { padding-left: 0; }
th:last-child, td:last-child { padding-right: 0; }
thead th { font-weight: 700; border-bottom: 1px solid #111; }
tbody td { border-bottom: 1px solid #d9d9d9; }
tfoot td { font-weight: 700; border-top: 1px solid #111; }
.num { text-align: right; white-space: nowrap; }
tr { break-inside: avoid; }
.note { font-size: 10pt; }
.amount { display: flex; justify-content: space-between; margin-top: 16pt; padding: 10pt 0; font-size: 16pt; font-weight: 700;
  border-top: 1px solid #111; border-bottom: 1px solid #d9d9d9; }
.words { font-style: italic; }
.parties { display: grid; grid-template-columns: 1fr 1fr; gap: 14pt; }
.parties div { display: flex; flex-direction: column; }
.sign { display: grid; grid-template-columns: 1fr 1fr; gap: 18pt; margin-top: 20pt; break-inside: avoid; }
.sign > div { display: flex; flex-direction: column; gap: 4pt; font-size: 11.25pt; }
.space { height: 58pt; display: flex; align-items: center; }
.stamp { display: flex; align-items: center; justify-content: center; width: 78pt; height: 48pt; border: 1px dashed #8a9099;
  font-size: 8pt; color: #4a505a; text-align: center; }
.keep { break-inside: avoid; }
.name { padding-top: 4pt; border-top: 1px solid #8a9099; font-weight: 700; }
""")


def e(text: object) -> str:
    return html.escape(str(text))


def facts_block(rows: list[tuple[str, str]], heading: str | None = None) -> str:
    pairs = "".join(f"<dt>{e(key)}</dt><dd>{e(value)}</dd>" for key, value in rows)
    head = f"<h2>{e(heading)}</h2>" if heading else ""
    return f'<div class="block">{head}<dl class="facts">{pairs}</dl></div>'


def section(heading: str, body_html: str) -> str:
    return f'<div class="block"><h2>{e(heading)}</h2>{body_html}</div>'


def list_html(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{e(item)}</li>" for item in items) + "</ul>"


def table(heading: str | None, columns: list[str], rows: list[list[str]], numeric: set[int] = frozenset(),
          total: list[str] | None = None) -> str:
    def cells(values: list[str], tag: str) -> str:
        numeric_class = ' class="num"'
        return "".join(f"<{tag}{numeric_class if index in numeric else ''}>{e(value)}</{tag}>"
                       for index, value in enumerate(values))
    head = f"<h2>{e(heading)}</h2>" if heading else ""
    body = "".join(f"<tr>{cells(row, 'td')}</tr>" for row in rows)
    foot = f"<tfoot><tr>{cells(total, 'td')}</tr></tfoot>" if total else ""
    return (f'<div class="block">{head}<table><thead><tr>{cells(columns, "th")}</tr></thead>'
            f"<tbody>{body}</tbody>{foot}</table></div>")


def signatures(signers: list[tuple[str, str]], stamp_text: str | None) -> str:
    cells = []
    for index, (role, name) in enumerate(signers):
        stamp = f'<span class="stamp">{e(stamp_text)}</span>' if stamp_text and index == 0 else ""
        cells.append(f'<div><span>{e(role)}</span><div class="space">{stamp}</div>'
                     f'<span class="name">{e(name)}</span></div>')
    if len(cells) == 1:
        cells.insert(0, "<div></div>")
    return f'<div class="sign">{"".join(cells)}</div>'


def page(lang: str, title: str, project: str, issuer: str, code: str, when: str, body: str) -> str:
    css = CSS.substitute({
        "issuer": css_string(issuer),
        "head": css_string(f"{code} · {when}"),
        "code": css_string(code),
        "page": '"' + label(lang, "page").replace("{page}", '" counter(page) "')
        .replace("{pages}", '" counter(pages) "') + '"',
    })
    return (f'<!doctype html>\n<html lang="{lang}">\n<head>\n<meta charset="utf-8">\n<title>{e(code)}</title>\n'
            f"<style>{css}</style>\n</head>\n<body>\n"
            f'<div class="title"><h1>{e(title)}</h1><p>{e(project)}</p></div>\n{body}\n</body>\n</html>\n')


def headless_shell_root() -> Path:
    return home_dir() / ".local" / "share" / "pakem" / HEADLESS_SHELL


def installed_headless_shell() -> Path | None:
    """The newest installed chrome-headless-shell, by version folder."""
    found = [path for path in headless_shell_root().glob(f"*/{HEADLESS_SHELL}-*/{HEADLESS_SHELL}")
             if os.access(path, os.X_OK)]
    if not found:
        return None
    return max(found, key=lambda path: tuple(int(part) for part in re.findall(r"\d+", path.parent.parent.name)))


def find_browser(configured: str | None) -> str | None:
    if configured:
        return configured if Path(configured).exists() or shutil.which(configured) else None
    shell = installed_headless_shell()
    if shell is not None:
        return str(shell)
    if sys.platform != "darwin":
        for command in BROWSER_COMMANDS:
            found = shutil.which(command)
            if found:
                return found
    return None


def headless_shell_download() -> tuple[str, str]:
    """The current stable chrome-headless-shell for this machine: (version, zip URL)."""
    platform_key = CFT_PLATFORMS.get((sys.platform, os.uname().machine))
    if platform_key is None:
        raise PakemError(f"chrome-headless-shell has no build for {sys.platform} {os.uname().machine}")
    with urllib.request.urlopen(CFT_VERSIONS_URL, timeout=30) as response:
        stable = json.load(response)["channels"]["Stable"]
    for item in stable["downloads"][HEADLESS_SHELL]:
        if item["platform"] == platform_key:
            return stable["version"], item["url"]
    raise PakemError(f"Chrome for Testing lists no {HEADLESS_SHELL} for {platform_key}")


def extract_zip(archive: Path, destination: Path) -> None:
    """Unzip keeping each file's permission bits, which zipfile alone drops."""
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise PakemError(f"the archive holds a path outside its folder: {info.filename}")
            bundle.extract(info, destination)
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                os.chmod(target, mode)


def install_headless_shell(version: str, url: str) -> Path:
    destination = headless_shell_root() / version
    with tempfile.TemporaryDirectory(prefix="pakem-headless-shell-") as directory:
        archive = Path(directory) / "shell.zip"
        with urllib.request.urlopen(url, timeout=300) as response, archive.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        destination.mkdir(parents=True, exist_ok=True)
        extract_zip(archive, destination)
    shell = installed_headless_shell()
    if shell is None:
        raise PakemError(f"the download held no {HEADLESS_SHELL} executable")
    return shell


def print_pdf(browser: str, html_path: Path, pdf_path: Path) -> None:
    """Headless print with a throwaway profile, so an open browser window is never touched.
    Printing needs no stored passwords: the mock keychain keeps macOS from asking for one."""
    with tempfile.TemporaryDirectory(prefix="pakem-docs-browser-") as profile:
        headless = [] if Path(browser).name == HEADLESS_SHELL else ["--headless=new"]
        command = [browser, *headless, "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                   "--use-mock-keychain", "--password-store=basic",
                   f"--user-data-dir={profile}", "--no-pdf-header-footer", "--print-to-pdf-no-header",
                   f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()]
        # Its own process group, so a stuck print ends with all its helper processes.
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   start_new_session=True)
        try:
            stdout, stderr = process.communicate(timeout=PRINT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            raise PakemError(f"the browser did not finish printing within {PRINT_TIMEOUT_SECONDS} seconds") from exc
    if not pdf_path.is_file() or not pdf_path.read_bytes().startswith(b"%PDF"):
        detail = (stderr or stdout).strip().splitlines()[-1:] or ["no output"]
        raise PakemError(f"the browser wrote no PDF (exit {process.returncode}): {detail[0]}")
