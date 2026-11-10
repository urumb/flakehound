import shop


def test_single_tag_renders():
    assert shop.render_tags({"python"}) == "python"


def test_addition():
    assert 1 + 1 == 2
