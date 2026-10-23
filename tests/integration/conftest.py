"""Integration harness: drive the cross-contract lifecycle on GenLayer Sim.

Direct mode cannot deliver cross-contract messages, so the enforcement claim of
this project -- that the vault refuses to move funds without a verified ruling --
is only provable here. Everything in `tests/direct/` about the guard's internals
is meaningless without this file.

One shim is needed to bridge glsim to the pinned std lib: glsim decodes RPC
calldata with the host ABI and hands `genlayer_py` calldata objects to guest
contract code, but the guest encoder only understands `genlayer.py.types.Address`.
The shim rewrites at the `call_method` boundary.

The calldata-key shim a v0.6 harness needs is **not** required here: the OLD std
lib already writes the emitted method name under the `"method"` key glsim reads
(`genlayer/gl/genvm_contracts.py::_make_calldata_obj`).
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

GUARD_PATH = str(REPO_ROOT / "contracts" / "covenant_guard.py")
VAULT_PATH = str(REPO_ROOT / "contracts" / "treasury_vault.py")

OWNER = "0x" + "11" * 20
PROPOSER = "0x" + "33" * 20
RECIPIENT = "0x" + "44" * 20

# Fixed evidence roles, distinct from the owner/proposer/recipient above.
PROCUREMENT = "0x" + "55" * 20
RISK = "0x" + "66" * 20
SIGNER_A = "0x" + "77" * 20
SIGNER_B = "0x" + "88" * 20

T0 = "2026-01-01T00:00:00+00:00"
CLAIM_VALID_UNTIL = 1796083200  # 2026-12-01T00:00:00Z
SOURCE_URI = "https://risk.example.test/vendors/" + RECIPIENT[2:]
SOURCE_DIGEST = "ab" * 32

COVENANT = (
	"Clause 1: The approved vendor list is maintained by the risk desk.\n"
	"Clause 2: Recurring monthly payments to an approved vendor are permitted.\n"
	"Clause 3: No single payment above 50000 may be released without multisig "
	"approval from two distinct signers.\n"
	"Clause 4: Any counterparty not on the approved vendor list is prohibited."
)

COVENANT_V2 = (
	"Clause 1: The approved vendor list is maintained by the risk desk.\n"
	"Clause 2: Recurring monthly payments to an approved vendor are permitted.\n"
	"Clause 3: No single payment above 250000 may be released without multisig "
	"approval from two distinct signers.\n"
	"Clause 4: Any counterparty not on the approved vendor list is prohibited."
)

EXCERPT_OK = "Recurring monthly payments to an approved vendor are permitted"
EXCERPT_FABRICATED = "Payments below 50000 require no multisig approval at all"

QUORUM = 2
TIMELOCK = 3600

class SimError(RuntimeError):
	"""A JSON-RPC level failure returned by glsim."""


def reset_contract_registry() -> None:
	"""Clear the GenVM std lib's one-contract-per-process global.

	glsim caches contract classes and re-allocates them on a second deploy,
	which fails with `class is not marked for usage within storage`. Same global
	as direct mode, different symptom. See state/MEMORY.md §12.
	"""
	try:
		import genlayer.gl.genvm_contracts as contracts
	except ImportError:
		return
	contracts.__known_contract__ = None


def _install_validator_capture_shim() -> None:
	"""Make glsim actually run the validator half of the equivalence principle.

	**This is the most important shim in the file, and it closes a real gap in
	the simulator rather than a version skew.**

	The pinned std lib sends both halves:

	```python
	{'RunNondet': {'data_leader': dumps(real_leader_fn),
	               'data_validator': dumps(real_validator_fn)}}
	```

	But `gltest.direct.wasi_mock._handle_run_nondet` reads only `data_leader`,
	runs it, and returns. `data_validator` is silently dropped, so
	`vm._captured_validators` stays empty, so `glsim.consensus.run_consensus` takes
	its `elif not captured:` branch and returns `votes = ["agree"] * n` without
	consulting a single validator.

	Consequence: **stock glsim cannot demonstrate validator disagreement for the
	in-VM `run_nondet` seam at all.** It will finalize a transaction whose
	validators would all reject it. Any claim that fail-closed behaviour is
	"tested on the simulator" is false unless this shim is installed.

	The shim captures the validator and adapts the calling convention: the std
	lib's `real_validator_fn` expects a `stage_data` dict holding the *encoded*
	leader result, whereas `run_consensus._run_validators` hands the validator a
	decoded `gl.vm.Return`. The adapter re-encodes for the std lib's closure.

	Every import here is lazy. `genlayer.py.calldata` and `cloudpickle` live
	inside the GenVM std lib and are not importable until the first contract
	deploy, so neither exists yet at fixture setup time.

	Applied only to the multi-validator fixture. The single-validator lifecycle
	suite deliberately runs stock, because there is no split to simulate there.
	"""
	from gltest.direct import wasi_mock

	original = wasi_mock._handle_run_nondet
	if getattr(original, "_covenant_validator_capture", False):
		return

	def shim(vm, data):
		import struct

		import cloudpickle
		from genlayer.py import calldata

		marker = wasi_mock._DIRECT_MARKER
		registry = getattr(cloudpickle, "_glsim_direct_registry", None)
		if registry is None:
			raise RuntimeError(
				"cloudpickle._glsim_direct_registry missing; the shim cannot resolve "
				"leader closures and the harness cannot demonstrate validator disagreement"
			)

		data_leader = data.get("data_leader")
		if not data_leader:
			raise ValueError("RunNondet missing data_leader")

		if data_leader[: len(marker)] == marker:
			key = struct.unpack("!Q", data_leader[len(marker):])[0]
			leader_fn = registry.pop(key)
		else:
			leader_fn = cloudpickle.loads(data_leader)

		import os as _os
		if _os.environ.get("COVENANT_SHIM_DEBUG"):
			print("[shim] RunNondet data keys:", sorted(data.keys()))

		data_validator = data.get("data_validator")
		validator_fn = cloudpickle.loads(data_validator) if data_validator else None
		if _os.environ.get("COVENANT_SHIM_DEBUG"):
			print("[shim] validator captured:", validator_fn is not None)

		vm._in_nondet = True
		try:
			result = leader_fn(None)
		except Exception as exc:
			return bytes([1]) + str(exc).encode("utf-8")
		finally:
			vm._in_nondet = False

		if validator_fn is not None:

			def adapted(stored_result):
				# `real_validator_fn` decodes stage_data["leaders_result"], which
				# is the encoded form: a zero code byte then the calldata.
				payload = bytes([0]) + calldata.encode(stored_result)
				return validator_fn({"leaders_result": payload})

			vm._captured_validators.append((result, leader_fn, adapted))

		return bytes([0]) + calldata.encode(result)

	shim._covenant_validator_capture = True  # type: ignore[attr-defined]
	wasi_mock._handle_run_nondet = shim


def _apply_glsim_shims() -> None:
	"""Rewrite host-ABI address objects into guest Address objects."""
	from glsim.engine import SimEngine

	original_call_method = SimEngine.call_method
	if getattr(original_call_method, "_covenant_guest_type_shim", False):
		return

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


class Sim:
	"""Thin JSON-RPC client over an in-process glsim app."""

	def __init__(self, client, store, engine):
		self.client = client
		self.store = store
		self.engine = engine
		self._request_id = 0

	def sync_time(self) -> None:
		"""Publish glsim's simulated clock as the contract block time.

		glsim syncs only sender/value/chain_id into `message.raw`, so the
		`datetime` the contract reads would stay frozen at import. See
		state/MEMORY.md §13.

		The module is reached through `sys.modules`, and the OLD std lib's key
		was checked rather than assumed: it has no `genlayer/message.py`, so
		`genlayer.message` does not exist here. `message_raw` lives on
		`genlayer.gl`, which re-exports `genlayer._internal.msg`.
		"""
		for module_name in ("genlayer.message", "genlayer.gl"):
			module = sys.modules.get(module_name)
			if module is None:
				continue
			raw = getattr(module, "raw", None)
			if raw is None:
				raw = getattr(module, "message_raw", None)
			if isinstance(raw, dict):
				raw["datetime"] = self.store.get_effective_datetime()
				return

		# Before the first deploy nothing has imported the guest SDK, so there is
		# nothing to publish to. This is expected, not a failure: the clock is
		# re-published before every RPC call, and the first contract to read it
		# does so after a deploy has loaded the SDK.
		if "genlayer" not in sys.modules:
			return

		raise RuntimeError(
			"genlayer is loaded but message_raw was not found in genlayer.message or genlayer.gl"
		)

	def rpc(self, method: str, params=None):
		self.sync_time()
		reset_contract_registry()
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

	def deploy(self, code_path: str, args=(), sender: str = OWNER) -> str:
		# `sim_deploy` accepts JSON arguments only; address parameters are
		# converted inside the contract's __init__ for that reason.
		return self.rpc(
			"sim_deploy",
			{"code_path": code_path, "args": list(args), "kwargs": {}, "sender": sender},
		)["contract_address"]

	def call(self, to: str, method: str, args=(), sender: str = OWNER):
		result = self.rpc(
			"sim_call",
			{"to": to, "method": method, "args": list(args), "kwargs": {}, "sender": sender},
		)
		return result["result"] if isinstance(result, dict) else result

	def write(self, to: str, method: str, args=(), sender: str = OWNER):
		"""Typed write for methods that take addresses; persists state."""
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

	def install_llm_handler(self, handler) -> None:
		"""Install a callable invoked when no LLM mock matches.

		This is the only way to make validators actually disagree under glsim.
		`_run_validators` re-runs the same captured closure once per validator, and
		static mocks are pattern -> response and never consumed, so every call
		returns the same text and every validator agrees. A handler with call
		state gives the leader one output and the validators another, which is
		what a real split looks like.

		Two things about this are easy to get wrong:

		* it is set on the **engine**, not the VM. `_install_live_handlers` copies
		  `engine._llm_handler` onto `vm._live_llm_handler` on every activation,
		  so a handler assigned to the VM is silently replaced and never runs;
		* the handler returns wasi-shaped `{"ok": <value>}`, not the bare value.

		Mock matching is tried *before* the handler, so install no matching mock.
		"""
		self.engine._llm_handler = handler
		self.engine.vm._live_llm_handler = handler

	def receipt(self, tx_hash: str):
		return self.rpc("sim_getTransactionByHash", {"transaction_hash": tx_hash})

	def set_time(self, iso_datetime: str) -> None:
		self.rpc("sim_setTime", {"datetime": iso_datetime})
		self.sync_time()


@pytest.fixture
def sim():
	"""Function-scoped.

	Session scope makes glsim reuse a cached contract class on the second
	deploy, which re-allocates storage without the setup `deploy_contract`
	performs and fails. A fresh engine per test costs milliseconds.
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
		seed="covenant-integration",
	)
	with TestClient(app) as client:
		instance = Sim(client, app.state.store, app.state.engine)
		instance.set_time(T0)
		yield instance


