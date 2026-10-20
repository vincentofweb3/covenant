#!/usr/bin/env python3
"""Preflight: architectural invariants and a secret scan, before anything runs.

Deliberately dependency-free (`ast` and `pathlib` only) so it runs as the first
CI step on any Python 3.12+, with nothing installed and nothing downloaded. It
catches the class of mistake that a linter cannot: an invariant that was true in
the previous commit and is not any more.

Run:

    python3 scripts/preflight.py
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The pinned GenVM bundle. Must match the header on line 1 of every contract and
# the SDK_VERSION in tests/direct/conftest.py -- see state/MEMORY.md §11.
HEADER = '# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }'
SDK_VERSION = "v0.3.0-rc7"

TARGET_RPC = "https://studio.genlayer.com/api"
TARGET_CHAIN_ID = 61999

CONTRACTS = ROOT / "contracts"

# Files allowed to contain anything that looks like key material. `preflight.py`
# exempts itself because it necessarily holds the patterns it searches for.
SCAN_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".txt", ".toml", ".cfg", ".ini"}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "artifacts", ".mypy_cache"}

# High-signal patterns only. A wide net produces noise, and a noisy secret
# scanner gets disabled, which is worse than none.
SECRET_PATTERNS = {
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "eth private key": re.compile(r"\b0x[0-9a-fA-F]{64}\b"),
    "aws access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}\b"),
    "slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "openai key": re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
    "anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9-]{32,}\b"),
}

# Lines that name a hash rather than hold a key. A private key is 32 bytes and so
# is a transaction hash; only the field name tells them apart.
HASH_CONTEXT = re.compile(
    r'"(tx_id|hash|transaction_hash|ruling_digest|input_digest|binding_digest|'
    r'covenant_hash|validator_votes_hash|raw_error)"\s*:'
    r"|^\s*[A-Z_]*(HASH|DIGEST|TX_ID)[A-Z_]*\s*="
)

failures: list[str] = []
checks = 0
files_scanned = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(message)


# --- contracts -------------------------------------------------------------


def check_contracts() -> None:
    global files_scanned
    files = sorted(CONTRACTS.glob("*.py"))
    check(bool(files), "contracts/ contains no .py files")

    for path in files:
        text = path.read_text(encoding="utf-8")
        files_scanned += 1
        first = text.splitlines()[0] if text.splitlines() else ""

        check(
            first == HEADER,
            f"{path.name}: dependency header must be line 1 and match the pinned runtime. "
            f"got: {first!r}",
        )

        try:
            ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            failures.append(f"{path.name}: does not parse -- {exc}")
            continue

        classes = [
            node
            for node in ast.walk(ast.parse(text))
            if isinstance(node, ast.ClassDef)
            # `gl.Contract` parses as an Attribute (no .id); a bare `Contract`
            # base parses as a Name.
            and any(
                (isinstance(base, ast.Attribute) and base.attr == "Contract")
                or (isinstance(base, ast.Name) and base.id == "Contract")
                for base in node.bases
            )
        ]
        check(
            len(classes) == 1,
            f"{path.name}: exactly one contract class per file "
            f"(genvm-lint and the std lib both enforce this); found {len(classes)}",
        )

        # No caller-supplied outcome. A method that takes a verdict is a bypass.
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = [a.arg for a in node.args.args]
            for suspicious in ("verdict", "outcome", "ruling", "approved"):
                check(
                    suspicious not in args,
                    f"{path.name}:{node.lineno}: {node.name}() accepts '{suspicious}' -- "
                    f"a verdict must never be caller-supplied",
                )

        # Fail loudly. Bare except in contract code hides exactly the model and
        # parsing failures this project exists to handle explicitly.
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                failures.append(
                    f"{path.name}:{node.lineno}: bare `except:` -- model output must be "
                    f"normalised explicitly, not swallowed"
                )

    # The consensus boundary must exist exactly once, in the guard.
    #
    # Both halves of this matter. A guard with no `run_nondet` is a contract
    # that trusts a caller, which is the failure this project exists to prevent.
    # A contract that *also* has one -- the vault -- is a second judge, and a
    # second judge is a second opinion nothing reconciles.
    seams = {
        path.name: path.read_text(encoding="utf-8").count("gl.vm.run_nondet(")
        for path in files
    }
    check(
        sum(1 for count in seams.values() if count > 0) == 1,
        f"exactly one contract may hold the equivalence-principle seam; found {seams}",
    )
    check(
        any(count > 0 for count in seams.values()),
        "no contract uses gl.vm.run_nondet -- there is no consensus boundary in this project",
    )
    vault = CONTRACTS / "treasury_vault.py"
    if vault.exists():
        check(
            "gl.vm.run_nondet(" not in vault.read_text(encoding="utf-8"),
            "treasury_vault.py must consult the guard, never rule for itself",
        )

    # Cross-contract messages must use finalized semantics.
    for path in files:
        text = path.read_text(encoding="utf-8")
        if "get_contract_at" in text:
            check(
                'on="finalized"' in text or "on='finalized'" in text,
                f"{path.name}: emits a cross-contract message without on=\"finalized\" -- "
                f"`accepted` is documented as producing undesired results",
            )


# --- harness invariants ----------------------------------------------------


def check_harness() -> None:
    global files_scanned
    # The pin may live in conftest.py or in the suite's support module, but it
    # must be defined somewhere in the direct-mode tree.
    direct_tree = sorted((ROOT / "tests" / "direct").glob("*.py"))
    check(bool(direct_tree), "tests/direct has no Python files")
    pinned = None
    for candidate in direct_tree:
        text = candidate.read_text(encoding="utf-8")
        files_scanned += 1
        match = re.search(r'SDK_VERSION\s*=\s*"([^"]+)"', text)
        if match is not None:
            pinned = (candidate.name, match.group(1))
            break
    check(
        pinned is not None and pinned[1] == SDK_VERSION,
        f"tests/direct: SDK_VERSION must be {SDK_VERSION} -- without an explicit pin, "
        f"gltest silently takes the newest cached GenVM bundle (state/MEMORY.md §11)",
    )

    readme = ROOT / "README.md"
    check(readme.exists(), "README.md is missing")
    if readme.exists():
        text = readme.read_text(encoding="utf-8")
        files_scanned += 1
        check(TARGET_RPC in text, f"README.md does not document the target RPC {TARGET_RPC}")
        check(str(TARGET_CHAIN_ID) in text, f"README.md does not document chain id {TARGET_CHAIN_ID}")

    # The honest-limitations rule, enforced. This project is judged partly on
    # whether it overclaims, so the claim surface is a machine-checked artifact.
    threat = ROOT / "docs" / "THREAT_MODEL.md"
    check(threat.exists(), "docs/THREAT_MODEL.md is missing")
    if threat.exists():
        text = threat.read_text(encoding="utf-8")
        files_scanned += 1
        # Heading numbers move as the document grows, so match on the title text
        # rather than the number.
        for heading in ("Explicit non-goals", "What this does not establish"):
            check(
                heading in text,
                f"docs/THREAT_MODEL.md is missing the '{heading}' section",
            )


# --- secret scan -----------------------------------------------------------


def check_no_secrets() -> None:
    global files_scanned
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name == ".env" or path.name == "preflight.py":
            # Never open a real .env, even to scan it. It is not in git; if it
            # leaked, git is what to check, not this script.
            continue
        if path.suffix not in SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        files_scanned += 1
        for label, pattern in SECRET_PATTERNS.items():
            for match in pattern.finditer(text):
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                line = text[line_start : line_end if line_end != -1 else len(text)]
                # A 32-byte hash and a private key are the same shape. Evidence
                # files are full of transaction hashes, so a bare hex match is
                # only a finding when the line is not a hash field. Keeping the
                # wide net for a naked key is why this is a context check and
                # not a narrower regex.
                if label == "eth private key" and HASH_CONTEXT.search(line):
                    continue
                line_no = text[: match.start()].count("\n") + 1
                failures.append(f"{path.relative_to(ROOT)}:{line_no}: possible {label}")


def main() -> int:
    check_contracts()
    check_harness()
    check_no_secrets()

    if failures:
        print("✗ Preflight failed")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print(f"✓ Preflight passed ({checks} checks, {files_scanned} files scanned)")
    print(f"  runtime pin: {HEADER}")
    print(f"  sdk version:  {SDK_VERSION}")
    print(f"  target:       chain {TARGET_CHAIN_ID} via {TARGET_RPC}")
    return 0


if __name__ == "__main__":
    sys.exit(main())