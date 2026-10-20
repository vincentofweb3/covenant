"""Shared fixtures for the direct-mode suite.

No network. The model is replaced by a fixed reply function, which proves the
contract's normalisation and state machine, not live model behaviour.
"""

import pytest
from gltest.direct import pytest_plugin  # noqa: F401  (registers direct_* fixtures)

from .direct_support import (  # noqa: F401  (re-exported for fixtures and tests)
	CONTRACT,
	COVENANT,
	COVENANT_V2,
	EXCERPT_FABRICATED,
	EXCERPT_OK,
	QUORUM,
	SDK_VERSION,
	T0,
	TIMELOCK_SECONDS,
	VAULT_HEX,
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
	"""Deploy CovenantGuard, returning the contract proxy."""

	def _deploy(covenant: str = COVENANT, quorum: int = QUORUM, timelock: int = TIMELOCK_SECONDS):
		reset_contract_registry()
		instance = direct_deploy(CONTRACT, covenant, quorum, timelock, sdk_version=SDK_VERSION)
		# After the deploy, not before: `deploy_contract` is what puts the GenVM
		# SDK on sys.path, and `genlayer` is not importable until it has run.
		# The constructor does not read the clock, so setting it here is safe.
		set_clock(T0)
		return instance

	return _deploy


@pytest.fixture
def guard(deploy_guard):
	return deploy_guard()


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