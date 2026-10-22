#!/usr/bin/env python3
"""covctl -- a covenant compiler and policy linter for GenLayer's CovenantGuard.

A covenant is a written policy that a model will be asked to apply. Two things
go wrong in practice, and both are cheap to prevent before deployment:

1. **The covenant is unciteable.** CovenantGuard only honours an `ALLOWED`
   verdict whose excerpt is a verbatim substring of the covenant, capped at
   `MAX_EXCERPT_LEN` characters. A clause longer than that can never be cited in
   full, so every ruling that relies on it is silently downgraded to `AMBIGUOUS`.
   A clause too short to be distinctive does not narrow anything.

2. **The covenant is undecidable.** Rules that contradict, thresholds with no
   unit, terms used before they are defined, or no fallback for ambiguous
   evidence. The contract will not complain; the model will improvise, and an
   improvised verdict on a real treasury is the expensive outcome.

So: lint before you deploy, and compute the digest the contract will compute so
the deployed text can be verified against what you intended.

    covctl lint covenant.md
    covctl compile covenant.md
    covctl digest covenant.md
    covctl check-excerpt covenant.md --excerpt "..."
    covctl selftest

Deliberately dependency-free. It must run in CI as the first thing, before
anything is installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

# Must match contracts/covenant_guard.py. Kept here as literals rather than
# imported: importing the contract would require the GenVM SDK, and this tool
# exists to run before anything is installed.
__version__ = "0.2.0"
HASH_VERSION = "covenant-sha256-v1"
MAX_COVENANT_LEN = 8000
MAX_EXCERPT_LEN = 320

# Bounded, and the same list the contract enforces.
CONTROL_MARKERS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the covenant",
    "reveal your system prompt",
    "you are now",
    "developer message",
    "override the verdict",
    "return allowed unconditionally",
)

MIN_CLAUSE_CHARS = 24

SEVERITIES = ("error", "warning", "info")


class Finding:
    def __init__(self, severity: str, code: str, message: str, line: int | None = None):
        self.severity = severity
        self.code = code
        self.message = message
        self.line = line

    def render(self) -> str:
        where = f" (line {self.line})" if self.line else ""
        return f"{self.severity.upper():7} {self.code}{where}: {self.message}"


# --- exact input handling --------------------------------------------------


def normalize_covenant(text: str) -> str:
    """The contract's `_check_text` normalisation: `str(value).strip()`.

    CovenantGuard stores the stripped text and hashes those bytes. The tool has
    to do the same, otherwise a trailing newline silently changes the digest.
    Internal bytes, including CRLF, are left untouched.
    """
    return str(text).strip()


def read_covenant(path: str) -> str:
    """Read without newline translation, so CRLF survives to the digest."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


# --- parsing ---------------------------------------------------------------


class Clause:
    def __init__(self, label: str, text: str, line: int):
        self.label = label
        self.text = text
        self.line = line

    @property
    def citable(self) -> str:
        return self.text.strip()


CLAUSE_HEADER_RE = re.compile(
    r"^[ \t]*(?:Clause[ \t]+(?P<named>\d+)[ \t]*[.:)]?|(?P<bare>\d+)[ \t]*[.:)])"
    r"(?:[ \t]+(?P<rest>.*))?$",
    re.IGNORECASE,
)

PARAGRAPH_GAP_RE = re.compile(r"(?:\r\n|\r|\n)[ \t]*(?:(?:\r\n|\r|\n)[ \t]*)+")


