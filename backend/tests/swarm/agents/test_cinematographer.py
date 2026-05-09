from backend.services.swarm.agents.cinematographer import Cinematographer, ShotPlanList


def test_cinematographer_produces_shot_plans():
    canned = '''{"plans": [
        {"scene_number": 1, "shot_number": 1, "camera_angle": "wide",
         "framing": "establishing", "duration_seconds": 4.0, "mood": "calm",
         "image_prompt": "wide shot of a kitchen, calm morning light",
         "subjects_in_shot": [1, 2]}
    ]}'''
    agent = Cinematographer(llm=lambda **kw: canned)
    inv = agent.invoke({
        "shots": [{"scene_number": 1, "shot_number": 1, "description": "Dean enters"}],
        "subjects": [{"id": 1, "name": "Dean"}, {"id": 2, "name": "kitchen"}],
    })
    assert inv.status == "ok"
    assert len(inv.output.plans) == 1
    plan = inv.output.plans[0]
    assert plan.camera_angle == "wide"
    assert plan.duration_seconds == 4.0
    assert plan.subjects_in_shot == [1, 2]


def test_cinematographer_close_up_shot():
    canned = '''{"plans": [
        {"scene_number": 1, "shot_number": 2, "camera_angle": "close-up",
         "framing": "face only", "duration_seconds": 2.5, "mood": "tense",
         "image_prompt": "extreme close-up on Dean's face, tense expression",
         "subjects_in_shot": [1]}
    ]}'''
    agent = Cinematographer(llm=lambda **kw: canned)
    inv = agent.invoke({"shots": [], "subjects": []})
    assert inv.status == "ok"
    assert inv.output.plans[0].camera_angle == "close-up"
    assert inv.output.plans[0].mood == "tense"
