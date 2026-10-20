"""Constants and low-level helpers shared by the direct-mode suite.

This lives outside `conftest.py` on purpose. pytest imports every `conftest.py`
under the module name `conftest`, so when `tests/direct` and `tests/integration`
are collected in one run the two files collide and `import conftest` silently
resolves to whichever was imported first. A uniquely named module removes the
ambiguity; `conftest.py` re-exports these names for fixtures.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Pin the GenVM bundle explicitly.
#
# Without this, gltest picks the lexicographically newest tarball in ~/.cache.
# `genvm-lint check` drops bundle-named symlinks into that same cache, so
# "newest" can silently become a bundle with a different internal layout.
# See state/MEMORY.md §11.
SDK_VERSION = "v0.3.0-rc7"

CONTRACT = str(REPO_ROOT / "contracts" / "covenant_guard.py")

# Three clauses. The excerpt used by compliant fixtures must appear verbatim in
# this text -- that is the whole point of the design, and the tests are built so
# that a change to the guard's verification would break them.
COVENANT = (
	"Clause 1: The approved vendor list is maintained by the risk desk.\n"
	"Clause 2: Recurring monthly payments to an approved vendor are permitted.\n"
	"Clause 3: No single payment above 50000 may be released without multisig "
	"approval from two distinct signers.\n"
	"Clause 4: Any counterparty not on the approved vendor list is prohibited."
)

# A verbatim substring of Clause 2.
EXCERPT_OK = "Recurring monthly payments to an approved vendor are permitted"

# Plausible, specific, and absent from the covenant. This is the attack the
# design exists to stop.
EXCERPT_FABRICATED = "Payments below 50000 require no multisig approval at all"

VAULT_HEX = "0x" + "ab" * 20

QUORUM = 2
TIMELOCK_SECONDS = 3600

# An amended covenant used by the amendment tests. Clause 3's threshold is
# raised, which is what lets a previously VIOLATED operation become ALLOWED
# after activation -- the demo's final and most persuasive step.
COVENANT_V2 = (
	"Clause 1: The approved vendor list is maintained by the risk desk.\n"
	"Clause 2: Recurring monthly payments to an approved vendor are permitted.\n"
	"Clause 3: No single payment above 250000 may be released without multisig "
	"approval from two distinct signers.\n"
	"Clause 4: Any counterparty not on the approved vendor list is prohibited."
)

T0 = "2026-01-01T00:00:00+00:00"


def set_clock(iso_datetime: str) -> None:
	"""Move the chain clock the contract actually reads.

	``VMContext.warp()`` refreshes only ``sender_address`` and
	``origin_address`` on ``gl.message_raw``; it never touches ``datetime``, so a
	test that calls ``warp()`` and then asserts on a deadline is asserting on a
	stale value. Writing the key in place is the only correct approach. See
	state/MEMORY.md §13.

	The module is reached through ``sys.modules`` rather than ``import`` on
	purpose: the contract-side ``genlayer`` package is not importable from the
	host at all (see state/MEMORY.md §0). It only exists in ``sys.modules`` once
	``deploy_contract`` has loaded a contract, and it lives there as
	``genlayer.gl``, which re-exports ``message_raw``.
	"""
	import sys

	module = sys.modules.get("genlayer.gl")
	if module is None:
		raise RuntimeError(
			"genlayer.gl is not loaded; deploy a contract before touching the clock"
		)
	raw = getattr(module, "message_raw", None)
	if not isinstance(raw, dict):
		raise RuntimeError("gl.message_raw is not a mutable dict in this runtime")
	raw["datetime"] = iso_datetime


def reset_contract_registry() -> None:
	"""Clear the GenVM std lib's one-contract-per-process global.

	See state/MEMORY.md §12. Safe no-op when the SDK is not importable.
	"""
	try:
		import genlayer.gl.genvm_contracts as contracts
	except ImportError:
		return
	contracts.__known_contract__ = None
