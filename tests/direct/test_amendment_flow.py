"""Phase 1b -- the amendment flow.

The capture-resistance half of the design: a guard that can be rewritten by
whoever it is blocking is not a guard. These tests prove that changing the
covenant requires a quorum of *distinct* approvers plus an elapsed timelock, and
that the audit trail keeps every version that was actually in force.

The clock is written in place via `set_clock`. `VMContext.warp()` does not
refresh `gl.message_raw["datetime"]`, so it cannot be used here.
"""

import json

import pytest
from .direct_support import COVENANT, COVENANT_V2, TIMELOCK_SECONDS, set_clock


def _amendment(guard, amendment_id):
	return json.loads(guard.get_amendment(amendment_id))


def _governance(guard):
	return json.loads(guard.get_governance())


# --- construction validation --------------------------------------------

@pytest.mark.parametrize(
	"quorum,timelock,why",
	[
		(1, 3600, "quorum below two can be met by one compromised key"),
		(0, 3600, "zero quorum is not a quorum"),
		(2, 59, "timelock below the floor gives no reaction window"),
		(2, 7 * 24 * 3600 + 1, "timelock above the ceiling"),
	],
)
def test_invalid_governance_parameters_are_rejected_at_deployment(deploy_guard, quorum, timelock, why):
	with pytest.raises(Exception):
		deploy_guard(COVENANT, quorum, timelock)


def test_governance_is_readable(guard):
	governance = _governance(guard)
	assert governance["quorum"] == 2
	assert governance["timelock_seconds"] == TIMELOCK_SECONDS
	assert governance["version"] == 1


# --- proposing -----------------------------------------------------------

def test_proposing_an_amendment_changes_nothing(guard):
	guard.propose_amendment("am-1", "a1", COVENANT_V2)

	assert _amendment(guard, "a1")["state"] == "DRAFT"
	assert guard.get_covenant() == COVENANT
	assert guard.get_covenant_hash() != _amendment(guard, "a1")["new_hash"]
	assert _governance(guard)["version"] == 1


def test_only_the_owner_may_propose(guard, direct_vm):
	from gltest.direct.loader import create_address

	direct_vm.sender = create_address("intruder")
	with pytest.raises(Exception):
		guard.propose_amendment("am-2", "a2", COVENANT_V2)


def test_an_amendment_must_actually_change_the_text(guard):
	with pytest.raises(Exception):
		guard.propose_amendment("am-3", "a3", COVENANT)


def test_duplicate_amendment_id_is_rejected(guard):
	guard.propose_amendment("am-4", "a-dup", COVENANT_V2)
	with pytest.raises(Exception):
		guard.propose_amendment("am-5", "a-dup", COVENANT_V2)


# --- quorum --------------------------------------------------------------