def split_clauses(covenant: str) -> list[Clause]:
    """Split a covenant into the smallest units a model could cite.

    Two shapes are supported, both taken from the exact stored bytes:

    * numbered clauses -- ``Clause 3: ...`` or ``3. ...``; a leading
      unnumbered block is reported as ``Preamble``;
    * otherwise, blank-line separated paragraphs.

    Numbering is preferred when present because it gives stable labels. The
    returned text is always the exact substring of the stored covenant, so the
    reported lengths and citations refer to text that really exists.
    """
    text = normalize_covenant(covenant)
    if not text:
        return []

    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    position = 0
    for line in lines:
        offsets.append(position)
        position += len(line)

    headers: list[tuple[int, re.Match]] = []
    for index, line in enumerate(lines):
        match = CLAUSE_HEADER_RE.match(line.rstrip("\r\n"))
        if match:
            headers.append((index, match))

    clauses: list[Clause] = []

    if headers:
        preamble = text[: offsets[headers[0][0]]].strip()
        if preamble:
            clauses.append(Clause("Preamble", preamble, 1))
        for order, (index, match) in enumerate(headers):
            inline = match.group("rest")
            start = offsets[index] + (match.start("rest") if inline is not None else match.end())
            stop = offsets[headers[order + 1][0]] if order + 1 < len(headers) else len(text)
            body = text[start:stop].strip()
            if body:
                number = match.group("named") or match.group("bare")
                clauses.append(Clause(f"Clause {number}", body, index + 1))
        return clauses

    gaps = list(PARAGRAPH_GAP_RE.finditer(text))
    starts = [0] + [gap.end() for gap in gaps]
    stops = [gap.start() for gap in gaps] + [len(text)]
    for number, (start, stop) in enumerate(zip(starts, stops), start=1):
        body = text[start:stop].strip()
        if body:
            clauses.append(Clause(f"Paragraph {number}", body, text.count("\n", 0, start) + 1))
    return clauses

# --- the linter ------------------------------------------------------------


