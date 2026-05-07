from pydantic import BaseModel
from backend.services.swarm.agents.base import BaseSwarmAgent


class ScriptShot(BaseModel):
    number: int
    description: str
    dialogue: str | None = None


class ScriptScene(BaseModel):
    number: int
    location: str
    shots: list[ScriptShot]


class ScriptSubject(BaseModel):
    kind: str  # character | environment | prop
    name: str
    description: str


class ScriptBreakdown(BaseModel):
    scenes: list[ScriptScene]
    subjects: list[ScriptSubject]


SYSTEM = """You are a screenplay analyst. Break down the script into scenes (one per location)
and shots (one per camera setup within a scene). Extract every named character, location, and
significant prop as a Subject with a short description.

Return ONLY a JSON object matching this shape, with no prose around it:
{
  "scenes": [
    {"number": <int>, "location": <str>, "shots": [
      {"number": <int>, "description": <str>, "dialogue": <str|null>}
    ]}
  ],
  "subjects": [
    {"kind": "character"|"environment"|"prop", "name": <str>, "description": <str>}
  ]
}"""


class Screenwriter(BaseSwarmAgent[ScriptBreakdown]):
    name = "screenwriter"
    output_model = ScriptBreakdown
    system_prompt = SYSTEM

    def build_user_prompt(self, input_data: str) -> str:
        return f"Script:\n\n{input_data}\n\nReturn the JSON breakdown."