def test_one_approval_is_not_a_quorum(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-5", "a-q", COVENANT_V2)
	direct_vm.sender = create_address("approver-1")
	guard.approve_amendment("am-6", "a-q")

	amendment = _amendment(guard, "a-q")
	assert amendment["state"] == "DRAFT"
	assert amendment["approval_count"] == 1
	# No timelock is armed, so it cannot be activated early.
	with pytest.raises(Exception):
		guard.activate_amendment("am-7", "a-q")


def test_the_same_approver_cannot_vote_twice(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-8", "a-dup-vote", COVENANT_V2)
	direct_vm.sender = create_address("approver-1")
	guard.approve_amendment("am-9", "a-dup-vote")
	guard.approve_amendment("am-10", "a-dup-vote")

	amendment = _amendment(guard, "a-dup-vote")
	assert amendment["approval_count"] == 1
	assert amendment["state"] == "DRAFT"


def test_two_distinct_approvers_reach_quorum(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-11", "a-quorum", COVENANT_V2)
	direct_vm.sender = create_address("approver-1")
	guard.approve_amendment("am-12", "a-quorum")
	direct_vm.sender = create_address("approver-2")
	guard.approve_amendment("am-13", "a-quorum")

	amendment = _amendment(guard, "a-quorum")
	assert amendment["state"] == "APPROVED"
	assert amendment["approval_count"] == 2
	assert amendment["activate_after"] > 0


# --- timelock ------------------------------------------------------------

def test_activation_is_refused_before_the_timelock_elapses(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-14", "a-tl", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-15-{index}", "a-tl")

	set_clock("2026-01-01T00:30:00+00:00")  # 30 minutes in, timelock is 60
	with pytest.raises(Exception):
		guard.activate_amendment("am-16", "a-tl")
	assert guard.get_covenant() == COVENANT


def test_an_elapsed_timelock_leaves_the_amendment_ready_not_expired(guard, direct_vm):
	"""The timelock is a minimum wait, not a deadline.

	Once quorum is met and the wait has passed, the amendment is ready. Failing
	it forever would let a single unactivated amendment block a policy change
	indefinitely.
	"""
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-17", "a-ready", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-18-{index}", "a-ready")

	set_clock("2026-01-02T00:00:00+00:00")  # a day later
	assert guard.get_amendment_status("a-ready") == "APPROVED"
	guard.activate_amendment("am-19", "a-ready")
	assert guard.get_covenant() == COVENANT_V2


def test_a_stale_amendment_cannot_clobber_the_current_covenant(guard, direct_vm):
	"""EXPIRED means stale, and the stale case is the one that matters.

	An amendment written against v1 must not activate over v2, or it would
	silently discard whatever superseded it.
	"""
	from gltest.direct.loader import create_address

	# Two amendments, both written against v1.
	guard.propose_amendment("am-s1", "a-first", COVENANT_V2)
	guard.propose_amendment("am-s2", "a-second", COVENANT + "\nClause 5: no swaps at all.")

	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-s3-{index}", "a-first")
		guard.approve_amendment(f"am-s4-{index}", "a-second")

	set_clock("2026-01-02T00:00:00+00:00")
	guard.activate_amendment("am-s5", "a-first")
	assert _governance(guard)["version"] == 2

	# The second was approved too, but against a covenant that is no longer in
	# force. Activating it would silently discard the first.
	assert guard.get_amendment_status("a-second") == "EXPIRED"
	with pytest.raises(Exception):
		guard.activate_amendment("am-s6", "a-second")
	assert guard.get_covenant() == COVENANT_V2


def test_activation_succeeds_once_the_timelock_elapses(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-20", "a-act", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-21-{index}", "a-act")

	set_clock("2026-01-02T00:00:00+00:00")
	guard.activate_amendment("am-22", "a-act")

	assert guard.get_covenant() == COVENANT_V2
	assert _governance(guard)["version"] == 2
	assert _amendment(guard, "a-act")["state"] == "ACTIVE"


def test_activation_is_permissionless(guard, direct_vm):
	"""A lapsed timelock must not leave the covenant stuck between versions."""
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-23", "a-perm", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-24-{index}", "a-perm")

	set_clock("2026-01-02T00:00:00+00:00")
	direct_vm.sender = create_address("complete-stranger")
	guard.activate_amendment("am-25", "a-perm")

	assert _amendment(guard, "a-perm")["state"] == "ACTIVE"


def test_double_activation_is_rejected(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-26", "a-twice", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-27-{index}", "a-twice")

	set_clock("2026-01-02T00:00:00+00:00")
	guard.activate_amendment("am-28", "a-twice")
	with pytest.raises(Exception):
		guard.activate_amendment("am-29", "a-twice")


# --- audit trail ---------------------------------------------------------

def test_every_version_that_was_in_force_is_retained(guard, direct_vm):
	from gltest.direct.loader import create_address

	guard.propose_amendment("am-30", "a-hist", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-31-{index}", "a-hist")
	set_clock("2026-01-02T00:00:00+00:00")
	guard.activate_amendment("am-32", "a-hist")

	history = [json.loads(entry) for entry in guard.get_covenant_history()]
	versions = [entry["version"] for entry in history]

	assert 1 in versions
	assert 2 in versions
	# The superseded version records which amendment replaced it.
	superseded = [entry for entry in history if "superseded_by" in entry]
	assert len(superseded) == 1
	assert superseded[0]["version"] == 1
	assert superseded[0]["superseded_by"] == "a-hist"


def test_a_ruling_stays_bound_to_the_covenant_version_it_judged(guard, direct_vm, allow):
	"""The audit property that makes the whole guard reviewable.

	An operation proposed and ruled under v1 must remain re-readable against v1
	after v2 activates. Without this, the first policy change silently
	invalidates every earlier ruling.
	"""
	direct_vm.mock_llm(r".*", allow)
	guard.propose_operation("ao-1", "op-old", "recurring vendor payment", "invoice 1")
	guard.judge_operation("ao-2", "op-old")

	version_one_hash = json.loads(guard.get_ruling("op-old"))["covenant_digest"]
	assert version_one_hash == guard.get_covenant_hash()

	from gltest.direct.loader import create_address

	guard.propose_amendment("am-33", "a-audit", COVENANT_V2)
	for index in (1, 2):
		direct_vm.sender = create_address(f"approver-{index}")
		guard.approve_amendment(f"am-34-{index}", "a-audit")
	set_clock("2026-01-02T00:00:00+00:00")
	guard.activate_amendment("am-35", "a-audit")

	assert guard.get_covenant_hash() != version_one_hash
	# The old ruling still names the covenant it was judged against.
	ruling = json.loads(guard.get_ruling("op-old"))
	assert ruling["covenant_digest"] == version_one_hash


def test_an_unknown_amendment_reads_empty(guard):
	assert guard.get_amendment("nope") == ""
	assert guard.get_amendment_status("nope") == ""


@pytest.mark.parametrize("method", ["approve_amendment", "activate_amendment"])
def test_amendment_writes_on_an_unknown_id_are_rejected(guard, method):
	with pytest.raises(Exception):
		getattr(guard, method)("am-x", "nope")