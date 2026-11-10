from flakehound.core.models import Outcome, RunResult, RunSpec, TestResult
from flakehound.detectors.shake import Verdict, analyze, plan_runs, summarize_failure

IDS = [f"t{i}" for i in range(6)]


def test_plan_is_deterministic_and_complete():
    a = plan_runs(IDS, 5, master_seed=42)
    b = plan_runs(IDS, 5, master_seed=42)
    assert a == b
    assert a[0].order == IDS  # baseline keeps the default order
    for spec in a:
        assert sorted(spec.order) == sorted(IDS)
    assert len({tuple(s.order) for s in a}) > 1  # actually shuffled
    assert len({s.seed for s in a}) == 5
    assert plan_runs(IDS, 5, master_seed=43) != a


def _run(run_id: int, outcomes: dict[str, Outcome], message: str = "") -> RunResult:
    spec = RunSpec(run_id=run_id, order=list(outcomes), seed=run_id)
    results = [TestResult(n, o, 0.01, message if o.is_failure else "") for n, o in outcomes.items()]
    return RunResult(spec, results, 0, 0.1)


def test_verdicts():
    p, f, s = Outcome.PASSED, Outcome.FAILED, Outcome.SKIPPED
    runs = [
        _run(0, {"flaky": p, "broken": f, "stable": p, "skip": s}),
        _run(1, {"flaky": f, "broken": f, "stable": p, "skip": s}),
        _run(2, {"flaky": p, "broken": Outcome.ERROR, "stable": p, "skip": s}),
    ]
    stats = {x.nodeid: x for x in analyze(["flaky", "broken", "stable", "skip", "gone"], runs)}
    assert stats["flaky"].verdict is Verdict.FLAKY
    assert stats["flaky"].failing_runs == [1]
    assert stats["broken"].verdict is Verdict.BROKEN
    assert stats["stable"].verdict is Verdict.STABLE
    assert stats["skip"].verdict is Verdict.SKIPPED
    assert stats["gone"].verdict is Verdict.NOT_RUN
    # Flaky tests are listed first.
    assert analyze(["stable", "flaky"], runs)[0].nodeid == "flaky"


def test_failures_grouped_by_location():
    msg1 = "def test():\n>   assert x < 0.8\nE   assert 0.83 < 0.8\n\nt.py:5: AssertionError"
    msg2 = msg1.replace("0.83", "0.95")
    key1, example = summarize_failure(msg1)
    assert key1 == summarize_failure(msg2)[0]
    assert example == "assert 0.83 < 0.8  (t.py:5)"
    runs = [_run(0, {"t": Outcome.FAILED}, msg1), _run(1, {"t": Outcome.FAILED}, msg2)]
    assert analyze(["t"], runs)[0].top_messages() == [("assert 0.83 < 0.8  (t.py:5)", 2)]
