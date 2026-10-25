# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""TreasuryVault -- the worked example that proves CovenantGuard is load-bearing.

A guard nobody must consult is a guard nobody will consult. This vault exists so
that the enforcement can be observed rather than asserted: every outbound
movement must carry a finalized ``ALLOWED`` ruling from a registered
``CovenantGuard``, and this contract re-reads the guard over a cross-contract
call to check. It does not trust the caller, and it does not trust a bare
``is_downstream_actionable`` answer either -- it re-reads the ruling and
independently refuses any ruling whose excerpt was not verified or whose
evidence bundle was not acquired by the guard itself.

**Why this contract binds the amount into the judged text.**

The obvious design is to keep ``amount`` in the vault and send the guard a
free-text ``summary``. That is exploitable. The model judges the summary and the
evidence; it never sees the amount. A proposer could write
``summary="routine small vendor payment"`` and request 1,000,000 against a
covenant whose Clause 3 threshold is 50,000. The judge would see nothing wrong.

So this vault does not keep the amount out of band. It builds a canonical,
machine-readable binding of amount, recipient and memo, and that binding is
exactly what is submitted to the guard for judgment. On execution the vault
reads the operation back from the guard and refuses unless the summary it is
about to act on is byte-identical to the summary it submitted. Authority
obtained for one payload cannot be spent on another.

**Why the vault does not forward evidence text.** The first version accepted a
free-text evidence string and passed it to the guard. That made the excerpt
check decorative: the requester could assert "vendor approved" and the model
would reason about the assertion. The vault now supplies only the payment and
its recipient. The guard reads the current vendor and risk attestations for that
recipient from its own role-gated registry, counts the configured signers'
approvals of the exact binding, and binds the resulting evidence digest into the
ruling. The vault re-checks that binding before it moves anything.

