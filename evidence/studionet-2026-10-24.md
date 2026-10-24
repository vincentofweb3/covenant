# Studionet run — 2026-10-24

Amended deployment and full demonstration of Covenant. Every transaction below
reached `FINALIZED` with consensus result `MAJORITY_AGREE`; the five enforcement
outcomes matched expectations exactly. This is the run to cite for the
evidence-provenance claim.

Raw data: [`live-run.json`](live-run.json) — 39 transactions, 37 reads.
Historical pre-amendment run: [`studionet-2026-10-20.md`](studionet-2026-10-20.md)
and [`live-run-2026-10-20.json`](live-run-2026-10-20.json).

## Network

| | |
|---|---|
| RPC | `https://studio.genlayer.com/api` |
| Chain id | `61999` |
| Runtime | `py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6` |
| Deployer | `0x158e5ccAA39F7Db714B35Ae5fb6A5d925BE219cD` |
| Deployer balance | **0 wei** (studionet is gasless) |

## Deployed

| Contract | Address |
|---|---|
| `covenant_guard` | `0xaBe274C9429F025419cAf7C00c007c4c6B3af24e` |
| `treasury_vault` | `0x56d62A2526D5d5f3DA5C3c51790fac43D9414D4d` |

Constructor: covenant from `examples/covenant.md`, quorum `2`, timelock `60`s,
procurement attestor `0x9494488e…`, risk attestor `0xe4634c36…`, signers
`0x158e5cca…` and `0x3e20ab25…`.

Covenant v1 hash:
`fea742a0c51497a7288181070698f403f162a89fb8c16d53688bb4a0c2bf9b5e`.

The guard read this hash itself during setup (`get_covenant_hash`), so the
deployed hash is recorded in the evidence rather than inferred from source.

## Facts the requester did not supply

The procurement attestor wrote a vendor claim for `0x158e5cca…` with status
`approved`; the risk attestor wrote a `clear` claim for the same subject. A second
vendor subject `0x9999…` received a `not_approved` vendor claim and a `clear` risk
claim.

Each claim carries a `source_uri` and `source_digest`. These are **audit anchors,
not fetched inputs**; the guard authenticates the role key and the claim record,
not the external document. Claims expire after 30 days and are keyed by subject.

The requester only proposed operations: it never supplied a vendor status, risk
status, source URI, source digest, or approval list.

## Enforcement results

| Step | Amount | Applicant-supplied facts | Verdict | Guard state | Vault status | Moved |
|---|---|---|---|---|---|---|
| `op-small` | 900 | memo only | ALLOWED | FINALIZED | EXECUTED | −900 |
| `op-signed` | 900 | memo only; two signer approvals | ALLOWED | FINALIZED | EXECUTED | −900 |
| `op-big` | 120,000 | memo only; no approvals | VIOLATED | REJECTED | REQUESTED | 0 |
| `op-prohibited` | 500 | memo only | VIOLATED | REJECTED | REQUESTED | 0 |
| `op-after` | 120,000 | memo only; no approvals | ALLOWED | FINALIZED | EXECUTED | −120,000 |

Rulings:

- `op-small` and `op-signed` cite clause 2:
  `Payments of 50000 GEN or less to an approved vendor are permitted.`
- `op-big` cites clause 3:
  `A payment above 50000 GEN requires approval from two distinct signers and is permitted once both have approved.`
  Reason code `POLICY_DENIAL`; no approvals were recorded.
- `op-prohibited` cites clause 4:
  `Any counterparty not on the approved vendor list is prohibited.`
  Reason code `POLICY_DENIAL`; the current vendor claim was `not_approved`.
- `op-after` cites clause 2 under v2:
  `Payments of 250000 GEN or less to an approved vendor are permitted.`

`op-signed` carries `approval_count: 2` and signers
`0x158e5cca…` and `0x3e20ab25…`; the approval is bound to the exact operation
binding. The payment is independently permitted under clause 2 because it is
below the threshold. The above-threshold approval-required path is covered
deterministically by `tests/direct/test_evidence_provenance.py` and
`tests/integration/test_lifecycle.py`.

## Amendment

The guard amended itself by quorum + timelock:

- proposal replaces `50000 GEN` with `250000 GEN` in the stored covenant;
- two distinct signers approve;
- the 60-second timelock elapses;
- `activate_amendment` changes version 1 → 2.

Covenant v2 hash:
`9aec91476aa33aa414cc355313addd23b17a871ba24e8ab42cec570e8d433275`.

The identical 120,000 payment that was `VIOLATED` under v1 is `ALLOWED` under v2.
Enforcement tracks the covenant actually in force, not a snapshot taken at
deploy time.

## Balances

```
deposit                    1,000,000
op-small                       −900
op-signed                      −900
op-big                           +0   (refused)
op-prohibited                    +0   (refused)
op-after                   −120,000
                         -----------
balance                      878,200
```

`get_totals` returned
`{"balance":878200,"deposits_total":1000000,"outflow_total":121800}`. The two
refused payments left the vault balance unchanged; their vault payment status
remains `REQUESTED` while the guard state is `REJECTED`.

## Consensus

39/39 transactions were `FINALIZED` with `MAJORITY_AGREE`; 38 settled in one
round, and the `op-big` judge settled in three. The guard's four granular states
are `FINALIZED`, `REJECTED`, `UNDETERMINED`, and `RULED`; no transaction here
ended `UNDETERMINED`.

## A policy-wording finding, kept honestly

Earlier runs used a clause that permitted recurring payments and a separate
clause that required approvals above the threshold. On a 120,000 payment with
two approvals, validators split between those overlapping permissions, producing
`MAJORITY_DISAGREE` and no ruling. That failed closed — no funds moved — but it
was not a clean `ALLOWED`/`VIOLATED` demonstration.

The covenant was rewritten to make the policy disjoint by amount: at or below
the threshold is permitted to an approved vendor; above it requires two signers.
After that change, the denial and the post-amendment permission both settled with
a ruling. This is a policy-authoring constraint, not a property of the evidence
registry: ambiguous prose can prevent consensus even when the facts are
authenticated.

## Limits

- The contract proves which role wrote a claim and when, not that the claim is
  true. Attestor keys are immutable, with no rotation or revocation.
- Evidence is a ruling-time snapshot; expiry is not re-checked at execution.
- The external sources were not fetched. `source_uri` and `source_digest` are
  audit anchors only.
- Five live rulings are evidence that this run worked, not a reliability claim
  for the consensus protocol or the model.
