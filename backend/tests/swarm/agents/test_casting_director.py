from backend.services.swarm.agents.casting_director import CastingDirector, CastingPlan


def test_casting_director_routes_to_existing_lora():
    canned = '''{"actions": [
        {"subject_name": "Dean", "action": "use_existing_lora", "existing_lora_id": 42}
    ]}'''
    agent = CastingDirector(llm=lambda **kw: canned)
    inv = agent.invoke({
        "subjects": [{"name": "Dean", "kind": "character"}],
        "library": [{"id": 42, "name": "Dean", "kind": "character"}],
    })
    assert inv.status == "ok"
    assert inv.output.actions[0].action == "use_existing_lora"
    assert inv.output.actions[0].existing_lora_id == 42


def test_casting_director_routes_to_train_from_uploads():
    canned = '''{"actions": [
        {"subject_name": "Dean", "action": "train_from_uploads"}
    ]}'''
    agent = CastingDirector(llm=lambda **kw: canned)
    inv = agent.invoke({
        "subjects": [{"name": "Dean", "kind": "character"}],
        "library": [],
    })
    assert inv.status == "ok"
    assert inv.output.actions[0].action == "train_from_uploads"
    assert inv.output.actions[0].existing_lora_id is None
