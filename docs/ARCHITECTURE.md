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
covenant_text         str                  active covenant, exact bytes
covenant_hash         str                  sha256 over (HASH_VERSION, "covenant", text)
covenant_version      u32
covenant_history      DynArray[str]        {version, hash} and {version, hash, superseded_by}
quorum                u32                  >= 2
timelock_seconds      u32                  in [60, 604800]

operations            TreeMap[str, str]   operation_id -> canonical JSON
operation_order       DynArray[str]
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
     |--request_payment(op_id, amount, recipient, memo, evidence)-->|
     |             |--propose_operation(op_id, BINDING, evidence)-->|   [finalized]
     |             |                    |
     |--guard.judge_operation(op_id)------------------------------>|   [consensus]
     |             |                    |  verdict + excerpt
     |             |                    |
     |--guard.finalize_operation(op_id)--------------------------->|
     |             |                    |
     |--vault.execute_payment(op_id)-->|                            |
     |             |--get_operation(op_id)------------------------>|   [view]
     |             |--is_downstream_actionable(op_id)--------------->|   [view]
     |             |--get_ruling(op_id)---------------------------->|   [view]
     |             |                    |
     |             |  all three must pass, or the tx reverts
     |<--balance updated, PaymentExecuted emitted-------------------|
```

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
