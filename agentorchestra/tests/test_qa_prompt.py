from agentorchestra.prompts.qa import QA_SYSTEM_PROMPT, QA_TASK_EXPECTED_OUTPUT


def test_qa_prompt_enforces_evidence_only_review():
    prompt = f"{QA_SYSTEM_PROMPT}\n{QA_TASK_EXPECTED_OUTPUT}".casefold()

    assert "do not modify files" in prompt
    assert "do not run tools" in prompt
    assert "do not invoke agents" in prompt
    assert "promote staging" in prompt
    assert "preserve each criterion wording exactly" in prompt
    assert "return exactly one criterionresult" in prompt
    assert "accept only when every criterion is passed" in prompt
    assert "evidence is insufficient" in prompt
    assert "do not use browser rendering assumptions" in prompt
    assert "internet research" in prompt
    assert "force acceptance" in prompt
    assert "chain-of-thought" in prompt