def model(payload: dict, prefix: str = "Assessment of the covenant:\n") -> str:
	"""Prose plus JSON, which is what `response_format='text'` expects."""
	return prefix + json.dumps(payload)


def allow_ok() -> str:
	return model({"verdict": "ALLOWED", "reason_code": "CITED_CLAUSE", "excerpt": EXCERPT_OK})


def deny_ok() -> str:
	return model({"verdict": "VIOLATED", "reason_code": "POLICY_DENIAL", "excerpt": EXCERPT_OK})


def unsure_ok() -> str:
	return model({"verdict": "AMBIGUOUS", "reason_code": "EVIDENCE_INSUFFICIENT"})


def allow_fabricated() -> str:
	return model(
		{"verdict": "ALLOWED", "reason_code": "CITED_CLAUSE", "excerpt": EXCERPT_FABRICATED}
	)


def write_standard_claims(sim, guard, action_prefix="fixture"):
	"""Write the standard approved/clear claims for the test recipient."""
	sim.call(
		guard,
		"attest_vendor",
		[
			action_prefix + "-vendor",
			RECIPIENT,
			"approved",
			CLAIM_VALID_UNTIL,
			SOURCE_URI,
			SOURCE_DIGEST,
		],
		sender=PROCUREMENT,
	)
	sim.call(
		guard,
		"attest_risk",
		[
			action_prefix + "-risk",
			RECIPIENT,
			"clear",
			CLAIM_VALID_UNTIL,
			SOURCE_URI,
			SOURCE_DIGEST,
		],
		sender=RISK,
	)


