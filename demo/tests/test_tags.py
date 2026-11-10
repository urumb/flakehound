import shop


def test_tags_render_alphabetically():
    # Bug: sets have no defined order. Passes only for some hash seeds.
    assert shop.render_tags({"flaky", "pytest", "python"}) == "flaky,pytest,python"
