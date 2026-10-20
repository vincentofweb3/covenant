"""Shared probe constants.

Deliberately not a conftest: `probe/conftest.py` and `probe/integration/conftest.py`
would otherwise collide on the module name `conftest`, and the second one wins
for imports inside the first tree.
"""

from pathlib import Path

PROBE_ROOT = Path(__file__).resolve().parent

CALLER = str(PROBE_ROOT / "probe_caller.py")
CALLEE = str(PROBE_ROOT / "probe_callee.py")

COVENANT = "No payment above 50000 without multisig approval."

# Fixed hex addresses. The contract converts these itself, because `sim_deploy`
# only accepts JSON arguments and a host-ABI CalldataAddress is not serialisable.
PEER_HEX = "0x" + "ab" * 20
OWNER = "0x" + "11" * 20
OTHER = "0x" + "22" * 20

# Pin the GenVM bundle explicitly.
#
# Without this, gltest picks the lexicographically newest tarball in ~/.cache.
# `genvm-lint check` drops bundle-named symlinks into that same cache, so
# "newest" can silently become a bundle with a different internal layout.
SDK_VERSION = "v0.3.0-rc7"


def _reset_contract_registry() -> None:
	"""Clear the GenVM std lib's one-contract-per-process global.

	`genlayer/gl/genvm_contracts.py` keeps a module-level `__known_contract__`.
	Direct mode raises `only one contract is allowed` on the second deploy;
	glsim, which caches contract classes, fails with
	`class is not marked for usage within storage`. Same root cause, two
	symptoms. Safe no-op when the SDK is not importable.
	"""
	try:
		import genlayer.gl.genvm_contracts as contracts
	except ImportError:
		return
	contracts.__known_contract__ = None


reset_contract_registry = _reset_contract_registry