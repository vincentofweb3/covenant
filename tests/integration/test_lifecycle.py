"""Phase 2 -- the refusal proof.

This is the central claim of the entire project, and it is only testable here.

> A value-moving method consults a policy oracle and refuses when the oracle says
> no.

`tests/direct/` covers the guard's internals. It cannot cover any of this: direct
mode drops cross-contract calls and shares storage slots between instances. Every
test below runs on the in-process GenLayer simulator.
"""

import json

import pytest
from .conftest import (
	CLAIM_VALID_UNTIL,
	COVENANT,
	COVENANT_V2,
	EXCERPT_FABRICATED,
	GUARD_PATH,
	OWNER,
	PROCUREMENT,
	PROPOSER,
	QUORUM,
	RECIPIENT,
	SIGNER_A,
	SIGNER_B,
	SOURCE_DIGEST,
	SOURCE_URI,
	TIMELOCK,
	T0,
	VAULT_PATH,
	SimError,
	allow_fabricated,
	allow_ok,
	deny_ok,
	request,
	rule,
	unsure_ok,
)

SMALL = 900          # under Clause 3's 50000 threshold
LARGE = 120000       # over the threshold, no multisig approval in evidence


def _balance(sim, vault) -> int:
	return int(sim.read(vault, "get_balance"))


def _payment(sim, vault, operation_id):
	return json.loads(sim.read(vault, "get_payment", [operation_id]))


def _ruling(sim, guard, operation_id):
	return json.loads(sim.read(guard, "get_ruling", [operation_id]))


# --- the payload is forwarded, not summarised -----------------------------

