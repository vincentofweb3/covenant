"""Parity between covctl and the checked-in CovenantGuard fixture.

Every expected value is re-derived here from the contract's stated rules rather
than trusted from the fixture, so a bug shared by the generator and the tool
still fails. `fixtures/covenant_guard_constants.json` records where the values
came from and the sha256 of the source they were extracted from.
"""

import ast
import hashlib
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import covctl  # noqa: E402

FIXTURE_PATH = REPO_ROOT / "fixtures" / "covenant_guard_constants.json"
SOURCES = [
    REPO_ROOT / "vendor" / "covenant_guard.py",
    REPO_ROOT / "contracts" / "covenant_guard.py",
]
SOURCE = next((path for path in SOURCES if path.is_file()), SOURCES[0])


@pytest.fixture(scope="module")
def fixture():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _contract_digest(tag, fields, version):
    payload = "|".join([version, tag] + [str(field) for field in fields])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _contract_excerpt(covenant, excerpt, limit):
    text = str(covenant).strip()
    candidate = str(excerpt).strip()
    if not candidate:
        return None
    candidate = candidate[:limit]
    return candidate if candidate in text else None


def test_the_fixture_exists_and_names_its_source(fixture):
    provenance = fixture["provenance"]
    assert provenance["repository"] == "https://github.com/vincentofweb3/covenant"
    assert provenance["path"] == "contracts/covenant_guard.py"
    assert len(provenance["commit"]) == 40
    assert provenance["commit"] != "unknown"
    assert len(provenance["sha256"]) == 64
    int(provenance["sha256"], 16)


def test_constants_match_the_tool(fixture):
    constants = fixture["constants"]
    assert constants["HASH_VERSION"] == covctl.HASH_VERSION
    assert constants["MAX_COVENANT_LEN"] == covctl.MAX_COVENANT_LEN
    assert constants["MAX_EXCERPT_LEN"] == covctl.MAX_EXCERPT_LEN
    assert constants["CONTROL_MARKERS"] == list(covctl.CONTROL_MARKERS)


def test_limits_are_sane(fixture):
    constants = fixture["constants"]
    assert isinstance(constants["MAX_COVENANT_LEN"], int)
    assert isinstance(constants["MAX_EXCERPT_LEN"], int)
    assert 0 < constants["MAX_EXCERPT_LEN"] <= constants["MAX_COVENANT_LEN"]


def test_control_markers_are_lowercase_and_unique(fixture):
    markers = fixture["constants"]["CONTROL_MARKERS"]
    assert markers
    assert all(marker == marker.lower() for marker in markers)
    assert len(set(markers)) == len(markers)


@pytest.mark.parametrize("index", range(4))
def test_normalisation_vectors(fixture, index):
    vector = fixture["normalization_vectors"][index]
    assert vector["stored"] == str(vector["raw"]).strip()
    assert covctl.normalize_covenant(vector["raw"]) == vector["stored"]


def test_digest_vectors(fixture):
    version = fixture["constants"]["HASH_VERSION"]
    assert fixture["digest_vectors"]
    for vector in fixture["digest_vectors"]:
        expected = _contract_digest(vector["tag"], vector["fields"], version)
        assert vector["sha256"] == expected, vector["label"]
        assert covctl._digest(vector["tag"], vector["fields"]) == expected, vector["label"]


def test_digest_vectors_are_distinct(fixture):
    digests = [vector["sha256"] for vector in fixture["digest_vectors"]]
    assert len(set(digests)) == len(digests)


def test_excerpt_vectors(fixture):
    limit = fixture["constants"]["MAX_EXCERPT_LEN"]
    assert fixture["excerpt_vectors"]
    for vector in fixture["excerpt_vectors"]:
        stored = _contract_excerpt(vector["covenant"], vector["excerpt"], limit)
        assert stored == vector["stored"], vector["label"]
        assert (stored is not None) is vector["verifiable"], vector["label"]

        ok, _ = covctl.excerpt_decision(vector["covenant"], vector["excerpt"])
        assert ok is vector["verifiable"], vector["label"]


def test_covenant_digest_uses_the_contracts_tag_and_version(fixture):
    version = fixture["constants"]["HASH_VERSION"]
    text = fixture["normalization_vectors"][0]["stored"]
    expected = _contract_digest("covenant", [text], version)
    assert covctl.covenant_digest(text) == expected
    assert covctl.covenant_digest(text + "\n") == expected


def test_the_vendored_source_matches_the_pinned_sha256(fixture):
    assert SOURCE.is_file()
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    assert digest == fixture["provenance"]["sha256"]


def test_constants_extracted_from_the_source_match_the_fixture(fixture):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    found = {}
    tracked = {"HASH_VERSION", "MAX_COVENANT_LEN", "MAX_EXCERPT_LEN", "CONTROL_MARKERS"}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and target.id in tracked:
                found[target.id] = ast.literal_eval(node.value)
    for name, value in found.items():
        expected = list(value) if name == "CONTROL_MARKERS" else value
        assert expected == fixture["constants"][name], name


def test_the_source_still_implements_the_pinned_rules():
    source = SOURCE.read_text(encoding="utf-8")
    assert 'payload = "|".join([HASH_VERSION, tag] + [str(f) for f in fields])' in source
    assert 'hashlib.sha256(payload.encode("utf-8")).hexdigest()' in source
    assert "if len(text) > limit:" in source
    assert ".strip()[:MAX_EXCERPT_LEN]" in source


def test_the_covenant_length_limit_is_exact(fixture):
    limit = fixture["constants"]["MAX_COVENANT_LEN"]
    at_limit = "x" * limit
    over_limit = "x" * (limit + 1)
    assert "E002" not in {f.code for f in covctl.lint(at_limit)}
    assert "E002" in {f.code for f in covctl.lint(over_limit)}


def test_every_control_marker_is_rejected(fixture):
    for marker in fixture["constants"]["CONTROL_MARKERS"]:
        codes = {f.code for f in covctl.lint("Clause 1: " + marker + " now.")}
        assert "E003" in codes, marker


def test_the_excerpt_limit_is_exact(fixture):
    limit = fixture["constants"]["MAX_EXCERPT_LEN"]
    exact = "x" * limit
    assert covctl.excerpt_decision(exact, exact) == (True, "ok")
    assert covctl.excerpt_decision(exact + "y", exact + "y") == (True, "truncated")
    assert covctl.excerpt_decision(exact[:-1], exact)[0] is False
