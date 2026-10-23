"""Shared fixtures for the direct-mode suite.

No network. The model is replaced by a fixed reply function, which proves the
contract's normalisation and state machine, not live model behaviour.
"""

import pytest
from gltest.direct import pytest_plugin  # noqa: F401  (registers direct_* fixtures)

from .direct_support import (  # noqa: F401  (re-exported for fixtures and tests)
	CLAIM_VALID_UNTIL,
	CONTRACT,
	COVENANT,
	COVENANT_V2,
	EXCERPT_FABRICATED,
	EXCERPT_OK,
	PROCUREMENT_HEX,
	QUORUM,
	RISK_HEX,
	SDK_VERSION,
	SIGNER_A_HEX,
	SIGNER_B_HEX,
	SOURCE_DIGEST,
	SOURCE_URI,
	T0,
	TIMELOCK_SECONDS,
	VAULT_HEX,
	VENDOR_HEX,
	reset_contract_registry,
	set_clock,
)

@pytest.fixture(autouse=True)
def _contract_registry_guard():
	reset_contract_registry()
	yield
	reset_contract_registry()


@pytest.fixture
def deploy_guard(direct_deploy):
	"""Deploy CovenantGuard, returning the contract proxy.

	The deployed guard has **no** evidence claims. The ``guard`` fixture below
	adds the standard approved/clear claims; tests that need the missing-claim
	path should use this fixture directly.
	"""

	def _deploy(
		covenant: str = COVENANT,
		quorum: int = QUORUM,
		timelock: int = TIMELOCK_SECONDS,
		procurement: str = PROCUREMENT_HEX,
		risk: str = RISK_HEX,
		signer_a: str = SIGNER_A_HEX,
		signer_b: str = SIGNER_B_HEX,
	):
		reset_contract_registry()
		instance = direct_deploy(
			CONTRACT,
			covenant,
			quorum,
			timelock,
			procurement,
			risk,
			signer_a,
			signer_b,
			sdk_version=SDK_VERSION,
		)
		# After the deploy, not before: `deploy_contract` is what puts the GenVM
		# SDK on sys.path, and `genlayer` is not importable until it has run.
		# The constructor does not read the clock, so setting it here is safe.
		set_clock(T0)
		return instance

	return _deploy


@pytest.fixture
def guard(deploy_guard, direct_vm):
	"""Deploy a guard and attest the standard vendor as approved/clear.

	The claims are written through the real role-gated methods, with the sender
	switched to each attestor. The signers are *not* auto-approved: an approval
	is a property of one operation binding, so it belongs in the test that
	proposes that operation.
	"""
	instance = deploy_guard()

	from genlayer.py.types import Address

	original = direct_vm.sender
	direct_vm.sender = Address(PROCUREMENT_HEX)
	instance.attest_vendor(
		"fixture-vendor",
		VENDOR_HEX,
		"approved",
		CLAIM_VALID_UNTIL,
		SOURCE_URI,
		SOURCE_DIGEST,
	)
	direct_vm.sender = Address(RISK_HEX)
	instance.attest_risk(
		"fixture-risk",
		VENDOR_HEX,
		"clear",
		CLAIM_VALID_UNTIL,
		SOURCE_URI,
		SOURCE_DIGEST,
	)
	direct_vm.sender = original
	return instance


@pytest.fixture
def model():
	"""Build a model reply the way a real model would produce one.

	Prose plus a JSON object, not a bare JSON string: the contract uses
	``response_format='text'`` and does its own brace scan, so the mock layer's
	handling of bare JSON never comes into play.
	"""

	def make(payload: dict, prefix: str = "Assessment of the covenant:\n") -> str:
		import json

		return prefix + json.dumps(payload)

	return make


@pytest.fixture
def allow(model):
	return model({"verdict": "ALLOWED", "reason_code": "CITED_CLAUSE", "excerpt": EXCERPT_OK})


@pytest.fixture
def deny(model):
	return model({"verdict": "VIOLATED", "reason_code": "POLICY_DENIAL", "excerpt": EXCERPT_OK})


@pytest.fixture
def unsure(model):
	return model({"verdict": "AMBIGUOUS", "reason_code": "EVIDENCE_INSUFFICIENT"})