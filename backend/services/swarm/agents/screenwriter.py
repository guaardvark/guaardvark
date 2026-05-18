from pydantic import BaseModel
from backend.services.swarm.agents.base import BaseSwarmAgent


class ScriptShot(BaseModel):
    number: int
    description: str
    character_name: str | None = None
    dialogue: str | None = None


class ScriptScene(BaseModel):
    number: int
    location: str
    mood: str | None = "neutral"
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

For each scene, identify a "mood" (e.g., suspense, action, romantic, neutral) which will
be used for music and lighting.

Return ONLY a JSON object matching this shape, with no prose around it:
{
  "scenes": [
    {"number": <int>, "location": <str>, "mood": <str>, "shots": [
      {"number": <int>, "description": <str>, "character_name": <str|null>, "dialogue": <str|null>}
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
