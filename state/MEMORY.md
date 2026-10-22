# MEMORY — verified GenLayer facts

Every line here was **read off a real artifact on this machine**, not recalled from
documentation. Date verified: 2026-10-15. If a line below conflicts with what the
contract does at runtime, the runtime wins and this file is wrong — fix this file.

## 0. The most expensive trap

`import genlayer` **does not work on the host**. It fails with `ModuleNotFoundError`
under `/usr/bin/python3.12`. That is expected, not a broken install.

- The **client** SDK is installed as `genlayer_py` (e.g. `genlayer_py.chains.studionet`).
- The **contract-side** `genlayer` module only exists inside the GenVM std lib bundle.
  It cannot be introspected from a normal REPL. Read the files instead.

Do not spend time trying to `import genlayer` to discover the API. Read the std lib.

## 1. Where the contract-side std lib actually lives

```
~/.cache/gltest-direct/extracted/v0.3.0-rc7/py-lib-genlayer-std/<hash>/genlayer/
```

Two **different API generations** are cached. They are not interchangeable.

| | hash | API generation | target header |
|---|---|---|---|
| OLD | `11rhn002yfajawsz7fai6mykznbxkxs6l91iskj5cm82c92qhy3v` | `genlayer.gl.*` | `1jb45aa8…` |
| NEW | `10pqy9vk4a8w8pg25py83s23k3mjjy7dwpdqjvqggb9ms7ycipvh` | `genlayer.contract.Contract` | `5jycge4q…` |

**This project uses the OLD generation.** Rationale in `PLAN.md`.

## 2. OLD-generation API — the exact names that exist

Import surface (`genlayer/__init__.py`, OLD):

```python
from genlayer import gl, allow_storage, TreeMap, DynArray, Array, Address, u8..u256, bigint
```

`gl.Contract` is the base class. `gl.contract.Contract` does **not** exist here.

Decorators (`genlayer/gl/annotations.py`): `gl.public`, `gl.private`, and
`gl.public.write`, `gl.public.write.payable`, `gl.public.view`, `gl.public._min_gas`.

Consensus boundary (`genlayer/gl/vm.py`):

```python
def run_nondet[T](
    leader_fn: typing.Callable[[], T],
    validator_fn: typing.Callable[[Result[T]], bool],
    /, *,
    compare_user_errors: ... = lambda a, b: a.message == b.message,
    compare_vm_errors:  ... = lambda a, b: a.message == b.message,
) -> Lazy[T]: ...
```

`run_nondet` deliberately does **not** sandbox the validator. A validator that raises
is treated the same as one that returns `False` — fail-closed, which is what we want.
Also present: `run_nondet_unsafe`, `run_nondet_default`, `spawn_sandbox`, `unpack_result`,
`gl.vm.Return`, `gl.vm.UserError`, `gl.vm.VMError`.

Note: knot's `MEMORY.md` claims `run_nondet_unsafe` does not exist. That statement is
about the **v0.6 std lib**, and it is **wrong for the OLD lib** — here it does exist.
That file is not authoritative for us.

LLM access (`genlayer/gl/nondet/__init__.py`) — overloads, all keyword-only after prompt:

```python
exec_prompt(prompt, *, response_format: Literal['text'], images=None) -> Lazy[str]
exec_prompt(prompt, *, response_format: Literal['json'], image=None)     -> Lazy[dict]
```

These are **eager wrappers that return values directly**. Never call `.get()` on them.
The `json` overload is oddly typed (`image=` singular) — a sign it is less exercised.
knot works around this by using `response_format='text'` and parsing JSON itself.
That workaround is worth copying; see `PLAN.md`.

Message context: `gl.message.sender_address`, `.contract_address`, `.origin_address`,
`.value`, `.chain_id`, plus `gl.message_raw` (the raw dict, which carries `datetime`).

Cross-contract: `gl.get_contract_at(addr)`, `gl.deploy_contract`, `gl.contract_interface`,
`gl.Event`. Events: `gl.Event` (OLD) — the NEW lib uses `gl.chain.Event`.

`genlayer/gl/eq_principle.py` **also exists in the OLD lib**, lazily loaded. Unverified
against a live validator set. Treat as available-to-probe, not as a known-good path.

## 3. NEW-generation API — for reference only, not our target

`gl.contract.Contract`, `gl.chain.Event`, `gl.storage.allow`, `gl.contract.get_at`,
`gl.contract.deploy`, `gl.contract.interface`, and notably:

```
genlayer/eq_principle/__init__.py:  strict_eq | prompt_comparative | prompt_non_comparative
```

`prompt_comparative` is the canonical GenLayer way to express "LLM judges, validators
independently compare". Neither friend used it; both hand-rolled `run_nondet`.
Worth reading as a design reference, not as our dependency.

## 4. APIs that do NOT exist (checked in BOTH generations)

- `gl.eq_principle_prompt_comparative`
- `gl.exec`
- `gl.message.send_to_contract`
- `gl.Contract` (absent from the NEW lib; present only in OLD)
- `from genlayer import *` works on OLD only

If a plan, a tutorial, or an AI assistant suggests any of these, it is hallucinating.

## 5. Toolchain on this machine

```
python      3.12.3  /usr/bin/python3.12
genlayer-py     0.16.3   (site-packages)
genlayer-test   0.29.2   (gltest, gltest_cli, glsim present)
genvm-linter    0.11.0   (on PATH)
```

A second, **divergent** env exists at `legxusai/.venv` with genlayer-py **0.18.0**.
Do not assume the two agree. Pin per-project.

Newer on PyPI (unused so far): `genlayer-py 0.19.0rc2`, `genlayer-test 0.30.0-rc2`
(both uploaded 2026-09-03). Upgrading is a separate, optional decision.

Chains the installed SDK knows:

| alias | chain id |
|---|---|
| `studionet` | 61999 |
| `localnet` | 61999 |
| `testnet_asimov` | 4221 |
| `testnet_bradbury` | 4221 |

`studio_devnet` (61997) is **unknown** to this SDK. It is a separate preview network and
we are not targeting it.

## 6. The `genlayer` CLI is not usable here

`genlayer` 0.39.2 is the Windows/npm copy under WSL. It hangs, and it stores keys in an
OS keychain it cannot reach (`OS keychain is not available`), so it **cannot sign**. Drive
the network from `genlayer-py` instead. Expect urllib's default User-Agent to be
rejected with HTTP 403 by GenLayer hosts — set an explicit User-Agent.

## 7. Deploying always needs a fee distribution

A deploy with no explicit fee distribution reverts with `FeesDistributionMissing`. Call
the SDK's fee-estimation path before every send. Fee deposit observed on studio-dev was
`100000000000010352` wei; studionet's profile must be read, not assumed.

## 8. Read path is exact-match on the address string

`gen_call` compares the stored address **string** exactly. A non-checksummed or
differently-cased address returns `Contract not found` for a contract that demonstrably
exists. EIP-55 normalise every address in every script, and make the read step
**required**, not optional — a silent skip there hides your best evidence.

Never trust a transaction's `result` field as the contract's return value.

## 9. Target and its provenance

- Network: **studionet**, RPC `https://studio.genlayer.com/api`, chain id **61999**.
- Contract header: `# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }`

This exact header has already been deployed to this exact chain twice by this author:
`docket` (`0xb1a3778a3B11E0eD000bB24DF06108caDfd7729B`) and
`legxusai` (`0x66f24cDAde89f68F0040387815B4aE29e437785c`). It is the lowest-risk
option available and is chosen for that reason, not for modernity.

## 10. ANSWERED 2026-10-15 by the Phase 0 probe

Every question below is now settled empirically. The probe lives in `probe/` and
its four commands are recorded verbatim in `state/PROJECT-STATE.md`.

1. **Does `genvm-lint check` accept this contract shape? YES.** But it does not
   work out of the box — see the trap below. With `GENVM_VERSION=v0.3.0-rc7` it
   validates a 7-method contract against the real std lib.

2. **Do the `gltest` direct fixtures run? YES**, on Python 3.12.3, but only via
   the `sdk_version=` kwarg on `direct_deploy`. There is no env var that does it.

3. **Does `mock_llm` auto-parse bare JSON? MOOT, by design.** We use
   `response_format='text'` and parse the brace ourselves, so the mock layer's
   behaviour never matters. Mocks are plain strings and prose-plus-JSON works.

4. **Does `gl.get_contract_at(...).emit(on='finalized')` work? YES**, in the OLD
   std lib and on glsim. `emit()`'s `on` parameter **defaults to `'finalized'`**
   and the std lib docstring explicitly warns against `'accepted'`. See §11.

5. **Does `gl.message_raw['datetime']` exist? YES** — it is a typed field in
   `MessageRawType`. **But** direct mode never refreshes it; see §11.

## 11. The trap that will cost you the most time