def lint(covenant: str) -> list[Finding]:
    covenant = normalize_covenant(covenant)
    findings: list[Finding] = []
    clauses = split_clauses(covenant)

    if not covenant:
        findings.append(Finding("error", "E001", "the covenant is empty"))
        return findings

    # --- size ------------------------------------------------------------
    if len(covenant) > MAX_COVENANT_LEN:
        findings.append(
            Finding(
                "error",
                "E002",
                f"covenant is {len(covenant)} chars; the contract rejects anything "
                f"over {MAX_COVENANT_LEN}",
            )
        )

    # --- input the contract will reject outright -------------------------
    lowered = covenant.lower()
    for marker in CONTROL_MARKERS:
        if marker in lowered:
            findings.append(
                Finding(
                    "error",
                    "E003",
                    f"contains {marker!r}; the contract rejects this at the boundary "
                    f"as active control language",
                )
            )

    # --- citeability: the check that decides whether rulings can succeed ---
    for clause in clauses:
        length = len(clause.citable)
        if length > MAX_EXCERPT_LEN:
            findings.append(
                Finding(
                    "warning",
                    "W010",
                    f"{clause.label} is {length} chars, so it cannot be cited in full: "
                    f"the contract truncates a citation at {MAX_EXCERPT_LEN} chars. A "
                    f"ruling must rely on a fragment, which may omit part of the "
                    f"condition. Split the clause.",
                    clause.line,
                )
            )
        elif length < MIN_CLAUSE_CHARS:
            findings.append(
                Finding(
                    "warning",
                    "W011",
                    f"{clause.label} is only {length} chars; too short to narrow anything "
                    f"and likely to be cited ambiguously",
                    clause.line,
                )
            )

    # --- decidability ----------------------------------------------------
    permits = [c for c in clauses if re.search(r"\b(permitted|allowed|may|approved)\b", c.text, re.I)]
    prohibits = [c for c in clauses if re.search(r"\b(prohibited|forbidden|never|must not|no )\b", c.text, re.I)]
    if permits and prohibits:
        findings.append(
            Finding(
                "info",
                "I020",
                f"{len(permits)} permitting and {len(prohibits)} prohibiting clause(s). "
                f"Confirm none address the same subject; the model cannot resolve a "
                f"genuine contradiction and will pick one.",
            )
        )

    if prohibits and not permits:
        findings.append(
            Finding(
                "warning",
                "W012",
                "no clause permits anything: every rule forbids. A compliant action and "
                "an action the policy never mentions look the same to the model, so the "
                "guard can only refuse. Add an explicit permitting clause.",
            )
        )

    for clause in clauses:
        # A bare number in a threshold clause with no unit.
        if re.search(r"\b(above|over|exceed|greater than|less than|under|below)\b", clause.text, re.I):
            numbers = re.findall(r"(?<![\w.])(\d[\d,_]*)", clause.text)
            if numbers and not re.search(
                r"\b(gen|ether|eth|usd|eur|token|coin|unit|percent|%|days?|hours?|months?)\b",
                clause.text,
                re.I,
            ):
                findings.append(
                    Finding(
                        "warning",
                        "W021",
                        f"{clause.label} sets a numeric threshold with no unit "
                        f"({', '.join(numbers[:3])}). The model cannot tell whether the "
                        f"amount is denominated.",
                        clause.line,
                    )
                )

    # --- missing fallback -------------------------------------------------
    if not re.search(r"\b(ambiguous|insufficient|unclear|inconclusive|if in doubt)\b", covenant, re.I):
        findings.append(
            Finding(
                "warning",
                "W030",
                "the covenant says nothing about what to do when evidence is ambiguous. "
                "The contract already fails closed in that case; saying so in the policy "
                "removes the model's discretion over when to give up.",
            )
        )

    # --- defined terms ----------------------------------------------------
    # A term counts as defined when a clause says it is *maintained*. The first
    # version matched only `<Word> list`, which captured "vendor" from
    # "the approved vendor list is maintained" and then reported the very clause
    # that defines the term as leaving it undefined. Match the whole phrase.
    DEFINED_TERM = (
        r"\b((?:[a-z]+\s+){1,3}[a-z]+)\s+(list|register|roster)\s+"
        r"(?:is|are)\s+(?:maintained|managed|kept|updated|published)\b"
    )
    DEFINING_RE = re.compile(DEFINED_TERM, re.IGNORECASE)
    REFERENCE_RE = re.compile(
        r"\b((?:approved|authorised|authorized|permitted|eligible)\s+[a-z]+\s+"
        r"(?:list|register|roster))\b",
        re.IGNORECASE,
    )
    defined = set()
    for clause in clauses:
        for match in DEFINING_RE.finditer(clause.text):
            # The term is the whole noun phrase: "approved vendor list". An
            # earlier version captured only the qualifier ("approved vendor"),
            # so every reference then looked undefined -- the linter reported
            # the very clause that defines a term as failing to define it.
            term = f"{match.group(1)} {match.group(2)}".strip().lower()
            # "The approved vendor list is maintained" defines "approved vendor
            # list", not "the approved vendor list". The article is not part of
            # the term, and leaving it in made every reference look undefined.
            if term.startswith("the "):
                term = term[4:]
            defined.add(term)
    referenced = {
        m.group(1).strip().lower() for clause in clauses for m in REFERENCE_RE.finditer(clause.text)
    }
    for term in sorted(referenced - defined):
        findings.append(
            Finding(
                "warning",
                "W040",
                f"{term!r} is referenced but no clause maintains it. The model has "
                f"nothing to check membership against, so the rule is unenforceable.",
            )
        )

    # --- numbering --------------------------------------------------------
    labels = [c.label for c in clauses if c.label.startswith("Clause ")]
    numbers = [int(label.split()[1]) for label in labels]
    if numbers:
        expected = list(range(1, len(numbers) + 1))
        if numbers != expected:
            findings.append(
                Finding(
                    "warning",
                    "W050",
                    f"clause numbering is {numbers}, expected contiguous {expected}. "
                    f"Gaps suggest an editing accident and make the audit trail harder to read.",
                )
            )

    return findings


# --- digest ----------------------------------------------------------------


def _digest(tag: str, fields: list) -> str:
    """The contract's `_digest`, reproduced exactly."""
    payload = "|".join([HASH_VERSION, tag] + [str(field) for field in fields])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def covenant_digest(covenant: str) -> str:
    """Byte-identical to `covenant_guard._digest("covenant", [text])`.

    The contract hashes ``_check_text(covenant, ...)``, which strips the text.
    Normalising here is not cosmetic: a file with a trailing newline would
    otherwise hash differently from what the chain stores.
    """
    return _digest("covenant", [normalize_covenant(covenant)])