Scope, stated plainly: balances here are **internal accounting entries**. This is
not escrow, no native token is transferred, and nothing in this file should be
read as a custody or settlement system. What it does prove is the enforcement
shape: a value-moving method that consults a policy oracle and refuses when the
oracle says no.
"""

import hashlib
import json

from genlayer import gl, Address, DynArray, TreeMap, u256

PROTOCOL_VERSION = "covenant-vault-v1"
HASH_VERSION = "covenant-sha256-v1"

PAY_REQUESTED = "REQUESTED"
PAY_EXECUTED = "EXECUTED"
PAY_CANCELLED = "CANCELLED"

VERDICT_ALLOWED = "ALLOWED"

MAX_MEMO_LEN = 800
MAX_RECIPIENTS = 64

MAX_AMOUNT_DIGITS = 78


class PaymentExecuted(gl.Event):
	"""Emitted only on the path that moved funds.

	There is deliberately no "refused" event: a refusal reverts the transaction,
	which rolls back its logs, so such an event would never be observable. The
	absence of a ``PaymentExecuted`` log *is* the record of a refusal.
	"""

	def __init__(self, operation_id: str, recipient: Address, amount: u256, /): ...


def _fail(message: str) -> None:
	raise gl.vm.UserError("EXPECTED: " + message)


def _canonical(value) -> str:
	return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _digest(tag: str, fields: list) -> str:
	payload = "|".join([HASH_VERSION, tag] + [str(f) for f in fields])
	return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _address_text(value) -> str:
	return str(value).strip().lower()


def _sender() -> str:
	return _address_text(gl.message.sender_address)


def _check_amount(value) -> int:
	text = str(value).strip()
	if text == "":
		_fail("amount must not be empty")
	if not text.isdigit():
		_fail("amount must be a non-negative integer")
	if len(text) > MAX_AMOUNT_DIGITS:
		_fail("amount has too many digits")
	amount = int(text)
	if amount <= 0:
		_fail("amount must be positive")
	return amount


def _check_address(value, label: str) -> str:
	address = str(value).strip().lower()
	if not address.startswith("0x") or len(address) != 42:
		_fail(label + " must be a 0x-prefixed 20-byte address")
	return address


def _bind(amount: int, recipient: str, memo: str) -> str:
	"""The canonical payload submitted to the guard for judgment.

	Amount and recipient are inside the judged text by construction, not by
	convention. See the module docstring.
	"""
	return _canonical({"amount": amount, "recipient": recipient, "memo": memo})


class TreasuryVault(gl.Contract):
	"""A treasury whose outbound movements are gated by a covenant ruling."""

	owner: Address
	guard: Address
	balance: u256
	deposits_total: u256
	outflow_total: u256
	payments: TreeMap[str, str]
	payment_order: DynArray[str]
	actions: TreeMap[str, str]

	def __init__(self, guard: str, owner: str):
		guard_address = _check_address(guard, "guard")
		self.guard = Address(guard_address)
		self.owner = Address(_check_address(owner, "owner"))
		self.balance = u256(0)
		self.deposits_total = u256(0)
		self.outflow_total = u256(0)

	# --- views ------------------------------------------------------------

	@gl.public.view
	def get_guard(self) -> str:
		return str(self.guard)

	@gl.public.view
	def get_owner(self) -> str:
		return str(self.owner)

	@gl.public.view
	def get_balance(self) -> int:
		return int(self.balance)

	@gl.public.view
	def get_totals(self) -> str:
		return _canonical(
			{
				"balance": int(self.balance),
				"deposits_total": int(self.deposits_total),
				"outflow_total": int(self.outflow_total),
			}
		)

	@gl.public.view
	def get_payment(self, operation_id: str) -> str:
		return self.payments.get(str(operation_id), "")

	@gl.public.view
	def get_payment_status(self, operation_id: str) -> str:
		record = self.payments.get(str(operation_id), "")
		if record == "":
			return ""
		return str(json.loads(record).get("state", ""))

	@gl.public.view
	def get_payment_count(self) -> int:
		return len(self.payment_order)

	# --- idempotency ------------------------------------------------------

	def _action_digest(self, kind: str, payload: dict) -> str:
		return _digest("action", [kind, _canonical(payload)])

	def _action_replay(self, action_id: str, digest: str) -> bool:
		seen = self.actions.get(action_id, "")
		if seen == "":
			return False
		if json.loads(seen).get("digest") != digest:
			_fail("action_id was already used with different arguments")
		return True

	def _action_result(self, action_id: str) -> str:
		return str(json.loads(self.actions[action_id]).get("result", ""))

	def _record_action(self, action_id: str, digest: str, result: str) -> None:
		self.actions[action_id] = _canonical({"digest": digest, "result": result})

	# --- writes -----------------------------------------------------------

	@gl.public.write
	def deposit(self, action_id: str, amount: u256) -> str:
		"""Credit the internal ledger.

		Not payable and not a transfer: this is an accounting entry so the
		refusal behaviour can be demonstrated without claiming custody of funds.
		"""
		credit = _check_amount(int(amount))
		digest = self._action_digest("deposit", {"amount": credit})
		if self._action_replay(action_id, digest):
			return self._action_result(action_id)

		self.balance = u256(int(self.balance) + credit)
		self.deposits_total = u256(int(self.deposits_total) + credit)
		result = _canonical({"balance": int(self.balance), "credited": credit})
		self._record_action(action_id, digest, result)
		return result

	@gl.public.write
	def request_payment(
		self, action_id: str, operation_id: str, recipient: str, amount: u256, memo: str
	) -> str:
		"""Register a payment locally and ask the guard to rule on it.

		The summary submitted to the guard is the canonical binding of amount,
		recipient and memo -- never a human-written sentence standing in for
		them. No evidence text is accepted: the guard acquires the vendor and
		risk facts for ``recipient`` from its own attestation registry, and counts
		the configured signers' approvals of this exact binding. Permissionless:
		anyone may propose, and the covenant decides.
		"""
		op_id = str(operation_id).strip()
		if op_id == "":
			_fail("operation_id must not be empty")
		to = _check_address(recipient, "recipient")
		value = _check_amount(int(amount))
		note = str(memo).strip()
		if note == "":
			_fail("memo must not be empty")
		if len(note) > MAX_MEMO_LEN:
			_fail("memo exceeds the maximum length")

		digest = self._action_digest(
			"request_payment",
			{
				"operation_id": op_id,
				"recipient": to,
				"amount": value,
				"memo": note,
			},
		)
		if self._action_replay(action_id, digest):
			return self._action_result(action_id)

		if self.payments.get(op_id, "") != "":
			_fail("payment already exists for this operation_id")

		binding = _bind(value, to, note)
		record = {
			"operation_id": op_id,
			"state": PAY_REQUESTED,
			"proposer": _sender(),
			"recipient": to,
			"amount": value,
			"memo": note,
			"binding": binding,
			"binding_digest": _digest("binding", [binding]),
			"requested_at": 0,
			"executed_ruling_digest": "",
			"executed_evidence_digest": "",
		}
		encoded = _canonical(record)
		self.payments[op_id] = encoded
		self.payment_order.append(op_id)

		# Submit exactly the binding and its subject to the guard. Emitted with
		# finalized semantics; `on` defaults to 'finalized', which the std lib
		# recommends over 'accepted'.
		gl.get_contract_at(self.guard).emit(on="finalized").propose_operation(
			action_id, op_id, binding, to
		)

		self._record_action(action_id, digest, encoded)
		return encoded

	@gl.public.write
	def execute_payment(self, action_id: str, operation_id: str) -> str:
		"""Move funds, but only against a verified ruling from the guard.

		The refusal lives here. Four independent checks, in order of cost:

		1. the operation the guard holds must carry *our* binding and *our*
		   recipient, byte for byte -- otherwise an approval obtained for a
		   benign payload could be spent on a large one;
		2. the guard must report the operation as actionable, which it only does
		   for a finalized ALLOWED ruling;
		3. the ruling itself is re-read and must be ALLOWED with a verified
		   excerpt;
		4. the ruling must carry the same contract-acquired evidence digest the
		   guard stored on the operation, for the same subject.

		Checks 3 and 4 are deliberately redundant with the guard's own guarantees.
		If the guard were ever compromised, a ruling whose clause was never
		verified, or whose facts were never acquired, still cannot move funds here.
		"""
		op_id = str(operation_id).strip()
		digest = self._action_digest("execute_payment", {"operation_id": op_id})
		if self._action_replay(action_id, digest):
			return self._action_result(action_id)

		stored = self.payments.get(op_id, "")
		if stored == "":
			_fail("unknown payment")
		payment = json.loads(stored)
		if str(payment.get("state", "")) != PAY_REQUESTED:
			_fail("payment is not in a state that can execute")

		value = int(payment.get("amount", 0))
		to = str(payment.get("recipient", ""))
		binding = str(payment.get("binding", ""))

		# -- 1. the judged payload must be ours ------------------------------
		held_raw = gl.get_contract_at(self.guard).view().get_operation(op_id)
		if held_raw is None:
			_fail("guard did not answer the operation query")
		held_text = str(held_raw)
		if held_text == "":
			_fail("guard holds no operation for this id")
		try:
			held = json.loads(held_text)
		except Exception:
			_fail("guard returned an unreadable operation record")
		if str(held.get("summary", "")) != binding:
			_fail("the judged payload is not the payload this vault submitted")
		if _digest("binding", [str(held.get("summary", ""))]) != str(
			payment.get("binding_digest", "")
		):
			_fail("binding digest mismatch")
		if _address_text(held.get("subject", "")) != to:
			_fail("the judged evidence subject is not the payment recipient")
		held_evidence_digest = str(held.get("evidence_digest", ""))
		if held_evidence_digest == "":
			_fail("the guard holds no acquired evidence for this operation")
		if str(held.get("input_digest", "")) == "":
			_fail("the guard holds no input digest for this operation")

		# -- 2. the guard must report it actionable ----------------------------
		actionable = gl.get_contract_at(self.guard).view().is_downstream_actionable(op_id)
		if actionable is None:
			_fail("guard did not answer the actionability query")
		if not bool(actionable):
			_fail("operation is not actionable under the covenant")

		# -- 3. re-read and independently verify the ruling --------------------
		ruling_raw = gl.get_contract_at(self.guard).view().get_ruling(op_id)
		if ruling_raw is None:
			_fail("guard did not answer the ruling query")
		ruling_text = str(ruling_raw)
		if ruling_text == "":
			_fail("guard returned no ruling")
		try:
			ruling = json.loads(ruling_text)
		except Exception:
			_fail("guard returned an unreadable ruling")
		if str(ruling.get("verdict", "")) != VERDICT_ALLOWED:
			_fail("ruling is not an approval")
		if ruling.get("excerpt_verified") is not True:
			_fail("ruling cites no verified covenant clause")
		if ruling.get("evidence_verified") is not True:
			_fail("ruling cites no contract-acquired evidence")
		if _address_text(ruling.get("evidence_subject", "")) != to:
			_fail("ruling evidence is for a different subject")
		if str(ruling.get("evidence_digest", "")) != held_evidence_digest:
			_fail("ruling evidence digest does not match the stored evidence")
		if str(ruling.get("input_digest", "")) != str(held.get("input_digest", "")):
			_fail("ruling input digest does not match the stored operation")

		if value > int(self.balance):
			_fail("insufficient vault balance")

		self.balance = u256(int(self.balance) - value)
		self.outflow_total = u256(int(self.outflow_total) + value)
		payment["state"] = PAY_EXECUTED
		payment["executed_ruling_digest"] = str(ruling.get("ruling_digest", ""))
		payment["executed_covenant_digest"] = str(ruling.get("covenant_digest", ""))
		payment["executed_evidence_digest"] = held_evidence_digest
		payment["executed_vendor_status"] = str(ruling.get("vendor_status", ""))
		payment["executed_risk_status"] = str(ruling.get("risk_status", ""))
		payment["executed_approval_count"] = int(ruling.get("approval_count", 0))
		payment["executed_excerpt"] = str(ruling.get("excerpt", ""))
		encoded = _canonical(payment)
		self.payments[op_id] = encoded

		PaymentExecuted(op_id, Address(to), u256(value)).emit()
		self._record_action(action_id, digest, encoded)
		return encoded

	@gl.public.write
	def cancel_payment(self, action_id: str, operation_id: str) -> str:
		"""Withdraw a requested payment before it executes."""
		op_id = str(operation_id).strip()
		digest = self._action_digest("cancel_payment", {"operation_id": op_id})
		if self._action_replay(action_id, digest):
			return self._action_result(action_id)

		stored = self.payments.get(op_id, "")
		if stored == "":
			_fail("unknown payment")
		payment = json.loads(stored)
		if str(payment.get("state", "")) != PAY_REQUESTED:
			_fail("only a requested payment can be cancelled")

		if _sender() != str(payment.get("proposer", "")) and _sender() != _address_text(
			self.owner
		):
			_fail("only the proposer or vault owner may cancel a payment")

		payment["state"] = PAY_CANCELLED
		cancelled = _canonical(payment)
		self.payments[op_id] = cancelled
		self._record_action(action_id, digest, cancelled)
		return cancelled