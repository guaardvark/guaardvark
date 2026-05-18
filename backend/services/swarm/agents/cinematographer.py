import json
from pydantic import BaseModel
from backend.services.swarm.agents.base import BaseSwarmAgent


class ShotPlan(BaseModel):
    scene_number: int
    shot_number: int
    camera_angle: str        # e.g. "wide", "medium", "close-up", "over-shoulder"
    framing: str
    duration_seconds: float
    mood: str
    image_prompt: str        # for ComfyUI
    subjects_in_shot: list[int]  # subject IDs


class ShotPlanList(BaseModel):
    plans: list[ShotPlan]


SYSTEM = """You are a Cinematographer. Given a list of shots from the script and the cast/locations
available, produce a shot plan for each: camera angle (wide / medium / close-up / over-shoulder /
insert), framing description, duration in seconds (typically 2-6s), mood (e.g. "calm", "tense",
"hopeful"), an image prompt for the storyboard generator (specific, visual, includes lighting and
composition), and the IDs of which Subjects appear in the shot.

Return ONLY a JSON object of this shape:
{
  "plans": [
    {"scene_number": <int>, "shot_number": <int>, "camera_angle": <str>, "framing": <str>,
     "duration_seconds": <float>, "mood": <str>, "image_prompt": <str>,
     "subjects_in_shot": [<int>, ...]}
  ]
}"""


class Cinematographer(BaseSwarmAgent[ShotPlanList]):
    name = "cinematographer"
    output_model = ShotPlanList
    system_prompt = SYSTEM

    def build_user_prompt(self, input_data: dict) -> str:
        prompt = (
            f"Shots:\n{json.dumps(input_data.get('shots', []), indent=2)}\n\n"
            f"Subjects (with IDs):\n{json.dumps(input_data.get('subjects', []), indent=2)}\n\n"
        )
        
        feedback = input_data.get('feedback')
        if feedback:
            prompt += f"USER FEEDBACK / REVISION REQUEST:\n{feedback}\n\n"
            
        prompt += "Return the JSON shot plan list."
        return prompt
