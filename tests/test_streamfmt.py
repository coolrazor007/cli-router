from cli_router.streamfmt import (
    OutputCondenser,
    condense_extracted,
    first_meaningful_line,
    strip_ansi,
)


def test_strip_ansi_removes_csi_sequences():
    assert strip_ansi("\x1b[32mgreen\x1b[0m text") == "green text"
    assert strip_ansi("plain") == "plain"


def test_first_meaningful_line_skips_blanks_and_heading_markers():
    assert first_meaningful_line("\n\n## Summary\nDetails here") == "Summary"
    assert first_meaningful_line("\x1b[1mBold\x1b[0m first") == "Bold first"
    assert first_meaningful_line("") == ""
    assert first_meaningful_line(None) == ""


def test_condense_extracted_caps_lines_and_marks_truncation():
    text = "\n".join(f"line {index}" for index in range(30))
    preview = condense_extracted(text, max_lines=5)
    assert preview.splitlines()[:5] == [f"line {index}" for index in range(5)]
    assert preview.endswith("…")


def test_condense_extracted_short_text_is_untouched():
    assert condense_extracted("just this") == "just this"
    assert condense_extracted("") == ""
    assert condense_extracted(None) == ""


def test_condense_extracted_caps_characters():
    preview = condense_extracted("x" * 2000, max_chars=100)
    assert len(preview) <= 102  # 100 chars + newline + ellipsis
    assert preview.endswith("…")


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
