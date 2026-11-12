"""Environment diagnosis: randomness, hash seed, timezone and clock.

Two entry points:

* :func:`diagnose_seeds` for a flaky test already shown *not* to be about order.
  It runs the test alone and swaps one seed at a time between a failing and a
  passing run: if swapping only the ``random`` seed flips the result, randomness
  is the cause; if only ``PYTHONHASHSEED`` does, set/dict ordering is.
* :func:`confirm_probe` for a test that passed in the baseline run but failed
  under a timezone or clock probe (a "time bomb"). It re-runs the test alone
  under the probe and under the real clock, changing nothing else.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from flakehound.adapters.base import Runner
from flakehound.core.executor import Executor
from flakehound.core.models import Repro, RunResult, RunSpec
from flakehound.detectors.probes import Probe, describe_clock, local_offset_probe
from flakehound.diagnosis.common import failed, request, verify_repro

SEED_PAIRS = 2
PROBE_TRIALS = 2


class EnvCause(str, Enum):
    RANDOM_SEED = "random_seed"
    HASH_SEED = "hash_seed"
    SEEDS_COMBINED = "random_and_hash_seed"
    TIMEZONE = "timezone"
    CLOCK = "clock"
    TIMING = "timing"  # sleep used as a wait
    RACE = "race"  # thread interleaving
    NONDETERMINISTIC = "nondeterministic"
    INCONCLUSIVE = "inconclusive"


@dataclass
class EnvDiagnosis:
    nodeid: str
    cause: EnvCause
    confirmed: bool = False
    latent: bool = False  # passes in normal runs: a time bomb
    condition: str = ""  # e.g. "random seed 1234", "timezone UTC+14"
    evidence: list[str] = field(default_factory=list)
    fix: str = ""
    repro: list[str] = field(default_factory=list)
    repro_env: dict[str, str] = field(default_factory=dict)
    repro_verified: bool | None = None
    note: str = ""
    runs_used: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "cause": self.cause.value,
            "confirmed": self.confirmed,
            "latent": self.latent,
            "condition": self.condition,
            "evidence": self.evidence,
            "fix": self.fix,
            "repro": self.repro,
            "repro_env": self.repro_env,
            "repro_verified": self.repro_verified,
            "note": self.note,
            "runs_used": self.runs_used,
        }


_FIXES = {
    EnvCause.RANDOM_SEED: (
        "the test depends on unseeded randomness: seed it (`random.seed(...)` or pass a "
        "`random.Random(seed)` into the code) or assert on properties true for every value"
    ),
    EnvCause.HASH_SEED: (
        "the result depends on set/dict iteration order of strings, which changes with "
        "PYTHONHASHSEED: sort before comparing (e.g. `sorted(...)`) or compare as sets"
    ),
    EnvCause.SEEDS_COMBINED: (
        "the test depends on both unseeded randomness and set iteration order: seed the "
        "randomness and sort before comparing"
    ),
    EnvCause.NONDETERMINISTIC: (
        "it fails even with identical order and seeds: look for timing (`sleep`, threads), "
        "unseeded entropy (`uuid4`, `os.urandom`, `secrets`) or external state"
    ),
    EnvCause.TIMEZONE: (
        "the code mixes local time and UTC (e.g. `datetime.now()` or `date.today()` next to "
        "UTC values): use timezone-aware datetimes consistently, e.g. "
        "`datetime.now(timezone.utc)`"
    ),
}

_TZ_SETTING_FIX = (
    "the test (or the code) behaves differently when the `TZ` environment variable is set: "
    "make it independent of `TZ`, or set/unset `TZ` explicitly in the test "
    '(e.g. `monkeypatch.delenv("TZ", raising=False)`)'
)

_CLOCK_FIXES = {
    "midnight": (
        "the test reads the clock more than once or assumes the date can't change while it "
        "runs: read 'now' once (or inject a clock) and use fixed dates in tests"
    ),
}
_DATE_FIX = (
    "date arithmetic breaks {label}: use `timedelta` (or `dateutil.relativedelta`) "
    "instead of editing day/month/year fields directly, and test with fixed dates"
)


def classify_pair(
    fail_seeds: tuple[bool, bool], pass_seeds: bool, swap_hash: bool, swap_random: bool
) -> EnvCause:
    """Decide the cause from one failing/passing seed pair (True = test failed).

    ``fail_seeds``: two runs alone with the failing run's random seed + hash seed.
    ``pass_seeds``: one run alone with the passing run's seeds.
    ``swap_hash``: failing run's random seed, passing run's hash seed.
    ``swap_random``: passing run's random seed, failing run's hash seed.
    """
    if not (all(fail_seeds) and not pass_seeds):
        return EnvCause.NONDETERMINISTIC  # same seeds, different results
    if swap_hash and not swap_random:
        return EnvCause.RANDOM_SEED  # failing random seed is enough
    if swap_random and not swap_hash:
        return EnvCause.HASH_SEED  # failing hash seed is enough
    if not swap_hash and not swap_random:
        return EnvCause.SEEDS_COMBINED  # needs both
    return EnvCause.INCONCLUSIVE  # either one alone is enough: contradictory


def diagnose_seeds(
    victim: str, shake_runs: Sequence[RunResult], executor: Executor, runner: Runner
) -> EnvDiagnosis:
    start = executor.runs_used
    diag = _diagnose_seeds(victim, shake_runs, executor, runner)
    diag.runs_used = executor.runs_used - start
    return diag


def _diagnose_seeds(
    victim: str, shake_runs: Sequence[RunResult], executor: Executor, runner: Runner
) -> EnvDiagnosis:
    failing = [r.spec for r in shake_runs if failed(r, victim) is True]
    passing = [r.spec for r in shake_runs if failed(r, victim) is False]
    pairs = list(zip(failing, passing, strict=False))[:SEED_PAIRS]
    if not pairs:
        return EnvDiagnosis(victim, EnvCause.INCONCLUSIVE, note="not enough runs to compare")

    alone = [victim]
    reqs = []
    for f, p in pairs:
        reqs += [
            request(f, alone, label="seeds-fail"),
            request(f, alone, label="seeds-fail"),
            request(p, alone, label="seeds-pass"),
            request(f, alone, hash_seed=p.hash_seed, label="swap-hash"),  # random seed from F
            request(p, alone, hash_seed=f.hash_seed, label="swap-random"),  # hash seed from F
        ]
    results = [failed(r, victim) for r in executor.run_many(reqs)]

    verdicts: list[EnvCause] = []
    same_fail = same_total = 0
    for i in range(len(pairs)):
        a1, a2, b, c, d = results[i * 5 : i * 5 + 5]
        if None in (a1, a2, b, c, d):
            continue
        same_total += 2
        same_fail += int(bool(a1)) + int(bool(a2))
        verdicts.append(classify_pair((bool(a1), bool(a2)), bool(b), bool(c), bool(d)))

    if not verdicts:
        return EnvDiagnosis(victim, EnvCause.INCONCLUSIVE, note="the test could not be run alone")
    first = pairs[0][0]
    diag = EnvDiagnosis(victim, verdicts[0])
    agree = all(v is verdicts[0] for v in verdicts)
    if not agree:
        diag.cause = EnvCause.INCONCLUSIVE
        diag.note = "different seed pairs pointed to different causes"
        return diag
    if diag.cause is EnvCause.NONDETERMINISTIC:
        diag.confirmed = True
        diag.evidence = [
            f"alone, with the exact seeds of a failing run: failed {same_fail}/{same_total} "
            "(not every time), so the seeds don't decide it"
        ]
        diag.fix = _FIXES[EnvCause.NONDETERMINISTIC]
        return diag
    if diag.cause is EnvCause.INCONCLUSIVE:
        diag.note = "swapping either seed alone made it fail"
        return diag

    n = len(verdicts)
    diag.evidence = [
        f"alone, with a failing run's seeds: failed {same_fail}/{same_total}; "
        f"with a passing run's seeds: passed {n}/{n}",
    ]
    if diag.cause is EnvCause.RANDOM_SEED:
        diag.condition = f"random seed {first.seed}"
        diag.evidence.append(
            f"changing only the random seed flips the result; changing only PYTHONHASHSEED "
            f"does not ({n}/{n} seed pairs)"
        )
        repro = Repro([victim], seed=first.seed)
    elif diag.cause is EnvCause.HASH_SEED:
        diag.condition = f"PYTHONHASHSEED={first.hash_seed}"
        diag.evidence.append(
            f"changing only PYTHONHASHSEED flips the result; changing only the random seed "
            f"does not ({n}/{n} seed pairs)"
        )
        repro = Repro([victim], env={"PYTHONHASHSEED": str(first.hash_seed)})
    else:
        diag.condition = f"random seed {first.seed} with PYTHONHASHSEED={first.hash_seed}"
        diag.evidence.append(
            f"it fails only when both seeds come from a failing run ({n}/{n} seed pairs)"
        )
        repro = Repro([victim], env={"PYTHONHASHSEED": str(first.hash_seed)}, seed=first.seed)
    diag.fix = _FIXES[diag.cause]
    diag.repro = runner.repro_command(repro)
    diag.repro_env = dict(repro.env)
    diag.repro_verified = verify_repro(runner, repro)
    diag.confirmed = diag.repro_verified
    return diag


def confirm_probe(
    victim: str,
    probes: Sequence[Probe],
    baseline: RunSpec,
    executor: Executor,
    runner: Runner,
) -> EnvDiagnosis | None:
    """``probes``: the probes under which ``victim`` failed in its full-suite probe run.

    Returns None when no probe failure happens again with the test run alone: a
    one-off failure (e.g. a timeout on a busy machine) is not a time bomb.
    """
    start = executor.runs_used
    alone = [victim]
    reqs = [request(baseline, alone, label="probe-baseline")]
    for probe in probes:
        reqs += [
            request(
                baseline,
                alone,
                env={**baseline.env, **probe.env},
                clock=probe.clock,
                label=f"probe-{probe.name}",
            )
            for _ in range(PROBE_TRIALS)
        ]
    control = local_offset_probe() if any(p.kind == "timezone" for p in probes) else None
    if control is not None:
        reqs += [
            request(
                baseline,
                alone,
                env={**baseline.env, **control.env},
                label="probe-tz-control",
            )
            for _ in range(PROBE_TRIALS)
        ]
    results = [failed(r, victim) for r in executor.run_many(reqs)]
    base = results[0]
    control_outs = results[1 + len(probes) * PROBE_TRIALS :]
    confirmed_probes: list[Probe] = []
    evidence = []
    for i, probe in enumerate(probes):
        outs = results[1 + i * PROBE_TRIALS : 1 + (i + 1) * PROBE_TRIALS]
        fails = sum(bool(o) for o in outs)
        valid = sum(o is not None for o in outs)
        evidence.append(f"alone, {_probe_condition(probe)}: failed {fails}/{valid}")
        if valid and fails == valid:
            confirmed_probes.append(probe)

    probe_outs = results[1 : 1 + len(probes) * PROBE_TRIALS]
    if base is False and False in probe_outs and True not in probe_outs:
        return None
    main = confirmed_probes[0] if confirmed_probes else probes[0]
    cause = EnvCause.TIMEZONE if main.kind == "timezone" else EnvCause.CLOCK
    diag = EnvDiagnosis(victim, cause, latent=True)
    labels = [p.label for p in (confirmed_probes or probes)]
    diag.condition = (
        labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]
    )
    diag.evidence = [
        "passed in the normal run, failed in the same run with only "
        f"{'the timezone' if cause is EnvCause.TIMEZONE else 'the clock'} changed",
        *evidence,
    ]
    if base is True:
        diag.note = (
            "it also fails alone with the real clock (it needs other tests), so this is unconfirmed"
        )
    elif base is False:
        diag.evidence.append("alone, with the real clock and timezone: passed")
    diag.fix = (
        _FIXES[EnvCause.TIMEZONE]
        if cause is EnvCause.TIMEZONE
        else _CLOCK_FIXES.get(main.name, _DATE_FIX.format(label=main.label))
    )
    tz_setting = False
    if control is not None and control_outs:
        fails = sum(bool(o) for o in control_outs)
        valid = sum(o is not None for o in control_outs)
        diag.evidence.append(
            f"alone, {control.label} ({control.env['TZ']}): failed {fails}/{valid}"
        )
        tz_setting = cause is EnvCause.TIMEZONE and valid > 0 and fails == valid
    if tz_setting:
        # Same offset as now, still fails: the time difference is not the problem.
        diag.condition = "when the TZ environment variable is set (even to the current offset)"
        diag.note = (
            "it also fails with TZ set to this machine's own offset, so the cause is TZ being "
            "set (or its format), not the time difference"
        )
        diag.fix = _TZ_SETTING_FIX
    elif confirmed_probes and base is False:
        repro = Repro(alone, env=dict(main.env), clock=main.clock)
        diag.repro = runner.repro_command(repro)
        diag.repro_env = dict(repro.env)
        diag.repro_verified = verify_repro(runner, repro)
        diag.confirmed = diag.repro_verified
    diag.runs_used = executor.runs_used - start
    return diag


def _probe_condition(probe: Probe) -> str:
    if probe.clock:
        return f"clock at {describe_clock(probe.clock)}"
    return probe.label
