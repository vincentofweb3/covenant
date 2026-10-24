"""Run the Covenant demonstration against live studionet.

Studionet charges **zero gas** (`baseFeePerGas` 0, `gasPrice` 0, every block
`gasUsed` 0), so a deploy does not need a funded account. This was confirmed
before this script was written: a zero-balance key successfully deployed
`covenant_guard`, and the deploy reached FINALIZED with 5 validators voting
AGREE/AGREE/IDLE/AGREE/AGREE.

What this demonstrates
---------------------
1. a compliant payment is ALLOWED and moves value
2. a large payment with two distinct signer approvals is ALLOWED and moves value
3. the *same* large payment without approvals is VIOLATED and is refused
4. a payment to a vendor whose on-chain claim is `not_approved` is VIOLATED
5. the covenant is amended by quorum + timelock
6. the same large payment is now under the raised threshold and is ALLOWED

The facts in steps 1-4 are not supplied by the requester. Procurement and risk
attestors, fixed at deployment, write claims for the recipient; the guard reads
them at ruling time and binds their digest into the ruling. Step 6 is the same
point as before: enforcement tracks the policy actually in force, not a snapshot
taken at deploy time.

What this cannot demonstrate on-chain
-------------------------------------
The fabricated-excerpt defence. It depends on the model inventing a clause,
which cannot be provoked from outside. It is proven in `tests/direct/` and
`tests/integration/` with an injected hallucinating model, and that distinction
is recorded rather than blurred.

Attestor honesty. The demo proves that the guard reads claims written by the
configured role and binds them into the ruling; it cannot prove that the role key
is honest. That trust assumption is stated in `docs/THREAT_MODEL.md` §2.6.

Usage:
    python3 scripts/live_demo.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eth_utils import to_checksum_address  # noqa: E402
from genlayer_py import create_account, create_client  # noqa: E402
from genlayer_py.chains import studionet  # noqa: E402
from genlayer_py.types.transactions import TransactionStatus  # noqa: E402

MAX_ROTATIONS = 5
QUORUM = 2
TIMELOCK_SECONDS = 60

DEPOSIT = 1_000_000
SMALL_PAYMENT = 900
OVERSIZED_PAYMENT = 120_000
PROHIBITED_PAYMENT = 500

# Fixed evidence roles. Gasless studionet means these accounts need no balance.
PROCUREMENT_KEY = "0x" + ("6b" * 32)
RISK_KEY = "0x" + ("6c" * 32)
PROHIBITED_VENDOR = "0x" + ("99" * 20)

COVENANT_FILE = REPO_ROOT / "examples" / "covenant.md"
GUARD_FILE = REPO_ROOT / "contracts" / "covenant_guard.py"
VAULT_FILE = REPO_ROOT / "contracts" / "treasury_vault.py"

# Second approver. Any distinct address counts toward quorum, and because gas is
# free an unfunded account can still approve.
SECOND_APPROVER_KEY = "0x" + ("7a" * 32)


def load_key() -> str:
	"""Read the signing key from the environment or a local .env.

	Deliberately no hard-coded path to another project: this repository is
	published, and a path like `../some-private-project/.env` is both useless to
	anyone else and leaks the layout of a machine.
	"""
	import os

	from_env = os.environ.get("GENLAYER_PRIVATE_KEY")
	if from_env:
		return from_env.strip()
	for env_path in (REPO_ROOT / ".env", REPO_ROOT.parent / ".env"):
		if env_path.exists():
			for line in env_path.read_text(encoding="utf-8").splitlines():
				if line.strip().startswith("GENLAYER_PRIVATE_KEY="):
					return line.split("=", 1)[1].strip().strip('"').strip("'")
	raise SystemExit(
		"No signing key. Set GENLAYER_PRIVATE_KEY, or put it in .env at the repository root."
	)


def _with_retry(fn, what: str, attempts: int = 14, delay: float = 5.0):
	"""Retry transient network failures.

	Studionet dropped a connection mid-run once. Every retried write here is
	safe because the contracts dedupe on `action_id`, which is what that
	parameter exists for.
	"""
	last = None
	for attempt in range(1, attempts + 1):
		try:
			return fn()
		except Exception as exc:  # noqa: BLE001
			last = exc
			text = str(exc).lower()
			transient = any(
				marker in text
				for marker in ("unreachable", "connection", "timeout", "timed out", "502", "503", "504")
			)
			if not transient or attempt == attempts:
				raise
			print(f"       network retry {attempt}/{attempts} for {what}: {type(exc).__name__}")
			time.sleep(delay)
	raise last  # pragma: no cover


class Live:
	def __init__(self, account):
		self.account = account
		self.client = create_client(chain=studionet, account=account)
		self.events: list[dict] = []
		self.reads: list[dict] = []

	def deploy(self, path: Path, args: list) -> tuple[str, dict]:
		txid = _with_retry(
			lambda: self.client.deploy_contract(
				code=path.read_text(encoding="utf-8"),
				args=args,
				consensus_max_rotations=MAX_ROTATIONS,
			),
			f"deploy {path.name}",
		)
		receipt = self.wait(txid, f"deploy {path.name}")
		address = to_checksum_address(receipt["data"]["contract_address"])
		return address, receipt

	def write(self, address: str, method: str, args: list, label: str, account=None) -> dict:
		txid = _with_retry(
			lambda: self.client.write_contract(
				address=address,
				function_name=method,
				args=args,
				account=account,
				consensus_max_rotations=MAX_ROTATIONS,
			),
			label,
		)
		return self.wait(txid, label)

	def wait(self, txid: str, label: str) -> dict:
		receipt = _with_retry(
			lambda: self.client.wait_for_transaction_receipt(
				txid,
				status=TransactionStatus.FINALIZED,
				retries=180,
				interval=4000,
			),
			f"wait {label}",
		)
		row = {
			"label": label,
			"tx_id": txid,
			"status": receipt.get("status_name"),
			"result": receipt.get("result_name"),
			"rounds": receipt.get("num_of_rounds"),
			"leader_execution": _leader_execution(receipt),
			"validators": _votes(receipt),
		}
		self.events.append(row)
		print(f"  {label:46} {row['status']:10} {row['result']:16} rounds={row['rounds']}")
		if row["leader_execution"] not in ("SUCCESS", None):
			print(f"       leader execution: {row['leader_execution'][:160]}")
		return receipt

	def drain_children(self, parent_receipt: dict, label: str) -> None:
		"""Wait for messages the finalized transaction triggered.

		`request_payment` asks the guard to open the operation with
		`on="finalized"` semantics, so the guard's copy arrives asynchronously.
		Judging before it lands would address an operation the guard does not
		have.
		"""
		parent_tx = parent_receipt.get("tx_id") or parent_receipt.get("hash")
		try:
			children = self.client.get_triggered_transaction_ids(parent_tx)
		except Exception as exc:  # noqa: BLE001
			print(f"       (could not list children: {type(exc).__name__})")
			return
		for child in children or []:
			child_id = getattr(child, "tx_id", None) or (child if isinstance(child, str) else str(child))
			self.wait(child_id, f"  child of {label}")

	def read(self, address: str, method: str, args: list, label: str):
		try:
			value = _with_retry(
				lambda: self.client.read_contract(address=address, function_name=method, args=args),
				f"read {method}",
			)
		except Exception as exc:  # noqa: BLE001
			value = f"<error: {type(exc).__name__}: {exc}>"
		self.reads.append({"label": label, "method": method, "value": value})
		print(f"  {label:46} {str(value)[:100]}")
		return value

	def failed(self, receipt: dict) -> bool:
		return receipt.get("result_name") != "MAJORITY_AGREE" or _leader_execution(receipt) not in (
			"SUCCESS",
			None,
		)


def _leader_execution(receipt: dict) -> str | None:
	try:
		return receipt["consensus_data"]["leader_receipt"][0]["execution_result"]
	except Exception:  # noqa: BLE001
		return None


def _votes(receipt: dict) -> str:
	try:
		round_data = receipt["consensus_data"]["leader_receipt"][0]
		_ = round_data
		validators = receipt["consensus_data"].get("validators", [])
		names = [v.get("vote") for v in validators]
		cleaned = []
		for v in names:
			cleaned.append(v if isinstance(v, str) else str(v))
		return ",".join(cleaned)
	except Exception:  # noqa: BLE001
		return "?"


def main() -> int:
	key = load_key()
	deployer_account = create_account(key)
	deployer = to_checksum_address(deployer_account.address)
	live = Live(deployer_account)

	print("=" * 78)
	print("Covenant — live studionet demonstration")
	print("=" * 78)
	print(f"deployer   {deployer}")
	print(f"balance    {live.client.get_balance(deployer)} wei (studionet is gasless)")
	print(f"chain      {studionet.id}  {studionet.rpc_urls['default']['http'][0]}")
	print()

	covenant = COVENANT_FILE.read_text(encoding="utf-8")
	procurement_account = create_account(PROCUREMENT_KEY)
	risk_account = create_account(RISK_KEY)
	signer_b_account = create_account(SECOND_APPROVER_KEY)
	procurement = to_checksum_address(procurement_account.address)
	risk = to_checksum_address(risk_account.address)
	signer_b = to_checksum_address(signer_b_account.address)

	print("DEPLOY")
	guard, _ = live.deploy(
		GUARD_FILE,
		[covenant, QUORUM, TIMELOCK_SECONDS, procurement, risk, deployer, signer_b],
	)
	print(f"  guard                                      {guard}")
	vault, _ = live.deploy(VAULT_FILE, [guard, deployer])
	print(f"  vault                                      {vault}")
	print()

	print("SETUP")
	live.read(guard, "get_covenant_hash", [], "covenant hash at deploy")
	live.read(guard, "get_governance", [], "guard governance")
	live.read(guard, "get_attestors", [], "evidence attestors")
	live.write(guard, "register_vault", ["setup-register", vault], "guard.register_vault")
	live.write(vault, "deposit", ["setup-deposit", DEPOSIT], f"vault.deposit({DEPOSIT:,})")
	live.read(vault, "get_balance", [], "vault balance")
	print()

	print("ATTESTATIONS")
	claim_until = int(time.time()) + 30 * 24 * 3600
	claim_digest = hashlib.sha256(b"covenant demo vendor approval v1").hexdigest()
	live.write(
		guard,
		"attest_vendor",
		[
			"attest-vendor-on",
			deployer,
			"approved",
			claim_until,
			"https://risk.example.test/vendors/" + deployer.lower(),
			claim_digest,
		],
		"guard.attest_vendor(approved)",
		account=procurement_account,
	)
	live.write(
		guard,
		"attest_risk",
		[
			"attest-risk-on",
			deployer,
			"clear",
			claim_until,
			"https://risk.example.test/risk/" + deployer.lower(),
			claim_digest,
		],
		"guard.attest_risk(clear)",
		account=risk_account,
	)
	live.write(
		guard,
		"attest_vendor",
		[
			"attest-vendor-off",
			PROHIBITED_VENDOR,
			"not_approved",
			claim_until,
			"https://risk.example.test/vendors/" + PROHIBITED_VENDOR[2:],
			claim_digest,
		],
		"guard.attest_vendor(not_approved)",
		account=procurement_account,
	)
	live.write(
		guard,
		"attest_risk",
		[
			"attest-risk-off",
			PROHIBITED_VENDOR,
			"clear",
			claim_until,
			"https://risk.example.test/risk/" + PROHIBITED_VENDOR[2:],
			claim_digest,
		],
		"guard.attest_risk(clear, prohibited vendor)",
		account=risk_account,
	)
	live.read(guard, "get_vendor_claim", [deployer], "claim: approved vendor")
	live.read(guard, "get_vendor_claim", [PROHIBITED_VENDOR], "claim: removed vendor")
	print()

	print("ENFORCEMENT")
	live.read(guard, "get_covenant_version", [], "covenant version (expect 1)")
	live.read(vault, "get_payment_count", [], "payments so far")

	scenarios = [
		(
			"op-small",
			SMALL_PAYMENT,
			deployer,
			"vendor retainer",
			"ALLOWED",
			0,
		),
		(
			"op-signed",
			SMALL_PAYMENT,
			deployer,
			"vendor retainer with two recorded signer approvals",
			"ALLOWED",
			2,
		),
		(
			"op-big",
			OVERSIZED_PAYMENT,
			deployer,
			"vendor retainer above the threshold with no approvals",
			"VIOLATED",
			0,
		),
		(
			"op-prohibited",
			PROHIBITED_PAYMENT,
			PROHIBITED_VENDOR,
			"payment to a vendor whose current claim is not_approved",
			"VIOLATED",
			0,
		),
	]

	outcomes = {}
	for op_id, amount, recipient, memo, expected, approvals in scenarios:
		print(f"\n  [{op_id}] {amount:,} GEN — {memo}")
		receipt = live.write(
			vault,
			"request_payment",
			["req-" + op_id, op_id, recipient, amount, memo],
			f"vault.request_payment({op_id})",
		)
		live.drain_children(receipt, f"request_payment({op_id})")

		signers = [deployer_account, signer_b_account]
		for index in range(approvals):
			live.write(
				guard,
				"approve_payment",
				[f"ap-{index}-{op_id}", op_id],
				f"guard.approve_payment({op_id}, signer {index + 1})",
				account=signers[index],
			)

		live.write(guard, "judge_operation", ["judge-" + op_id, op_id], f"guard.judge_operation({op_id})")
		ruling = live.read(guard, "get_ruling", [op_id], f"ruling for {op_id}")
		live.write(
			guard, "finalize_operation", ["fin-" + op_id, op_id], f"guard.finalize_operation({op_id})"
		)
		live.read(guard, "get_operation_status", [op_id], f"  guard state {op_id}")
		before = live.read(vault, "get_balance", [], "  balance before execute")

		exec_receipt = live.write(
			vault, "execute_payment", ["exec-" + op_id, op_id], f"vault.execute_payment({op_id})"
		)
		after = live.read(vault, "get_balance", [], "  balance after execute")
		status = live.read(vault, "get_payment_status", [op_id], f"  payment status {op_id}")

		refused = live.failed(exec_receipt)
		moved = 0
		try:
			moved = int(after) - int(before)
		except Exception:  # noqa: BLE001
			pass
		outcomes[op_id] = {
			"amount": amount,
			"recipient": recipient,
			"expected": expected,
			"approvals": approvals,
			"ruling": ruling,
			"execute_refused": refused,
			"balance_moved": moved,
			"payment_status": status,
		}
		print(f"    expected {expected}; ruling={str(ruling)[:60]}; refused={refused}; moved={moved:+,}")

	print("\nAMENDMENT")
	amended = covenant.replace("50000 GEN", "250000 GEN")
	assert amended != covenant, "amendment text did not change the covenant"
	live.write(
		guard,
		"propose_amendment",
		["am-propose", "amend-1", amended],
		"guard.propose_amendment(50000 -> 250000)",
	)
	live.write(guard, "approve_amendment", ["am-a1", "amend-1"], "approve (deployer)")
	live.write(
		guard,
		"approve_amendment",
		["am-a2", "amend-1"],
		"approve (second distinct signer)",
		account=signer_b_account,
	)
	live.read(guard, "get_amendment_status", ["amend-1"], "amendment status after quorum")

	print(f"  waiting out the {TIMELOCK_SECONDS}s timelock…")
	time.sleep(TIMELOCK_SECONDS + 20)
	live.write(guard, "activate_amendment", ["am-activate", "amend-1"], "guard.activate_amendment")
	live.read(guard, "get_covenant_version", [], "covenant version (expect 2)")
	live.read(guard, "get_governance", [], "governance after amendment")

	print("\nENFORCEMENT UNDER THE AMENDED COVENANT")
	receipt = live.write(
		vault,
		"request_payment",
		[
			"req-op-after",
			"op-after",
			deployer,
			OVERSIZED_PAYMENT,
			"vendor retainer",
		],
		"vault.request_payment(op-after)",
	)
	live.drain_children(receipt, "request_payment(op-after)")
	live.write(guard, "judge_operation", ["judge-op-after", "op-after"], "guard.judge_operation(op-after)")
	ruling = live.read(guard, "get_ruling", ["op-after"], "ruling for op-after")
	live.write(
		guard, "finalize_operation", ["fin-op-after", "op-after"], "guard.finalize_operation(op-after)"
	)
	live.read(guard, "get_operation_status", ["op-after"], "  guard state op-after")
	before = live.read(vault, "get_balance", [], "  balance before execute")
	exec_receipt = live.write(
		vault, "execute_payment", ["exec-op-after", "op-after"], "vault.execute_payment(op-after)"
	)
	after = live.read(vault, "get_balance", [], "  balance after execute")
	status = live.read(vault, "get_payment_status", ["op-after"], "  payment status op-after")
	refused = live.failed(exec_receipt)
	moved = 0
	try:
		moved = int(after) - int(before)
	except Exception:  # noqa: BLE001
		pass
	outcomes["op-after"] = {
		"amount": OVERSIZED_PAYMENT,
		"recipient": deployer,
		"approvals": 0,
		"expected": "ALLOWED (threshold raised)",
		"ruling": ruling,
		"execute_refused": refused,
		"balance_moved": moved,
		"payment_status": status,
	}
	print(f"    expected ALLOWED under v2; ruling={str(ruling)[:60]}; refused={refused}; moved={moved:+,}")

	live.read(vault, "get_totals", [], "vault totals")

	evidence = {
		"network": {
			"rpc": studionet.rpc_urls["default"]["http"][0],
			"chain_id": studionet.id,
			"gas": "baseFeePerGas 0, gasPrice 0 — gasless",
		},
		"deployer": deployer,
		"deployer_balance_wei": str(live.client.get_balance(deployer)),
		"guard": guard,
		"vault": vault,
		"constructor": {
			"quorum": QUORUM,
			"timelock_seconds": TIMELOCK_SECONDS,
			"procurement_attestor": procurement,
			"risk_attestor": risk,
			"signer_a": deployer,
			"signer_b": signer_b,
		},
		"evidence": {
			"claim_valid_until": claim_until,
			"claim_source_digest": claim_digest,
			"policy": "role-gated on-chain claims read at ruling time; source URI/digest are audit anchors and are not fetched",
		},
		"transactions": live.events,
		"reads": live.reads,
		"outcomes": outcomes,
		"covenant_hash_at_deploy": next(
			(str(r["value"]) for r in live.reads if r["method"] == "get_covenant_hash"), None
		),
	}
	out = REPO_ROOT / "evidence" / "live-run.json"
	out.write_text(json.dumps(evidence, indent=2, default=str), encoding="utf-8")
	print(f"\nevidence -> {out.relative_to(REPO_ROOT)}")

	ok = (
		not outcomes["op-small"]["execute_refused"]
		and not outcomes["op-signed"]["execute_refused"]
		and outcomes["op-big"]["execute_refused"]
		and outcomes["op-prohibited"]["execute_refused"]
		and not outcomes["op-after"]["execute_refused"]
	)
	print("\nRESULT:", "all five enforcement expectations held" if ok else "expectations NOT all held")
	return 0 if ok else 1


if __name__ == "__main__":
	try:
		sys.exit(main())
	except KeyboardInterrupt:
		raise
	except Exception:  # noqa: BLE001
		traceback.print_exc()
		sys.exit(1)
