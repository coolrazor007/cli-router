from cli_router.streamfmt import OutputCondenser


def test_thinking_blocks_are_collapsed_to_a_count():
    condenser = OutputCondenser()

    for line in ["before\n", "<thinking>\n", "step one\n", "step two\n", "</thinking>\n", "after\n"]:
        condenser.feed(line)

    assert condenser.lines == ["before", "thinking... (3 lines hidden)", "after"]
    assert "step one" not in "\n".join(condenser.lines)


def test_unified_diffs_are_collapsed_by_file_with_counts():
    condenser = OutputCondenser()

    for line in [
        "diff --git a/app.py b/app.py\n",
        "--- a/app.py\n",
        "+++ b/app.py\n",
        "@@ -1,2 +1,3 @@\n",
        "-old\n",
        "+new\n",
        "+extra\n",
        "diff --git a/tests/test_app.py b/tests/test_app.py\n",
        "--- a/tests/test_app.py\n",
        "+++ b/tests/test_app.py\n",
        "@@ -1 +1 @@\n",
        "-assert old\n",
        "+assert new\n",
    ]:
        condenser.feed(line)

    assert condenser.lines == ["edited app.py (+2 -1)", "edited tests/test_app.py (+1 -1)"]


def test_prose_and_plus_minus_bullets_pass_through_without_diff_header():
    condenser = OutputCondenser()

    for line in ["Plan:\n", "- add a test\n", "+ keep this literal\n"]:
        condenser.feed(line)

    assert condenser.lines == ["Plan:", "- add a test", "+ keep this literal"]


def test_prose_after_diff_is_shown():
    condenser = OutputCondenser()

    for line in [
        "diff --git a/app.py b/app.py\n",
        "--- a/app.py\n",
        "+++ b/app.py\n",
        "@@ -1 +1 @@\n",
        "-old\n",
        "+new\n",
        "Done editing app.py\n",
    ]:
        condenser.feed(line)

    assert condenser.lines == ["edited app.py (+1 -1)", "Done editing app.py"]
