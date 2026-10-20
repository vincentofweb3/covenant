"""Phase 0 direct-mode probe tests. NOT the project's real suite.

Purpose: prove that the local toolchain can deploy, execute, mock the model, and
exercise the leader/validator boundary -- before any real contract code is
written. A failure here is cheap; the same failure after 1000 lines is not.

Every command and its verbatim output is recorded in state/PROJECT-STATE.md.
"""

import pytest
from gltest.direct import pytest_plugin  # noqa: F401  (registers direct_* fixtures)

from ._common import (
	CALLEE,
	CALLER,
	COVENANT,
	PEER_HEX,
	SDK_VERSION,
	_reset_contract_registry,
)


@pytest.fixture(autouse=True)
def _contract_registry_guard():
	_reset_contract_registry()
	yield
	_reset_contract_registry()


@pytest.fixture
def deploy_fresh(direct_deploy):
	"""Deploy, clearing the std lib's one-contract global first.

	The global is set when the contract *class* is created, so loading a second
	contract module in the same process raises even if the first instance is
	already alive.
	"""

	def _deploy(path: str, *args, **kwargs):
		_reset_contract_registry()
		return direct_deploy(path, *args, sdk_version=SDK_VERSION, **kwargs)

	return _deploy


@pytest.fixture
def callee(deploy_fresh):
	"""Deployed first on purpose: `deploy_contract` is what puts the SDK on
	sys.path, and nothing else in a direct-mode test does."""
	return deploy_fresh(CALLEE, "salt-1")


@pytest.fixture
def caller(deploy_fresh):
	return deploy_fresh(CALLER, PEER_HEX, COVENANT)