def test_vault_binds_amount_and_recipient_into_the_judged_text(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-bind", LARGE, "vendor invoice 8891")

	held = json.loads(sim.read(guard, "get_operation", ["op-bind"]))
	payment = _payment(sim, vault, "op-bind")

	# The judge must see the amount. A summary standing in for it would let a
	# proposer request 120000 while describing a routine small payment.
	summary = json.loads(held["summary"])
	assert summary["amount"] == LARGE
	assert summary["recipient"] == RECIPIENT.lower()
	assert held["summary"] == payment["binding"]


def test_vault_is_registered_with_the_guard(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]
	registration = json.loads(sim.read(guard, "get_vault", [vault.lower()]))
	assert registration["vault"] == vault.lower()
	assert registration["covenant_hash"] == sim.read(guard, "get_covenant_hash")


def test_funding_moves_the_balance(sim, deployed):
	assert _balance(sim, deployed["vault"]) == 1_000_000


# --- the compliant path ---------------------------------------------------

def test_a_permitted_payment_executes_and_moves_funds(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-ok", SMALL, "recurring monthly retainer")
	rule(sim, guard, "op-ok", allow_ok())

	assert _ruling(sim, guard, "op-ok")["verdict"] == "ALLOWED"

	sim.call(vault, "execute_payment", ["exec-ok", "op-ok"])

	assert _balance(sim, vault) == 1_000_000 - SMALL
	assert _payment(sim, vault, "op-ok")["state"] == "EXECUTED"
	assert _payment(sim, vault, "op-ok")["executed_ruling_digest"] != ""


def test_an_oversized_payment_is_refused_and_no_funds_move(sim, deployed):
	"""The refusal. Clause 3 caps a single payment at 50000 without multisig."""
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-big", LARGE, "one-off large disbursement")
	rule(sim, guard, "op-big", deny_ok())

	assert _ruling(sim, guard, "op-big")["verdict"] == "VIOLATED"

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-big", "op-big"])

	assert _balance(sim, vault) == 1_000_000
	assert _payment(sim, vault, "op-big")["state"] == "REQUESTED"


# --- ambiguity and model failure also block -------------------------------

def test_an_ambiguous_operation_is_refused(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-amb", LARGE, "unclear disbursement")
	rule(sim, guard, "op-amb", unsure_ok())

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-amb", "op-amb"])

	assert _balance(sim, vault) == 1_000_000


def test_a_fabricated_clause_does_not_unlock_the_vault(sim, deployed):
	"""The end-to-end version of the design's central assertion.

	The model claims permission and cites a confident, specific clause that does
	not exist. Downstream, that must be indistinguishable from any other refusal.
	"""
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-fake", LARGE, "one-off large disbursement")
	rule(sim, guard, "op-fake", allow_fabricated())

	ruling = _ruling(sim, guard, "op-fake")
	assert ruling["verdict"] == "AMBIGUOUS"
	assert ruling["reason_code"] == "UNVERIFIED_EXCERPT"
	assert EXCERPT_FABRICATED not in json.dumps(ruling)

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-fake", "op-fake"])

	assert _balance(sim, vault) == 1_000_000


def test_executing_before_any_ruling_is_refused(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-early", SMALL, "recurring retainer")

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-early", "op-early"])
	assert _balance(sim, vault) == 1_000_000


# --- the payload-binding attack ------------------------------------------

def test_an_approval_obtained_outside_the_vault_cannot_be_spent_by_it(sim, deployed):
	"""The operation-id replay attack, end to end.

	`propose_operation` on the guard is permissionless. So an attacker can take
	an operation to a genuine ALLOWED ruling entirely on their own, then present
	that operation_id to the vault. The vault holds no payment of its own for
	that id, so there is nothing authorising it to move its funds.

	This is why execution is bound to a payment the vault itself registered,
	not merely to "an operation with this id exists and is approved".
	"""
	guard, vault = deployed["guard"], deployed["vault"]

	# The attacker proposes directly on the guard, bypassing the vault, with a
	# payload the covenant is happy with.
	attacker_binding = json.dumps(
		{"amount": SMALL, "recipient": RECIPIENT.lower(), "memo": "recurring retainer"},
		sort_keys=True,
		separators=(",", ":"),
	)
	sim.call(
		guard,
		"propose_operation",
		["evil-1", "op-evil", attacker_binding, RECIPIENT],
		sender=PROPOSER,
	)
	rule(sim, guard, "op-evil", allow_ok())
	assert _ruling(sim, guard, "op-evil")["verdict"] == "ALLOWED"

	# The ruling is real and final. The vault still refuses it.
	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-evil", "op-evil"])
	assert _balance(sim, vault) == 1_000_000


def test_the_vault_executes_only_against_its_own_registered_payload(sim, deployed):
	"""The binding check, positively.

	When the guard's copy of the operation is the vault's own binding, execution
	proceeds. This is the other half of the check above: the vault is not simply
	refusing everything, it is refusing operations it did not request.
	"""
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-mine", SMALL, "recurring retainer")
	rule(sim, guard, "op-mine", allow_ok())
	sim.call(vault, "execute_payment", ["exec-mine", "op-mine"])

	payment = _payment(sim, vault, "op-mine")
	assert payment["state"] == "EXECUTED"
	# The ruling that authorised it is the one bound to this exact payload.
	assert payment["executed_ruling_digest"] == _ruling(sim, guard, "op-mine")["ruling_digest"]
	assert json.loads(payment["binding"])["amount"] == SMALL


def test_a_third_party_cannot_execute_someone_elses_payment_twice(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-once", SMALL, "recurring retainer")
	rule(sim, guard, "op-once", allow_ok())
	sim.call(vault, "execute_payment", ["exec-1", "op-once"])
	after_first = _balance(sim, vault)

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-2", "op-once"])
	assert _balance(sim, vault) == after_first


# --- vault-local guards ---------------------------------------------------

def test_exceeding_the_vault_balance_is_refused(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-drain", 5_000_000, "recurring retainer")
	rule(sim, guard, "op-drain", allow_ok())

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-drain", "op-drain"])
	assert _balance(sim, vault) == 1_000_000


def test_a_cancelled_payment_cannot_execute(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-cancel", SMALL, "recurring retainer")
	sim.call(vault, "cancel_payment", ["cancel-1", "op-cancel"])
	rule(sim, guard, "op-cancel", allow_ok())

	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-cancel", "op-cancel"])
	assert _balance(sim, vault) == 1_000_000


def test_only_the_proposer_or_vault_owner_can_cancel_a_payment(sim, deployed):
	vault = deployed["vault"]
	stranger = "0x" + "99" * 20

	request(sim, vault, "op-cancel-auth", SMALL, "vendor retainer")
	with pytest.raises(SimError):
		sim.call(
			vault,
			"cancel_payment",
			["cancel-stranger", "op-cancel-auth"],
			sender=stranger,
		)
	assert sim.read(vault, "get_payment_status", ["op-cancel-auth"]) == "REQUESTED"

	result = sim.call(
		vault,
		"cancel_payment",
		["cancel-proposer", "op-cancel-auth"],
		sender=PROPOSER,
	)
	assert json.loads(result)["state"] == "CANCELLED"


def test_unknown_payment_lookups_are_empty_not_fatal(sim, deployed):
	vault = deployed["vault"]
	assert sim.read(vault, "get_payment", ["nope"]) == ""
	assert sim.read(vault, "get_payment_status", ["nope"]) == ""


# --- governance changes the enforcement ----------------------------------

def test_amending_the_covenant_changes_what_the_vault_allows(sim, deployed):
	"""The demo's final and most persuasive step.

	The same 120000 payment is refused under v1. After a quorum-approved,
	timelocked amendment raises the threshold to 250000, an identical payment is
	allowed and executes. Enforcement tracks the policy actually in force.
	"""
	guard, vault = deployed["guard"], deployed["vault"]

	# 1. Under v1 this payment is refused.
	request(sim, vault, "op-before", LARGE, "one-off large disbursement")
	rule(sim, guard, "op-before", deny_ok())
	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-before", "op-before"])
	assert _balance(sim, vault) == 1_000_000

	# 2. Owner proposes, two distinct approvers sign, timelock elapses.
	sim.call(guard, "propose_amendment", ["am-1", "amend-1", COVENANT_V2])
	sim.call(guard, "approve_amendment", ["ap-1", "amend-1"], sender=SIGNER_A)
	sim.call(guard, "approve_amendment", ["ap-2", "amend-1"], sender=SIGNER_B)
	sim.set_time("2026-01-02T00:00:00+00:00")
	sim.call(guard, "activate_amendment", ["act-1", "amend-1"])
	assert int(sim.read(guard, "get_covenant_version")) == 2

	# 3. The identical payment is now permitted and executes.
	request(sim, vault, "op-after", LARGE, "one-off large disbursement")
	rule(sim, guard, "op-after", allow_ok())
	sim.call(vault, "execute_payment", ["exec-after", "op-after"])

	assert _balance(sim, vault) == 1_000_000 - LARGE


def test_a_single_approver_cannot_amend_the_covenant(sim, deployed):
	"""The capture-resistance half, on chain."""
	guard = deployed["guard"]

	before = sim.read(guard, "get_covenant_hash")
	sim.call(guard, "propose_amendment", ["am-2", "amend-2", COVENANT_V2])
	sim.call(guard, "approve_amendment", ["ap-3", "amend-2"], sender=SIGNER_A)

	assert sim.read(guard, "get_amendment_status", ["amend-2"]) == "DRAFT"
	with pytest.raises(SimError):
		sim.call(guard, "activate_amendment", ["act-2", "amend-2"])
	assert sim.read(guard, "get_covenant_hash") == before


def test_the_covenant_owner_cannot_amend_alone_either(sim, deployed):
	guard = deployed["guard"]
	before = sim.read(guard, "get_covenant_hash")

	sim.call(guard, "propose_amendment", ["am-3", "amend-3", COVENANT_V2], sender=OWNER)
	sim.call(guard, "approve_amendment", ["ap-4", "amend-3"], sender=OWNER)

	with pytest.raises(SimError):
		sim.call(guard, "activate_amendment", ["act-3", "amend-3"])
	assert sim.read(guard, "get_covenant_hash") == before


# --- honest reporting -----------------------------------------------------

def test_protocol_finality_is_not_evidence_of_execution(sim, deployed):
	"""A `sim_call` that reverts still produces a transaction.

	Recorded so the evidence file cannot accidentally claim that a reverted
	cross-contract refusal was a successful operation.
	"""
	guard, vault = deployed["guard"], deployed["vault"]

	request(sim, vault, "op-final", LARGE, "one-off large disbursement")
	rule(sim, guard, "op-final", deny_ok())

	# A reverted call still produces a transaction on a real chain, so a receipt
	# is not evidence of success. The balance is the ground truth, and it does
	# not move.
	with pytest.raises(SimError):
		sim.call(vault, "execute_payment", ["exec-final", "op-final"])
	assert _balance(sim, vault) == 1_000_000
	assert _payment(sim, vault, "op-final")["state"] == "REQUESTED"

# --- evidence provenance, end to end -------------------------------------

def test_two_signer_approvals_are_carried_into_the_ruling_and_the_vault(sim, deployed):
	"""The evidence bundle is not decorative: its digest is stored on execution."""
	guard, vault = deployed["guard"], deployed["vault"]
	request(sim, vault, "op-signed", LARGE, "approved large payment")

	sim.call(guard, "approve_payment", ["ap-a", "op-signed"], sender=SIGNER_A)
	sim.call(guard, "approve_payment", ["ap-b", "op-signed"], sender=SIGNER_B)
	rule(sim, guard, "op-signed", allow_ok())

	ruling = _ruling(sim, guard, "op-signed")
	assert ruling["approval_count"] == 2
	assert ruling["approval_signers"] == sorted([SIGNER_A, SIGNER_B])
	assert ruling["vendor_status"] == "approved"
	assert ruling["risk_status"] == "clear"

	sim.call(vault, "execute_payment", ["exec-signed", "op-signed"])
	payment = _payment(sim, vault, "op-signed")
	assert _balance(sim, vault) == 1_000_000 - LARGE
	assert payment["executed_evidence_digest"] == ruling["evidence_digest"]
	assert payment["executed_approval_count"] == 2


def test_a_missing_attestation_blocks_judgment_rather_than_guessing(sim):
	"""Fail closed: no claim, no ruling, no payment."""
	guard = sim.deploy(
		GUARD_PATH, [COVENANT, QUORUM, TIMELOCK, PROCUREMENT, "0x" + "66" * 20, SIGNER_A, SIGNER_B]
	)
	vault = sim.deploy(VAULT_PATH, [guard, OWNER])
	sim.call(guard, "register_vault", ["reg-noclaim", vault])
	sim.call(vault, "deposit", ["dep-noclaim", 1_000_000])
	request(sim, vault, "op-noclaim", SMALL, "recurring retainer")

	sim.install_mocks({".*": allow_ok()})
	with pytest.raises(SimError):
		sim.call(guard, "judge_operation", ["judge-noclaim", "op-noclaim"])

	assert sim.read(guard, "get_operation_status", ["op-noclaim"]) == "PROPOSED"
	assert _balance(sim, vault) == 1_000_000


def test_a_requester_cannot_write_a_vendor_claim(sim, deployed):
	"""The sender is the credential. A requester cannot attest for itself."""
	guard = deployed["guard"]
	with pytest.raises(SimError):
		sim.call(
			guard,
			"attest_vendor",
			[
				"forged",
				RECIPIENT,
				"approved",
				CLAIM_VALID_UNTIL,
				SOURCE_URI,
				SOURCE_DIGEST,
			],
			sender=PROPOSER,
		)


def test_an_expired_claim_blocks_judgment(sim, deployed):
	guard = deployed["guard"]
	sim.set_time("2026-12-02T00:00:00+00:00")
	request(sim, deployed["vault"], "op-expired", SMALL, "recurring retainer")

	sim.install_mocks({".*": allow_ok()})
	with pytest.raises(SimError):
		sim.call(guard, "judge_operation", ["judge-expired", "op-expired"])

	assert sim.read(guard, "get_operation_status", ["op-expired"]) == "PROPOSED"


def test_a_not_approved_vendor_claim_is_carried_into_the_ruling(sim, deployed):
	guard, vault = deployed["guard"], deployed["vault"]
	sim.call(
		guard,
		"attest_vendor",
		[
			"vendor-off",
			RECIPIENT,
			"not_approved",
			CLAIM_VALID_UNTIL,
			SOURCE_URI,
			SOURCE_DIGEST,
		],
		sender=PROCUREMENT,
	)
	request(sim, vault, "op-off", SMALL, "payment to a removed vendor")
	rule(sim, guard, "op-off", deny_ok())

	ruling = _ruling(sim, guard, "op-off")
	assert ruling["verdict"] == "VIOLATED"
	assert ruling["vendor_status"] == "not_approved"
	assert ruling["evidence_verified"] is True
