from flakehound.diagnosis.ddmin import ddmin


def _oracle(culprits: set[int], calls: list[int]):
    """Reproduces when every culprit is present (like a combined polluter)."""

    def test_many(subsets: list[list[int]]) -> list[bool]:
        calls.append(len(subsets))
        return [culprits <= set(s) for s in subsets]

    return test_many


def test_finds_single_culprit():
    calls: list[int] = []
    assert ddmin(list(range(100)), _oracle({37}, calls)) == [37]
    assert sum(calls) < 60  # far fewer runs than trying all 100


def test_finds_combination():
    assert sorted(ddmin(list(range(40)), _oracle({3, 29}, []))) == [3, 29]


def test_result_is_one_minimal():
    culprits = {2, 5, 11}
    result = ddmin(list(range(16)), _oracle(culprits, []))
    assert set(result) == culprits
    for x in result:
        assert not culprits <= set(result) - {x}


def test_trivial_inputs():
    assert ddmin([7], _oracle({7}, [])) == [7]
    assert ddmin([], _oracle(set(), [])) == []
