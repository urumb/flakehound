"""Timing diagnosis: sleeps used as waits, and race conditions.

* **Short waits.** Run the test alone with its main-thread ``sleep`` calls 10x
  shorter. If it now fails (and passes normally), it uses ``sleep`` to wait for
  something that isn't guaranteed to be done, which is exactly what fails on a
  slow or busy CI machine. Then shorten one call site at a time to find *which*
  sleep it is.
* **Thread stress.** Run it alone with a tiny thread switch interval. If it
  fails much more often than normally, threads interleave in a way the code
  doesn't expect: a race condition.

Used both for tests that passed normally but failed in a timing probe run
(latent, "racy") and for flaky tests where seeds and order didn't explain it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from flakehound.adapters.base import Runner
from flakehound.core.executor import Executor
from flakehound.core.models import Repro, RunSpec
from flakehound.diagnosis.common import failed, request, verify_repro
from flakehound.diagnosis.env import EnvCause, EnvDiagnosis

SLEEP_SCALE = 0.1
SWITCH_INTERVAL = 1e-6
MAX_SITES = 5
SHORT_TRIALS = 2
NORMAL_TRIALS = 2
STRESS_TRIALS = 4
REPRO_ATTEMPTS = 3  # races are probabilistic: a repro may need a few tries

_SLEEP_FIX = (
    "`sleep({seconds:g})` at {site} is used to wait for work that isn't guaranteed to be "
    "finished; wait for the actual event instead: `thread.join(timeout)`, "
    "`threading.Event().wait(timeout)`, or poll the condition with a deadline"
)
_SLEEP_FIX_GENERIC = (
    "the test uses `sleep` to wait for work that isn't guaranteed to be finished; wait for "
    "the actual event instead: `thread.join(timeout)`, `threading.Event().wait(timeout)`, "
    "or poll the condition with a deadline"
)
_RACE_FIX = (
    "threads share state without proper locking: protect read-modify-write sequences with "
    "a `threading.Lock`, use thread-safe structures like `queue.Queue`, and join threads "
    "before asserting"
)


def _fails(results: Sequence[bool | None]) -> tuple[int, int]:
    valid = [r for r in results if r is not None]
    return sum(valid), len(valid)


def _sites_from(sleep_sites: Sequence[dict[str, Any]]) -> list[tuple[str, float]]:
    ranked = sorted(sleep_sites, key=lambda s: -float(s.get("seconds", 0)))
    return [(str(s["site"]), float(s.get("seconds", 0))) for s in ranked[:MAX_SITES]]


def check_short_waits(
    victim: str,
    spec: RunSpec,
    executor: Executor,
    runner: Runner,
    *,
    known_sites: Sequence[dict[str, Any]] = (),
    latent: bool,
) -> EnvDiagnosis | None:
    """Return a TIMING diagnosis if shorter sleeps make the test fail, else None."""
    alone = [victim]
    base = request(spec, alone, sleep_scale=None, sleep_sites=[], switch_interval=None)
    reqs = [base] * NORMAL_TRIALS + [
        request(
            spec,
            alone,
            sleep_scale=SLEEP_SCALE,
            sleep_sites=[],
            record_sleeps=True,
            switch_interval=None,
            label="short-waits",
        )
    ] * SHORT_TRIALS
    results = executor.run_many(reqs)
    outs = [failed(r, victim) for r in results]
    normal_fail, normal_total = _fails(outs[:NORMAL_TRIALS])
    short_fail, short_total = _fails(outs[NORMAL_TRIALS:])
    if not short_total or short_fail != short_total or normal_fail == normal_total:
        return None
    if latent and normal_fail:
        return None  # a latent issue must pass normally

    sites_info: list[dict[str, Any]] = list(known_sites)
    for r in results[NORMAL_TRIALS:]:
        sites_info += r.sleep_sites.get(victim, [])
    seen: dict[str, float] = {}
    for site, seconds in _sites_from(sites_info):
        seen[site] = max(seen.get(site, 0.0), seconds)
    sites = list(seen.items())[:MAX_SITES]

    culprits: list[tuple[str, float]] = []
    evidence = [
        f"alone, main-thread sleeps {1 / SLEEP_SCALE:g}x shorter: failed "
        f"{short_fail}/{short_total}; normal: failed {normal_fail}/{normal_total}"
    ]
    if len(sites) > 1:
        per_site = executor.run_many(
            [
                request(
                    spec,
                    alone,
                    sleep_scale=SLEEP_SCALE,
                    sleep_sites=[site],
                    switch_interval=None,
                    label="short-wait-site",
                )
                for site, _ in sites
                for _ in range(SHORT_TRIALS)
            ]
        )
        for i, (site, seconds) in enumerate(sites):
            f, t = _fails(
                [failed(r, victim) for r in per_site[i * SHORT_TRIALS : (i + 1) * SHORT_TRIALS]]
            )
            evidence.append(f"shortening only `sleep({seconds:g})` at {site}: failed {f}/{t}")
            if t and f == t:
                culprits.append((site, seconds))
    elif sites:
        culprits = sites  # the only sleep it makes

    diag = EnvDiagnosis(victim, EnvCause.TIMING, latent=latent)
    if culprits:
        site, seconds = culprits[0]
        diag.condition = f"when the `sleep({seconds:g})` at {site} is shorter (a slower machine)"
        diag.fix = _SLEEP_FIX.format(seconds=seconds, site=site)
    else:
        diag.condition = "when its sleeps are shorter (a slower machine)"
        diag.fix = _SLEEP_FIX_GENERIC
    diag.evidence = evidence
    repro = Repro(alone, sleep_scale=SLEEP_SCALE, sleep_sites=[c[0] for c in culprits])
    diag.repro = runner.repro_command(repro)
    diag.repro_verified = verify_repro(runner, repro)
    diag.confirmed = diag.repro_verified
    return diag


def check_thread_stress(
    victim: str, spec: RunSpec, executor: Executor, runner: Runner, *, latent: bool
) -> EnvDiagnosis | None:
    """Return a RACE diagnosis if thread-switch stress makes it fail clearly more often."""
    alone = [victim]
    normal = request(spec, alone, switch_interval=None, sleep_scale=None, sleep_sites=[])
    stressed = request(
        spec,
        alone,
        switch_interval=SWITCH_INTERVAL,
        sleep_scale=None,
        sleep_sites=[],
        label="thread-stress",
    )
    outs = [
        failed(r, victim)
        for r in executor.run_many([normal] * NORMAL_TRIALS + [stressed] * STRESS_TRIALS)
    ]
    n_fail, n_total = _fails(outs[:NORMAL_TRIALS])
    s_fail, s_total = _fails(outs[NORMAL_TRIALS:])
    if not s_total or not n_total:
        return None
    # Require a clear difference: at least half the stressed runs fail, and the
    # stressed failure rate is well above the normal one.
    if s_fail * 2 < s_total or s_fail / s_total < n_fail / n_total + 0.5:
        return None
    if latent and n_fail:
        return None
    diag = EnvDiagnosis(victim, EnvCause.RACE, latent=latent)
    diag.condition = "when threads switch more often (a race condition)"
    diag.evidence = [
        f"alone, with frequent thread switches: failed {s_fail}/{s_total}; "
        f"normal: failed {n_fail}/{n_total}"
    ]
    diag.fix = _RACE_FIX
    repro = Repro(alone, switch_interval=SWITCH_INTERVAL)
    diag.repro = runner.repro_command(repro)
    diag.repro_verified = any(verify_repro(runner, repro) for _ in range(REPRO_ATTEMPTS))
    diag.confirmed = diag.repro_verified
    return diag


def diagnose_timing(
    victim: str,
    spec: RunSpec,
    executor: Executor,
    runner: Runner,
    *,
    latent: bool,
    known_sites: Sequence[dict[str, Any]] = (),
    try_sleeps: bool = True,
    try_threads: bool = True,
) -> EnvDiagnosis | None:
    start = executor.runs_used
    diag = None
    if try_sleeps:
        diag = check_short_waits(
            victim, spec, executor, runner, known_sites=known_sites, latent=latent
        )
    if diag is None and try_threads:
        diag = check_thread_stress(victim, spec, executor, runner, latent=latent)
    if diag is not None:
        diag.runs_used = executor.runs_used - start
    return diag
