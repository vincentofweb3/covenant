"""Phase 0 glsim harness — NOT the project's real integration suite.

Direct mode cannot deliver cross-contract messages, so this is the only place
the two-contract round trip can actually be exercised. Adapted from a working
v0.6 harness, with the OLD std lib's differences checked rather than assumed.

Differences found while adapting:
  * the OLD std lib already emits the method name under the `"method"` key that
    glsim reads, so the calldata-key shim a v0.6 harness needs is NOT needed here
    (verified: `genlayer/gl/genvm_contracts.py::_make_calldata_obj` writes
    `ret['method']`; `glsim/engine.py:444` reads `cd.get("method")`);
  * the host/guest Address gap still applies, because glsim decodes RPC calldata
    with the host ABI and hands the result to guest contract code.

`chain_id` defaults to 61127 here, which is not a live network. That is correct
for a local simulator.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# glsim resolves its GenVM bundle tree from GENVM_VERSION. The OLD std lib
# (py-genlayer:1jb45aa8...) ships in v0.3.0-rc7.
os.environ.setdefault("GENVM_VERSION", "v0.3.0-rc7")

OWNER = "0x" + "11" * 20
OTHER = "0x" + "22" * 20

COVENANT = "No payment above 50000 without multisig approval."


class SimError(RuntimeError):
	"""A JSON-RPC level failure returned by glsim."""


def _apply_glsim_shims() -> None:
	"""Bridge glsim to the OLD std lib.

	1. Host -> guest argument types. glsim decodes RPC calldata with the host
	   ABI (`genlayer_py.CalldataAddress`) and passes those objects into contract
	   code, but the guest encoder in `genlayer.py.calldata` only understands
	   `genlayer.py.types.Address`. Rewriting at the `call_method` boundary covers
	   both RPC calls and drained cross-contract messages.
	2. Block datetime is handled in `Sim.sync_time` instead: glsim only syncs
	   sender/value/chain_id into `genlayer.message.raw`, so `raw["datetime"]`
	   would stay frozen at import.
	"""
	from glsim.engine import SimEngine

	original_call_method = SimEngine.call_method
	if not getattr(original_call_method, "_covenant_guest_type_shim", False):

		def to_guest(value):
			if isinstance(value, dict):
				return {key: to_guest(item) for key, item in value.items()}
			if isinstance(value, list):
				return [to_guest(item) for item in value]
			try:
				from genlayer_py.types.calldata import CalldataAddress
			except ImportError:
				return value
			if isinstance(value, CalldataAddress):
				from genlayer.py.types import Address

				return Address(value.as_bytes)
			return value

		def call_method(self, contract_address, method_name, args=None, kwargs=None, sender=None):
			# Imported lazily: the guest SDK resolves only once the engine has
			# activated and installed its paths.
			return original_call_method(
				self,
				contract_address,
				method_name,
				[to_guest(arg) for arg in (args or [])],
				{key: to_guest(value) for key, value in (kwargs or {}).items()},
				sender,
			)

		call_method._covenant_guest_type_shim = True  # type: ignore[attr-defined]
		SimEngine.call_method = call_method


def _reset_contract_registry() -> None:
	"""Clear the GenVM std lib's one-contract-per-process global.

	Same constraint as direct mode, different symptom: without this, the second
	`sim_deploy` of a contract class fails with
	`class is not marked for usage within storage, please, annotate it with
	@allow_storage`, because the engine re-enters the storage builder once
	`__known_contract__` is already populated.
	"""
	try:
		import genlayer.gl.genvm_contracts as contracts
	except ImportError:
		return
	contracts.__known_contract__ = None


class Sim:
	"""Thin JSON-RPC client over an in-process glsim app."""

	def __init__(self, client, store, engine):
		self.client = client
		self.store = store
		self.engine = engine
		self._request_id = 0

	def sync_time(self) -> None:
		"""Publish glsim's simulated clock as the contract block time."""
		message = sys.modules.get("genlayer.message")
		raw = getattr(message, "raw", None)
		if isinstance(raw, dict):
			raw["datetime"] = self.store.get_effective_datetime()

	def rpc(self, method: str, params=None):
		self.sync_time()
		_reset_contract_registry()
		self._request_id += 1
		response = self.client.post(
			"/api",
			json={
				"jsonrpc": "2.0",
				"id": self._request_id,
				"method": method,
				"params": params if params is not None else [],
			},
		)
		payload = response.json()
		error = payload.get("error")
		if error:
			raise SimError(f"{method}: {error.get('message')}")
		return payload.get("result")

	@staticmethod
	def typed(value):
		"""Encode a hex address as the host ABI type the guest expects.

		A bare JSON string reaches the contract as `str` and fails at the
		storage setter with `'str' object has no attribute 'as_bytes'`.
		"""
		if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
			from genlayer_py.types.calldata import CalldataAddress

			return CalldataAddress(value)
		return value

	def deploy(self, code_path: str, args=(), sender: str = OWNER) -> str:
		# `sim_deploy` accepts JSON arguments only. Address arguments must
		# therefore be converted inside the contract's __init__; a host-ABI
		# CalldataAddress here fails with `not JSON serializable`.
		return self.rpc(
			"sim_deploy",
			{"code_path": code_path, "args": list(args), "kwargs": {}, "sender": sender},
		)["contract_address"]

	def call(self, to: str, method: str, args=(), sender: str = OWNER):
		"""Write that accepts plain JSON arguments."""
		result = self.rpc(
			"sim_call",
			{"to": to, "method": method, "args": list(args), "kwargs": {}, "sender": sender},
		)
		return result["result"] if isinstance(result, dict) else result

	def write(self, to: str, method: str, args=(), sender: str = OWNER):
		"""Write for methods taking addresses; persists state.

		A bare JSON address string reaches the storage setter as `str` and fails,
		so this encodes through the typed host ABI and goes via `gen_call`.
		"""
		import rlp
		from genlayer_py.abi import calldata
		from genlayer_py.types.calldata import CalldataAddress

		def typed(value):
			if isinstance(value, str) and value.startswith("0x") and len(value) == 42:
				return CalldataAddress(value)
			return value

		encoded = calldata.encode({"method": method, "args": [typed(a) for a in args], "kwargs": {}})
		data = "0x" + rlp.encode([encoded, b"\x00"]).hex()
		raw = self.rpc("gen_call", [{"type": "write", "to": to, "from": sender, "data": data}])
		return calldata.decode(bytes.fromhex(raw))

	def read(self, to: str, method: str, args=()):
		result = self.rpc(
			"sim_read", {"to": to, "method": method, "args": list(args), "kwargs": {}}
		)
		return result["result"] if isinstance(result, dict) else result

	def install_mocks(self, llm_mocks: dict, web_mocks: dict | None = None, strict: bool = True) -> None:
		from glsim import server as glsim_server

		self.engine._persistent_llm_mocks = dict(llm_mocks)
		self.engine._persistent_web_mocks = dict(web_mocks or {})
		self.engine.vm._strict_mock_mode = bool(strict)
		glsim_server._clear_sim_config_mocks(self.engine)

	def set_time(self, iso_datetime: str) -> None:
		self.rpc("sim_setTime", {"datetime": iso_datetime})
		self.sync_time()


@pytest.fixture
def sim():
	"""Function-scoped on purpose.

	Session scope makes glsim's engine reuse a cached contract class on the
	second deploy (`engine.py:202-206`), and that path re-allocates storage
	without the setup `deploy_contract` performs, failing with
	`class is not marked for usage within storage`. A fresh engine per test
	costs some milliseconds and avoids the whole problem.
	"""
	pytest.importorskip("glsim", reason="integration tests need the glsim local network")
	pytest.importorskip("starlette.testclient", reason="integration tests need starlette + httpx")
	_apply_glsim_shims()

	from glsim.server import create_app
	from starlette.testclient import TestClient

	app = create_app(
		num_validators=1,
		max_rotations=1,
		llm_provider=None,
		use_browser=False,
		verbose=False,
		seed="covenant-probe",
	)
	with TestClient(app) as client:
		yield Sim(client, app.state.store, app.state.engine)


@pytest.fixture(scope="session")
def caller_path() -> str:
	return str(REPO_ROOT / "probe" / "probe_caller.py")


@pytest.fixture(scope="session")
def callee_path() -> str:
	return str(REPO_ROOT / "probe" / "probe_callee.py")