def deploy_guard(sim, covenant: str = COVENANT, prefix: str = "deploy") -> str:
	guard = sim.deploy(
		GUARD_PATH,
		[covenant, QUORUM, TIMELOCK, PROCUREMENT, RISK, SIGNER_A, SIGNER_B],
	)
	write_standard_claims(sim, guard, prefix)
	return guard


@pytest.fixture
def deployed(sim):
	"""Guard + vault, registered, funded, with standard evidence on record."""
	guard = deploy_guard(sim, prefix="fixture-a")
	vault = sim.deploy(VAULT_PATH, [guard, OWNER])

	sim.call(guard, "register_vault", ["reg-1", vault])
	sim.call(vault, "deposit", ["dep-1", 1_000_000])
	return {"guard": guard, "vault": vault}


def request(sim, vault, operation_id, amount, memo, sender=PROPOSER):
	"""Ask the vault for a payment; it forwards the bound payload to the guard."""
	sim.call(
		vault,
		"request_payment",
		["req-" + operation_id, operation_id, RECIPIENT, amount, memo],
		sender=sender,
	)


def rule(sim, guard, operation_id, reply):
	"""Judge and finalize one operation under a fixed model reply."""
	sim.install_mocks({".*": reply})
	sim.call(guard, "judge_operation", ["judge-" + operation_id, operation_id])
	sim.call(guard, "finalize_operation", ["final-" + operation_id, operation_id])

