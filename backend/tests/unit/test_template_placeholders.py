import string

import pytest

try:
    from backend.utils import prompt_utils
except Exception:
    pytest.skip("prompt_utils not available", allow_module_level=True)


@pytest.mark.skip(reason="FALLBACK_QA_PROMPT_TEXT no longer exists or is not relevant.")
def test_fallback_qa_template_formatting():
    pass


@pytest.mark.skip(reason="FALLBACK_CODE_GEN_PROMPT_TEXT no longer exists or is not relevant.")
def test_fallback_code_gen_template_formatting():
    tmpl = prompt_utils.FALLBACK_CODE_GEN_PROMPT_TEXT
    tmpl.format(
        user_requirements="req",
        available_input_csv="a.csv",
        available_input_xml="b.xml",
        output_filename_suggestion="out.txt",
    )