def excerpt_decision(covenant: str, excerpt: str) -> tuple[bool, str]:
    """Mirror the contract's decision path for a model-supplied excerpt.

    ``_normalize`` strips the excerpt and truncates it to ``MAX_EXCERPT_LEN``
    *before* the substring check. An over-long citation is therefore not
    rejected; its tail is ignored. ``_excerpt_is_verifiable`` is stricter, but
    it only ever sees the already-truncated value.
    """
    text = normalize_covenant(covenant)
    candidate = str(excerpt).strip()
    if candidate == "":
        return False, "empty"
    truncated = len(candidate) > MAX_EXCERPT_LEN
    candidate = candidate[:MAX_EXCERPT_LEN]
    if candidate not in text:
        return False, "not_in_covenant"
    return True, "truncated" if truncated else "ok"

# --- commands --------------------------------------------------------------


def cmd_lint(args: argparse.Namespace) -> int:
    covenant = normalize_covenant(read_covenant(args.path))
    findings = lint(covenant)

    for finding in findings:
        print(finding.render())

    errors = sum(1 for finding in findings if finding.severity == "error")
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    infos = sum(1 for finding in findings if finding.severity == "info")
    clauses = len(split_clauses(covenant))

    print()
    print(f"{clauses} clause(s), {len(covenant)} chars (contract length)")
    print(f"{errors} error(s), {warnings} warning(s), {infos} info")

    if args.strict and warnings:
        return 1
    return 1 if errors else 0


def cmd_compile(args: argparse.Namespace) -> int:
    covenant = normalize_covenant(read_covenant(args.path))
    findings = [finding for finding in lint(covenant) if finding.severity == "error"]
    if findings:
        print("refusing to compile a covenant with errors:")
        for finding in findings:
            print(f"  {finding.render()}")
        return 1

    clauses = split_clauses(covenant)
    compiled = {
        "version": 1,
        "hash": covenant_digest(covenant),
        "length": len(covenant),
        "clauses": [
            {"label": clause.label, "line": clause.line, "citable": clause.citable, "chars": len(clause.citable)}
            for clause in clauses
        ],
    }
    print(json.dumps(compiled, indent=2, sort_keys=True))
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    print(covenant_digest(read_covenant(args.path)))
    return 0


def cmd_check_excerpt(args: argparse.Namespace) -> int:
    """Mirror the contract's decision path, including its truncation order."""
    covenant = read_covenant(args.path)
    ok, reason = excerpt_decision(covenant, args.excerpt)

    if not ok:
        if reason == "empty":
            print("INVALID: an ALLOWED verdict must cite a clause; the excerpt is empty after trimming")
        else:
            print("INVALID: excerpt does not occur verbatim in the stored covenant")
            print("        the contract would discard it and record AMBIGUOUS / UNVERIFIED_EXCERPT")
        return 1

    candidate = args.excerpt.strip()[:MAX_EXCERPT_LEN]
    if reason == "truncated":
        print(
            f"VALID (truncated): the contract ignores everything after {MAX_EXCERPT_LEN} chars; "
            f"the retained prefix matches"
        )

    clause = next((c for c in split_clauses(covenant) if candidate in c.text), None)
    print(f"VALID: cited from {clause.label}" if clause else "VALID: cited verbatim")
    return 0


# --- self-test against the contract ----------------------------------------

FIXTURE_NAME = "covenant_guard_constants.json"


def _fixture_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    override = os.environ.get("COVCTL_FIXTURE")
    if override:
        candidates.append(Path(override))
    candidates += [
        here.parent / "fixtures" / FIXTURE_NAME,
        here.parent.parent / "fixtures" / FIXTURE_NAME,
        Path(sys.prefix) / "share" / "covctl" / FIXTURE_NAME,
    ]
    return candidates


def load_fixture() -> dict | None:
    """Load the checked-in, contract-derived fixture, or None."""
    for candidate in _fixture_candidates():
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _contract_candidates() -> list[Path]:
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    override = os.environ.get("COVENANT_GUARD")
    if override:
        candidates.append(Path(override))
    candidates += [
        here.parent / "contracts" / "covenant_guard.py",
        here.parent.parent / "contracts" / "covenant_guard.py",
        here.parent / "vendor" / "covenant_guard.py",
        here.parent.parent / "vendor" / "covenant_guard.py",
        Path(sys.prefix) / "share" / "covctl" / "covenant_guard.py",
    ]
    return candidates