**`genvm-lint check` picks the WRONG GenVM bundle by default.**

`resolve_version()` returns `GENVM_VERSION` env var, else the newest *cached*
tarball, else the latest release. This machine has three cached, and the newest
is `genlayerlabs-genvm-manager-v0.6.0-rc2` — the **v0.6 generation**, which does
not ship the OLD runner. The default therefore fails:

```
✗ Failed to load SDK: "filename 'runners/py-genlayer/1j/b45aa8….tar' not found"
```

The tarball that *does* ship it is `v0.3.0-rc7`:

```
runners/py-genlayer/1j/b45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6.tar
runners/py-genlayer/1z/r6nqk597d97kg0dyxg0shhrykx5v02zjgnyrajapy4wlqvfvwh.tar
```

**Always pass `GENVM_VERSION=v0.3.0-rc7`.** Two independent mechanisms need it:
the `genvm-lint check` env var, and the `direct_deploy(..., sdk_version=...)`
kwarg. Setting one does nothing for the other.

A prior project of this author hit the same trap from the other direction, and
carried a `check_env.py` solely to police it. Check the resolved bundle before
assuming an environment problem is real.

Note there is a **newer OLD-generation runner**, `1zr6nqk5…`, in the same tarball.
Untested here. Staying on `1jb45aa8…` because it is the one already proven
deployed to studionet twice.

**2026-10-22: the release asset was renamed.** `genvm-universal.tar.xz` was
withdrawn from the v0.3.0-rc7 release. `genvm-runners-all.tar.xz` is the same
134,842,512-byte file (sha256 `e218a1854214681560351051f76fe2b878545cf3409455ef372d57014a88ca67`).
`gltest` 0.29.2 still requests the old name, so CI stages the new asset at
`~/.cache/gltest-direct/genvm-universal-v0.3.0-rc7.tar.xz` and verifies that
sha256 before any deploy. Local caches were unaffected.

## 12. Five direct-mode constraints, all observed, none documented anywhere

These are properties of `gltest` 0.29.2 direct mode, established by probe:

1. **`DynArray()` and `TreeMap()` raise `TypeError` if user code constructs them.**
   Storage collections are runtime-allocated: declare the annotated class field
   and use it. Never assign it in `__init__`.

2. **One contract class per process.** `genlayer/gl/genvm_contracts.py` keeps a
   module-level `__known_contract__` and raises `only one contract is allowed`.
   Reset it before each deploy (`contracts.__known_contract__ = None`). glsim
   resets it itself but still fails with a *different* message on a second
   deploy (`class is not marked for usage within storage, please, annotate it
   with @allow_storage`) because it caches classes and re-allocates them.

3. **Deploying a second contract disturbs the first contract's storage.**
   Observed: `callee.call_count()` goes `0 → 1` purely by allocating another
   contract. **Therefore: in a direct-mode test, assert only on the contract you
   deployed in that test.**

4. **Cross-contract calls are undeliverable.** Views are traced as
   `Unknown gl_call request type: ['CallContract']` and return `None`; writes as
   `['PostMessage']`. The std lib substitutes `None` silently.

   **This is also a real contract bug waiting to happen:** `str(<failed cross-
   contract read>)` yields the plausible-looking string `"None"`. Every
   cross-contract read must null-check *before* coercing to `str`.

5. **Direct mode does not run the validator.** `run_nondet` is patched to call
   `leader_fn()`, return its value, and stash `(result, leader_fn, validator_fn)`
   for later. You must call `direct_vm.run_validator()` yourself. Swap
   `mock_llm` patterns between the contract call and `run_validator()` to make
   the validator see different data — that is how disagreement is simulated.

**Consequence for the test plan:** direct mode is for single-contract state
machine and judge-seam logic. Anything involving two contracts — including the
central claim that the vault *refuses* to move funds without a ruling — is
provable only on glsim. Do not write a direct-mode test that implies otherwise.

## 13. The clock

`gl.message_raw['datetime']` is real and typed. But `VMContext.warp()` refreshes
only `sender_address` and `origin_address` (`_refresh_gl_message`); it never
touches `datetime`, so `warp()` does **not** move the value a contract reads.
Under glsim the clock is worse: `sim_setTime` exists but glsim syncs only
sender/value/chain_id into `message.raw`, so the raw key stays frozen at import
unless the harness writes it.

Any timelock in the real guard must be tested by writing `message.raw["datetime"]`
in place. Do not write a test that calls `warp()` and then asserts on a deadline.