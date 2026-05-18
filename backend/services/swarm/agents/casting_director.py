import json
from pydantic import BaseModel
from backend.services.swarm.agents.base import BaseSwarmAgent


class CastingAction(BaseModel):
    subject_name: str
    action: str  # use_existing_lora | train_from_uploads | train_from_generated
    existing_lora_id: int | None = None
    voice_id: str | None = None


class CastingPlan(BaseModel):
    actions: list[CastingAction]


SYSTEM = """You are a Casting Director. For each Subject in the input, decide an action:

- "use_existing_lora": if the Subject's name + kind matches an entry in the cast library, use that library entry's id.
- "train_from_uploads": if no library match exists and the user has provided reference images.
- "train_from_generated": if no library match and no uploads — refs will be generated from the Subject description.

Additionally, for Subjects with kind="character", assign a "voice_id" from the available voices list. If the Subject is in the library, use their existing voice_id if present.

Return ONLY a JSON object of this shape:
{
  "actions": [
    {"subject_name": <str>, "action": "use_existing_lora"|"train_from_uploads"|"train_from_generated",
     "existing_lora_id": <int or null>, "voice_id": <str or null>}
  ]
}"""


class CastingDirector(BaseSwarmAgent[CastingPlan]):
    name = "casting_director"
    output_model = CastingPlan
    system_prompt = SYSTEM

    def build_user_prompt(self, input_data: dict) -> str:
        return (
            f"Subjects to cast:\n{json.dumps(input_data.get('subjects', []), indent=2)}\n\n"
            f"Cast Library:\n{json.dumps(input_data.get('library', []), indent=2)}\n\n"
            f"Available Voices:\n{json.dumps(input_data.get('available_voices', []), indent=2)}\n\n"
            "Return the JSON casting plan."
        )
