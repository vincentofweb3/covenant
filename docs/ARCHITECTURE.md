# Architecture

## Design boundary

Covenant is a **contract primitive**, not a product. There is no frontend, no
off-chain service, and no backend. Two contracts exist:

- `covenant_guard.py` — the primitive. Stores the covenant, rules on proposed
  operations, and governs changes to its own text.
- `treasury_vault.py` — a worked example whose only job is to demonstrate that
  the guard is load-bearing.

Any value-holding GenLayer contract can register with a guard. The vault is not
part of the primitive and can be deleted without affecting it.

## Frozen design decisions

- **The verdict is a three-value enum plus a verbatim excerpt.** No fourth
  value, no confidence score. A score would invite threshold tuning against a
  quantity that is not measurable.
- **Excerpt verification is deterministic and on-chain.** The contract holds the
  covenant bytes and checks substring membership itself. This is the thesis.
- **The judged payload is the bound payload.** The vault submits a canonical
  `{amount, recipient, memo}` binding as the operation summary. Not a summary
  standing in for the numbers.
- **External facts are attested, not asserted.** A requester supplies the
  instruction (`summary`, `subject`), never the evidence. Procurement and risk
  attestors and two payment signers are fixed at deployment. Their writes are
  authenticated by `gl.message.sender_address`, which is the on-chain
  provenance; the chain, not a payload field, is the credential.
- **Evidence is acquired at ruling time.** The guard reads the current vendor
  and risk claims for the operation's subject, counts approvals of the exact
  binding, and binds the bundle's digest into the ruling. A missing or expired
  claim fails closed; it does not become an `AMBIGUOUS` ruling.
- **The guard never holds value.** No balance, no deposit, no transfer method.
  `scripts/preflight.py` asserts the absence of all three.
- **Exactly one consensus seam.** The guard rules; the vault asks. A second judge
  is a second opinion nothing reconciles. Preflight enforces this too.
- **Amendments are quorum + timelock**, and `EXPIRED` means stale rather than late.
- **No native token settlement.** Internal accounting only, stated in the module
  docstring and enforced by the absence of any value-moving method on the guard.

## Durable state

### `CovenantGuard`

```
owner                 Address
procurement_attestor  Address              fixed at deploy; writes VENDOR_APPROVAL
risk_attestor         Address              fixed at deploy; writes RISK_STATUS
signer_a, signer_b    Address              fixed at deploy; approve one binding
covenant_text         str                  active covenant, exact bytes
covenant_hash         str                  sha256 over (HASH_VERSION, "covenant", text)
covenant_version      u32
covenant_history      DynArray[str]        {version, hash} and {version, hash, superseded_by}
quorum                u32                  >= 2
timelock_seconds      u32                  in [60, 604800]

operations            TreeMap[str, str]   operation_id -> canonical JSON
operation_order       DynArray[str]
vendor_claims         TreeMap[str, str]   subject -> current VENDOR_APPROVAL
risk_claims           TreeMap[str, str]   subject -> current RISK_STATUS
approvals             TreeMap[str, str]   operation_id -> list of signer approvals
claim_seq             u32                 monotonic claim sequence
amendments            TreeMap[str, str]   amendment_id -> canonical JSON
amendment_order       DynArray[str]
actions               TreeMap[str, str]   action_id -> {digest, result}
vaults                TreeMap[str, str]   vault address -> registration record
```

Records are canonical JSON in a `TreeMap[str, str]` rather than `@allow_storage`
dataclasses. The JSON form costs a parse and buys an audit trail that can be
diffed offline with `jq`, and it avoids a storage-typing surface that has to be
re-derived for every pinned std lib. The trade was made deliberately, not by
default.

### `TreasuryVault`

```
owner, guard          Address
balance               u256                internal accounting, NOT escrow
deposits_total        u256
outflow_total         u256
payments              TreeMap[str, str]  operation_id -> canonical JSON
payment_order         DynArray[str]
actions               TreeMap[str, str]
```

## State machines

### Operation

```
                    propose_operation
                          |
                          v
                     [ PROPOSED ] <--------- cancel_operation
                          |                        ^
                 judge_operation                   |
                          v                        |
                     [ RULED ] ------ finalize -----+-----> [ CANCELLED ]
                          |
                   finalize_operation
                          |
              +-----------+-----------+
              |                       |
        verdict ALLOWED         everything else
              |                       |
              v                       v
        [ FINALIZED ]            [ REJECTED ]
              |
    only disposition that is
    downstream-actionable
```

`AMBIGUOUS` and every model failure land in `REJECTED`. Fail closed, and
distinguishable afterwards via `ruling.reason_code` and `ruling.model_failure`.

### Amendment

```
                      propose_amendment
                             |
                             v
                        [ DRAFT ]
                             |
                  approve x distinct quorum
                             |
                             v
                       [ APPROVED ]  (timelock armed)
                             |
              +--------------+--------------+
              |                             |
        timelock elapsed            base_version != current
              |                             |
              v                             v
          [ ACTIVE ]                    [ EXPIRED ]
        old version SUPERSEDED
```

