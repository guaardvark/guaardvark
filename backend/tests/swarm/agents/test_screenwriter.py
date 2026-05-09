from backend.services.swarm.agents.screenwriter import Screenwriter, ScriptBreakdown


def test_screenwriter_parses_breakdown():
    canned = '''{
      "scenes": [{"number": 1, "location": "kitchen", "shots": [
        {"number": 1, "description": "Dean enters", "dialogue": "Hello"}
      ]}],
      "subjects": [
        {"kind": "character", "name": "Dean", "description": "the protagonist"},
        {"kind": "environment", "name": "kitchen", "description": "small modern kitchen"}
      ]
    }'''
    agent = Screenwriter(llm=lambda **kw: canned)
    inv = agent.invoke("INT. KITCHEN. Dean enters. 'Hello'.")
    assert inv.status == "ok"
    assert len(inv.output.scenes) == 1
    assert inv.output.scenes[0].location == "kitchen"
    assert inv.output.subjects[0].kind == "character"
    assert inv.output.subjects[0].name == "Dean"


def test_screenwriter_handles_missing_dialogue():
    canned = '''{"scenes": [{"number": 1, "location": "x", "shots": [
        {"number": 1, "description": "wide shot", "dialogue": null}
    ]}], "subjects": []}'''
    agent = Screenwriter(llm=lambda **kw: canned)
    inv = agent.invoke("INT. X.")
    assert inv.status == "ok"
    assert inv.output.scenes[0].shots[0].dialogue is None