@pytest.fixture
def sim_multi():
	"""A three-validator engine.

	`run_consensus` counts only validator votes -- the leader is not a vote -- so
	with `num_validators=3` a majority is 2 of 3. `max_rotations=1` means a
	disagreement is not retried with a new leader, which is the fail-closed
	behaviour under test.
	"""
	pytest.importorskip("glsim")
	pytest.importorskip("starlette.testclient")
	_apply_glsim_shims()
	_install_validator_capture_shim()

	from glsim.server import create_app
	from starlette.testclient import TestClient

	app = create_app(
		num_validators=3,
		max_rotations=1,
		llm_provider=None,
		use_browser=False,
		verbose=False,
		seed="covenant-consensus",
	)
	with TestClient(app) as client:
		instance = Sim(client, app.state.store, app.state.engine)
		instance.set_time(T0)
		yield instance


@pytest.fixture
def deployed_multi(sim_multi):
	guard = deploy_guard(sim_multi, prefix="fixture-m")
	vault = sim_multi.deploy(VAULT_PATH, [guard, OWNER])
	sim_multi.call(guard, "register_vault", ["reg-m", vault])
	sim_multi.call(vault, "deposit", ["dep-m", 1_000_000])
	return {"guard": guard, "vault": vault}


def install_validator_capture(sim, model) -> "DivergentModel":
	"""Point the simulator at a stateful model so validators can genuinely differ.

	Installs on the **engine**: `_install_live_handlers` copies
	`engine._llm_handler` onto the VM on every activation, so a handler assigned
	to `vm._live_llm_handler` alone is silently replaced and never runs. The
	return value must be wasi-shaped `{"ok": <value>}`, matching glsim's own
	live-handler contract.

	No mock may match, so callers pass `strict=False` and no patterns.
	"""
	sim.install_mocks({}, strict=False)
	sim.engine._llm_handler = model
	sim.engine.vm._live_llm_handler = model
	return model


class DivergentModel:
	"""A stand-in model that can disagree with itself across calls.

	Call 0 is the leader. Calls 1..n are validators re-deriving independently.
	Returning different text is what a genuine validator split looks like to the
	contract.

	`validator_switch_at` selects the call index from which `validator_reply` is
	used, so a *partial* split (some agree, some dissent) can be expressed.
	"""

	def __init__(self, leader_reply: str, validator_reply: str, validator_switch_at: int = 1):
		self.leader_reply = leader_reply
		self.validator_reply = validator_reply
		self.validator_switch_at = validator_switch_at
		self.calls = 0
		self.seen: list = []

	def __call__(self, data):
		reply = (
			self.leader_reply
			if self.calls < self.validator_switch_at
			else self.validator_reply
		)
		self.calls += 1
		self.seen.append(data.get("prompt", ""))
		return {"ok": reply}

	@property
	def prompts(self) -> list:
		return list(self.seen)
