import pytest

from cli_router.extractors import ExtractionError, extract_output


def test_extracts_plain_text_output():
    assert extract_output("hello\n", {"format": "text"}) == "hello\n"


def test_extracts_json_field_by_dotted_path():
    stdout = '{"message": {"result": "make PLAN.md"}}'

    assert extract_output(stdout, {"format": "json", "extract": "message.result"}) == "make PLAN.md"


def test_json_extraction_fails_for_missing_field():
    with pytest.raises(ExtractionError):
        extract_output('{"result": "ok"}', {"format": "json", "extract": "missing"})