The timelock is a **minimum wait, not a deadline**. Refusing a properly-approved
change because nobody activated it would hand a veto to anyone who can simply
not call the method. `EXPIRED` exists for staleness, which is a real condition
with a real consequence.

### Payment (vault)

```
   request_payment            execute_payment                cancel_payment
        |                            |                              |
        v                            v                              v
   [ REQUESTED ] --------------> [ EXECUTED ]                  [ CANCELLED ]
        |                            ^
        +-- refused -----------------+
        (execute_payment reverts; funds unchanged; balance is the record)
```

There is deliberately **no refusal event**: a refusal reverts the transaction,
which rolls back its logs. The absence of a `PaymentExecuted` log *is* the
record of a refusal.

## Message sequence

```
  operator        Vault                Guard
     |             |                    |
     |--deposit--->|                    |
     |             |                    |
     |--request_payment(op_id, amount, recipient, memo)------------>|
     |             |--propose_operation(op_id, BINDING, recipient)->|   [finalized]
     |             |                    |
     |--guard.attest_vendor / attest_risk (attestors, earlier)----->|
     |--guard.approve_payment (signers, before judgment)----------->|
     |--guard.judge_operation(op_id)------------------------------>|   [consensus]
     |             |                    |  verdict + excerpt + evidence_digest
     |             |                    |
     |--guard.finalize_operation(op_id)--------------------------->|
     |             |                    |
     |--vault.execute_payment(op_id)-->|                            |
     |             |--get_operation(op_id)------------------------>|   [view]
     |             |--is_downstream_actionable(op_id)--------------->|   [view]
     |             |--get_ruling(op_id)---------------------------->|   [view]
     |             |                    |
     |             |  payload, actionability, excerpt and
     |             |  evidence subject/digest must all pass,
     |             |  or the tx reverts
     |<--balance updated, PaymentExecuted emitted-------------------|
```

### Evidence acquisition

```
procurement attestor --attest_vendor(subject,status,valid_until,uri,digest)--> guard
risk attestor        --attest_risk  (same shape)----------------------------> guard
signer A/B           --approve_payment(operation_id)------------------------> guard

judge_operation:
  vendor = current claim for operation.subject       (role, status, expiry, digest checked)
  risk   = current claim for operation.subject       (same)
  approvals = configured signers whose stored binding_digest == operation.binding_digest
  evidence_digest = H("evidence", subject, vendor.claim_digest, risk.claim_digest,
                      approval_count, canonical(approval_signers), binding_digest)
  ruling.evidence_digest = evidence_digest
```

The `source_uri` and `source_digest` fields are audit anchors. The guard does not
fetch them. The provenance is the attestor's on-chain transaction, not the URL.

Every internal message uses `emit(on="finalized")`. The std lib documents
`accepted` as producing undesired results and makes `finalized` the default;
`scripts/preflight.py` asserts the literal is present.

## Trust boundaries

1. **The model.** Untrusted. Constrained to a verdict enum and an excerpt that
   the contract verifies. Its reasoning is never recorded and never trusted.
2. **The validator set.** The seam fails closed on disagreement. That the
   GenVM runtime actually drops such a transaction is the largest unproven
   assumption in this project — see `docs/THREAT_MODEL.md` §6.1.
3. **The covenant owner.** Can propose amendments. Cannot activate one alone.
4. **Vault proposers.** Permissionless. The covenant decides, not the proposer.
5. **The vault's own binding.** Trusted because the vault compares the guard's
   copy against what it submitted.
6. **Procurement and risk attestors.** Trusted to write truthful claims for their
   role. They are fixed at deployment and cannot be impersonated; their honesty
   is a governance assumption, not something the contract proves.
7. **Payment signers.** Trusted to approve only what they intend. An approval is
   bound to the exact operation binding and cannot be replayed onto another.

## No backend

There is no service, no indexer, and no signer. The contracts are the whole
system. `covctl` is an offline developer tool: it reads a file and prints text.

## Open design decisions

Not decided, and deliberately so:

- **Multi-guard routing.** A vault may need different policies for inflows and
  outflows. The primitive does not express that yet.
- **Machine-readable thresholds.** The linter can flag a threshold with no unit,
  but nothing enforces that the *model* parses it correctly. A structured
  covenant format is the obvious answer and is not built.
- **Ruling appeal.** A party blocked by a ruling has no recourse. Adding one
  would need a second judge, which §"Frozen design decisions" currently forbids.
  Those two are in tension and the tension is unresolved.
- **Attestor rotation and compromise.** The four evidence roles are immutable.
  There is no rotation, revocation or threshold. A compromised attestor key can
  lie until the guard is redeployed. This is a deliberate scope limit for the
  demo, not a claim of production readiness.
