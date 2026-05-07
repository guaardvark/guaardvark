from pydantic import BaseModel
from backend.services.swarm.agents.base import BaseSwarmAgent, AgentInvocation


class _TestOutput(BaseModel):
    answer: str


class _TestAgent(BaseSwarmAgent[_TestOutput]):
    name = "test"
    output_model = _TestOutput
    system_prompt = "Return JSON {answer:string}."

    def build_user_prompt(self, input_data):
        return f"Q: {input_data}"


def test_agent_parses_valid_output():
    agent = _TestAgent(llm=lambda **kw: '{"answer": "yes"}')
    inv = agent.invoke("question")
    assert inv.output.answer == "yes"
    assert inv.status == "ok"
    assert inv.latency_ms is not None
    assert inv.latency_ms >= 0


def test_agent_records_parse_error():
    agent = _TestAgent(llm=lambda **kw: "not json")
    inv = agent.invoke("question")
    assert inv.status == "parse_error"
    assert inv.output is None
    assert inv.error_text is not None
    assert "json" in inv.error_text.lower() or "validation" in inv.error_text.lower() or "expecting" in inv.error_text.lower()


def test_agent_records_llm_error():
    def boom(**kw):
        raise RuntimeError("LLM down")
    agent = _TestAgent(llm=boom)
    inv = agent.invoke("question")
    assert inv.status == "error"
    assert inv.output is None
    assert "LLM down" in inv.error_text
