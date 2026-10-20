# The consensus protocol

## Question

How does a contract that reads natural language reach a decision that multiple
validators will agree on — and what happens when they do not?

## The decision shape

The model returns one JSON object. The contract normalises it immediately into
this record, which is what gets compared:

```json
{
  "verdict": "ALLOWED | VIOLATED | AMBIGUOUS",
  "reason_code": "CITED_CLAUSE | POLICY_DENIAL | EVIDENCE_INSUFFICIENT | ...",
  "excerpt": "a verbatim substring of the covenant, or empty",
  "input_digest": "sha256 over the frozen operation",
  "covenant_digest": "sha256 over the covenant in force"
}
```

Validators compare **normalised results**, never free-form reasoning. Two models
that reach the same conclusion by different reasoning agree; two that reach the
same words by different inputs do not, because the digests are ours rather than
theirs.

## Leader

```python
def leader_fn() -> dict:
    raw = gl.nondet.exec_prompt(prompt, response_format="text")
    return _normalize(raw, covenant_text, input_digest, covenant_digest)
```

Two deliberate choices:

- **`response_format="text"`, not `"json"`.** The contract parses the reply
  itself, with a brace scanner that respects string literals and handles fenced
  and prose-wrapped output. The SDK's JSON path is oddly typed and less
  exercised. Fenced replies are ordinary model behaviour and must not become a
  denial.
- **`_normalize` is total.** It cannot raise. Every input maps to a dict, and
  every unusable input maps to `AMBIGUOUS` / `MALFORMED_OUTPUT`. A model failure
  must never become a transaction error *and* must never become permission.

## Validator

```python
def validator_fn(leader_result) -> bool:
    if not isinstance(leader_result, gl.vm.Return):
        return False
    leader_data = leader_result.calldata
    if not isinstance(leader_data, dict):
        return False

    # The deterministic check, restated rather than trusted from the leader.
    excerpt = str(leader_data.get("excerpt", "")).strip()
    if leader_data.get("verdict") == VERDICT_ALLOWED and not _excerpt_is_verifiable(excerpt, covenant_text):
        return False
    if excerpt != "" and not _excerpt_is_verifiable(excerpt, covenant_text):
        return False

    own = leader_fn()          # independent re-derivation
    return (
        leader_data.get("verdict")   == own.get("verdict")
        and leader_data.get("reason_code") == own.get("reason_code")
        and leader_data.get("excerpt")      == own.get("excerpt")
        and leader_data.get("input_digest") == own.get("input_digest")
        and leader_data.get("covenant_digest") == own.get("covenant_digest")
    )
```

Three properties worth naming:

1. **The validator re-derives, it does not re-read.** It calls `leader_fn()`
   again and normalises its *own* model output. Agreement therefore means two
   independent judgements coincided, not that one judgement was echoed.
2. **The excerpt check is restated.** The leader already normalised it, so this
   is redundant by construction. It is written out anyway because the security
   property should be visible at the point of enforcement rather than inferred
   from a callee.
3. **It returns `bool` strictly.** `run_nondet` does not sandbox the validator:
   a validator that raises is treated as `False`, which is the fail-closed
   outcome we want. Returning a truthy non-bool is a type error and fails the
   transaction.

## Fail-closed policy

| Situation | Recorded | Blocks funds |
|---|---|---|
| `ALLOWED` with a verified excerpt | `ALLOWED` / `CITED_CLAUSE` | no |
| `ALLOWED` with an unverifiable excerpt | `AMBIGUOUS` / `UNVERIFIED_EXCERPT` | yes |
| `VIOLATED` | `VIOLATED` / `POLICY_DENIAL` | yes |
| `AMBIGUOUS` | `AMBIGUOUS` / `EVIDENCE_INSUFFICIENT` | yes |
| unparseable reply | `AMBIGUOUS` / `MALFORMED_OUTPUT` | yes |
| wrong digest echo | `AMBIGUOUS` / `DIGEST_MISMATCH` | yes |
| validator disagreement | *no ruling written* | yes |

`MODEL_FAILURE_REASONS` separates "the model failed" from "the evidence was
insufficient". Both block identically; only one is a finding about the operation.

## Two harness facts that constrain what can be claimed

Established by tracing `glsim`, not by reading its documentation. Both are in
`tests/integration/test_consensus.py` and `docs/THREAT_MODEL.md` §4.

1. **`sim_call` and `gen_call` never run consensus.** `_rpc_sim_call_sdk` calls
   `engine.call_method` directly and hardcodes `"status": "FINALIZED"`.
   `run_consensus` is reachable only from `eth_sendRawTransaction`.
   **A `FINALIZED` from `sim_call` is a constant.**
2. **The validator half of `run_nondet` is discarded by default.** The std lib
   sends `data_leader` and `data_validator`; the harness reads only the leader,
   so `_captured_validators` is empty and consensus is vacuous.

So `tests/integration/test_consensus.py` calls `glsim.consensus.run_consensus`
directly with its own `execute_fn`. That is the only way to cast real votes, and
it is how this project demonstrates:

- three validators agreeing → `FINALIZED`
- all three dissenting → `UNDETERMINED`, `No consensus after max rotations`
- one agreeing and two dissenting → `UNDETERMINED`, because a majority is 2 of 3

## Reliability testing: what exists and what does not

| Tested | How |
|---|---|
| leader and validator agree | `test_leader_and_validator_agree_on_allowed` |
| validator dissent is observable | `test_validator_disagrees_when_the_model_output_changes` |
| three validators agreeing | `test_three_validators_agreeing_finalizes_the_ruling` |
| unanimous dissent does not finalize | `test_a_validator_split_does_not_finalize` |
| a 1-of-3 minority does not finalize | `test_a_partial_split_still_fails_without_a_majority` |
| fabricated clause is rejected by the validator too | `test_the_validator_rejects_a_fabricated_excerpt_independently` |

Not tested, and not claimed:

- **Real network disagreement.** glsim is not the GenVM runtime.
- **Leader rotation across a real failure.** `max_rotations=1` everywhere.
- **Genuine multi-model disagreement.** Every model is a fixed reply function.
- **Cost, gas, or timing under real models.**

**Do not claim consensus reliability from one successful fixture.** Three green
tests on a simulator with one substituted model is a demonstration that the seam
is wired correctly. It is not evidence that real validators agree in production.