def locate_contract() -> Path | None:
    for candidate in _contract_candidates():
        if candidate.is_file():
            return candidate
    return None


def cmd_selftest(args: argparse.Namespace) -> int:
    """Fail-closed parity check against the contract or its pinned fixture.

    The fixture is checked in, so this works without the contract repository or
    the GenVM runner. When the contract source *is* present, its sha256 must
    match the pinned provenance, which turns silent drift into a hard failure.
    """
    fixture = load_fixture()
    if fixture is None:
        print("FAIL: no contract-derived fixture found; refusing to claim parity")
        for candidate in _fixture_candidates():
            print(f"      looked in {candidate}")
        print("      generate one with scripts/gen_fixture.py or set COVCTL_FIXTURE")
        return 1

    failures: list[str] = []
    constants = fixture["constants"]
    expected = {
        "HASH_VERSION": HASH_VERSION,
        "MAX_COVENANT_LEN": MAX_COVENANT_LEN,
        "MAX_EXCERPT_LEN": MAX_EXCERPT_LEN,
        "CONTROL_MARKERS": list(CONTROL_MARKERS),
    }
    for name, value in expected.items():
        if constants.get(name) != value:
            failures.append(f"{name}: tool={value!r} fixture={constants.get(name)!r}")

    for vector in fixture["normalization_vectors"]:
        got = normalize_covenant(vector["raw"])
        if got != vector["stored"]:
            failures.append(f"normalize {vector['label']}: tool={got!r} fixture={vector['stored']!r}")

    for vector in fixture["digest_vectors"]:
        got = _digest(vector["tag"], vector["fields"])
        if got != vector["sha256"]:
            failures.append(f"digest {vector['label']}: tool={got} fixture={vector['sha256']}")

    for vector in fixture["excerpt_vectors"]:
        got, _ = excerpt_decision(vector["covenant"], vector["excerpt"])
        if got != vector["verifiable"]:
            failures.append(f"excerpt {vector['label']}: tool={got} fixture={vector['verifiable']}")

    source = locate_contract()
    if source is not None:
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        pinned = fixture["provenance"]["sha256"]
        if digest != pinned:
            failures.append(f"contract source sha256 {digest} != pinned {pinned}")

    if failures:
        print("FAIL: tool and contract fixture disagree")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    provenance = fixture["provenance"]
    print(
        f"OK: {len(fixture['normalization_vectors'])} normalization, "
        f"{len(fixture['digest_vectors'])} digest and "
        f"{len(fixture['excerpt_vectors'])} excerpt vectors match the contract"
    )
    print(f"    fixture: {provenance['repository']}/{provenance['path']} @ {provenance['commit'][:12]}")
    print(f"    sha256:  {provenance['sha256']}")
    if source is None:
        print("    contract source not present; verified against the checked-in fixture")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="covctl",
        description="Compile and lint a CovenantGuard covenant.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_lint = sub.add_parser("lint", help="report problems that would degrade or defeat rulings")
    p_lint.add_argument("path")
    p_lint.add_argument("--strict", action="store_true", help="treat warnings as failures")
    p_lint.set_defaults(func=cmd_lint)

    p_compile = sub.add_parser("compile", help="emit the parsed covenant and its digest")
    p_compile.add_argument("path")
    p_compile.set_defaults(func=cmd_compile)

    p_digest = sub.add_parser("digest", help="print the digest the contract will compute")
    p_digest.add_argument("path")
    p_digest.set_defaults(func=cmd_digest)

    p_excerpt = sub.add_parser("check-excerpt", help="verify a citation the way the contract would")
    p_excerpt.add_argument("path")
    p_excerpt.add_argument("--excerpt", required=True)
    p_excerpt.set_defaults(func=cmd_check_excerpt)

    p_self = sub.add_parser("selftest", help="check the digest formula against the contract")
    p_self.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
