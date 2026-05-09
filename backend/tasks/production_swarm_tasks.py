import json
from contextlib import contextmanager
from celery import Celery
from flask import current_app

from backend.models import db, Production, Subject, ProductionShot, ProductionShotSubject, ProductionSubject, SwarmMessage, Document
from backend.services.production_service import ProductionService
from backend.services.swarm.agents.screenwriter import Screenwriter
from backend.services.swarm.agents.cinematographer import Cinematographer
from backend.services.swarm.agents.casting_director import CastingDirector
from backend.services.swarm.agents.editor import Editor


class _AgentNonOK(Exception):
    pass

class _AgentRunContext:
    def __init__(self, production: Production, agent_name: str, stage: str):
        self.production = production
        self.agent_name = agent_name
        self.stage = stage

    def persist(self, inv, input_json: dict):
        msg = SwarmMessage(
            production_id=self.production.id,
            agent_name=self.agent_name,
            input_json=input_json,
            output_json=inv.output.model_dump() if inv.output else None,
            latency_ms=inv.latency_ms,
            model=inv.model,
            status=inv.status,
            error_text=inv.error_text
        )
        db.session.add(msg)
        db.session.commit()

        if inv.status != "ok":
            ProductionService(db.session).fail_stage(
                self.production.id, stage=self.stage, error=inv.error_text or inv.status
            )
            raise _AgentNonOK()

    def fail(self, reason):
        ProductionService(db.session).fail_stage(
            self.production.id, stage=self.stage, error=reason
        )
        raise _AgentNonOK()

@contextmanager
def _agent_run(prod_id: int, *, agent_name: str, expected_stage: str, next_agent: str | None):
    prod = db.session.get(Production, prod_id)
    if not prod or prod.current_stage != expected_stage:
        yield None
        return

    ctx = _AgentRunContext(production=prod, agent_name=agent_name, stage=expected_stage)
    try:
        yield ctx
    except _AgentNonOK:
        # Already failed via persist or fail
        pass
    except Exception as e:
        # Catch all other exceptions, fail stage, and absorb.
        # Absorbing is safer because Celery retry behavior is default, which WILL retry.
        ProductionService(db.session).fail_stage(prod_id, stage=expected_stage, error=str(e))
        pass
    else:
        # Clean exit
        advanced = ProductionService(db.session).advance_if_predecessor(prod_id, expected_predecessor=expected_stage)
        if advanced and next_agent:
            from backend.celery_app import celery
            celery.send_task(f"production.run_{next_agent}", args=[prod_id])


def _default_ollama_llm(*, system: str, user: str, model: str = "gemma4:e4b") -> str:
    import ollama
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return response["message"]["content"]


def create_production_swarm_tasks(celery_app: Celery):
    
    @celery_app.task(name="production.run_screenwriter")
    def run_screenwriter_task(prod_id: int):
        with current_app.app_context():
            run_screenwriter(prod_id)

    @celery_app.task(name="production.run_casting_director")
    def run_casting_director_task(prod_id: int):
        with current_app.app_context():
            run_casting_director(prod_id)

    @celery_app.task(name="production.run_cinematographer")
    def run_cinematographer_task(prod_id: int):
        with current_app.app_context():
            run_cinematographer(prod_id)

    @celery_app.task(name="production.run_storyboard_artist")
    def run_storyboard_artist_task(prod_id: int):
        with current_app.app_context():
            run_storyboard_artist(prod_id)

    @celery_app.task(name="production.run_editor")
    def run_editor_task(prod_id: int):
        with current_app.app_context():
            run_editor(prod_id)

    @celery_app.task(name="production.regen_storyboard_shot")
    def regen_storyboard_shot_task(shot_id: int, prompt_override: str | None = None):
        with current_app.app_context():
            regen_storyboard_shot(shot_id, prompt_override)

    return {
        "run_screenwriter": run_screenwriter_task,
        "run_casting_director": run_casting_director_task,
        "run_cinematographer": run_cinematographer_task,
        "run_storyboard_artist": run_storyboard_artist_task,
        "run_editor": run_editor_task,
        "regen_storyboard_shot": regen_storyboard_shot_task,
    }


def run_screenwriter(prod_id: int, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    with _agent_run(prod_id, agent_name="screenwriter", expected_stage="screenwriting", next_agent=None) as ctx:
        if ctx is None:
            return

        # Idempotency reset: delete existing outputs for this production
        ProductionSubject.query.filter_by(production_id=prod_id).delete()
        ProductionShot.query.filter_by(production_id=prod_id).delete()
        db.session.commit()

        agent = Screenwriter(llm=llm)
        input_data = ctx.production.script_text
        
        inv = agent.invoke(input_data)
        ctx.persist(inv, input_json={"script_text": input_data})

        out = inv.output
        for subj in out.subjects:
            existing = Subject.query.filter_by(name=subj.name, kind=subj.kind).first()
            if existing:
                existing.description = subj.description
                subject_to_link = existing
            else:
                new_subj = Subject(name=subj.name, kind=subj.kind, description=subj.description)
                db.session.add(new_subj)
                db.session.flush()  # get ID
                subject_to_link = new_subj
                
            # Link to production
            ps = ProductionSubject(production_id=prod_id, subject_id=subject_to_link.id)
            db.session.add(ps)
        
        for scene in out.scenes:
            for shot in scene.shots:
                new_shot = ProductionShot(
                    production_id=prod_id,
                    scene_number=scene.number,
                    shot_number=shot.number,
                    description=shot.description,
                    dialogue_text=shot.dialogue
                )
                db.session.add(new_shot)
        
        db.session.commit()


def run_casting_director(prod_id: int, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    with _agent_run(prod_id, agent_name="casting_director", expected_stage="casting", next_agent=None) as ctx:
        if ctx is None:
            return

        agent = CastingDirector(llm=llm)
        
        # Subjects from this production's script (from screenwriter output)
        screenwriter_msg = SwarmMessage.query.filter_by(
            production_id=prod_id, agent_name="screenwriter", status="ok"
        ).order_by(SwarmMessage.id.desc()).first()
        
        script_subjects = []
        if screenwriter_msg and screenwriter_msg.output_json:
            script_subjects = screenwriter_msg.output_json.get("subjects", [])
            
        # Cast library (existing trained Subject rows where lora_path is not None)
        library = Subject.query.filter(Subject.lora_path.isnot(None)).all()
        
        input_data = {
            "subjects": script_subjects,
            "library": [{"id": s.id, "name": s.name, "kind": s.kind, "description": s.description} for s in library]
        }
        
        inv = agent.invoke(input_data)
        ctx.persist(inv, input_json=input_data)


def run_cinematographer(prod_id: int, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    with _agent_run(prod_id, agent_name="cinematographer", expected_stage="cinematography", next_agent="storyboard_artist") as ctx:
        if ctx is None:
            return

        # Idempotency reset
        shots = ProductionShot.query.filter_by(production_id=prod_id).all()
        for shot in shots:
            ProductionShotSubject.query.filter_by(shot_id=shot.id).delete()
            if "\n\nIMAGE PROMPT: " in shot.description:
                shot.description = shot.description.split("\n\nIMAGE PROMPT: ")[0]
        db.session.commit()

        agent = Cinematographer(llm=llm)

        subjects = Subject.query.all()
        valid_subject_ids = {s.id for s in subjects}

        input_data = {
            "shots": [{"scene_number": s.scene_number, "shot_number": s.shot_number, "description": s.description} for s in shots],
            "subjects": [{"id": s.id, "name": s.name, "description": s.description} for s in subjects]
        }

        inv = agent.invoke(input_data)
        ctx.persist(inv, input_json=input_data)

        out = inv.output
        for plan in out.plans:
            shot = ProductionShot.query.filter_by(
                production_id=prod_id,
                scene_number=plan.scene_number,
                shot_number=plan.shot_number
            ).first()
            if shot:
                shot.camera_angle = plan.camera_angle
                shot.duration_seconds = plan.duration_seconds
                shot.description = f"{shot.description}\n\nIMAGE PROMPT: {plan.image_prompt}"

                # M2: validate subject_ids against the set we actually passed to the
                # LLM. Models occasionally hallucinate IDs (e.g. echoing the
                # scene_number as a subject_id); inserting those would FK-violate.
                for subj_id in plan.subjects_in_shot:
                    if subj_id not in valid_subject_ids:
                        continue
                    db.session.add(ProductionShotSubject(shot_id=shot.id, subject_id=subj_id))

        db.session.commit()


def run_storyboard_artist(prod_id: int, image_generator=None):
    with _agent_run(prod_id, agent_name="storyboard_artist", expected_stage="storyboard_gen", next_agent=None) as ctx:
        if ctx is None:
            return

        shots = ProductionShot.query.filter_by(production_id=prod_id).all()
        
        for i, shot in enumerate(shots):
            if image_generator:
                path = image_generator.generate_image(prompt=shot.description)
                shot.storyboard_image_path = path
            else:
                shot.storyboard_image_path = f"/tmp/storyboards/{prod_id}/shot_{i+1}.png"
                
        db.session.commit()

def run_editor(prod_id: int, i2v=None, audio_foundry=None, ffmpeg=None):
    with _agent_run(prod_id, agent_name="editor", expected_stage="rendering", next_agent=None) as ctx:
        if ctx is None:
            return

        shots = ProductionShot.query.filter_by(production_id=prod_id, approved=True).all()
        if not shots:
            ctx.fail("No approved shots")

        editor = Editor(i2v=i2v, audio_foundry=audio_foundry, ffmpeg=ffmpeg)
        
        from backend.services.swarm.agents.editor import ShotInput
        shot_inputs = []
        for s in shots:
            shot_inputs.append(ShotInput(
                shot_number=s.shot_number,
                storyboard_image_path=s.storyboard_image_path or "",
                image_prompt=s.description,
                duration_seconds=s.duration_seconds,
                dialogue_text=s.dialogue_text,
                lora_paths=[]
            ))
            
        import tempfile
        output_dir = tempfile.mkdtemp(prefix=f"prod_{prod_id}_")
        
        res = editor.render(
            production_id=prod_id,
            production_name=ctx.production.name,
            shots=shot_inputs,
            output_dir=output_dir
        )
        
        for i, shot in enumerate(shots):
            if i < len(res.clip_paths):
                shot.video_clip_path = res.clip_paths[i]
                
        from backend.services.production_documents import register_production_output
        register_production_output(production=ctx.production, file_path=res.final_mp4_path, category="final")
        
        db.session.commit()


def regen_storyboard_shot(shot_id: int, prompt_override: str | None = None, image_generator=None):
    shot = db.session.get(ProductionShot, shot_id)
    if not shot or shot.production.current_stage != "awaiting_approval":
        import logging
        logging.warning("Regen storyboard shot called when not awaiting approval")
        return

    if image_generator:
        prompt = prompt_override if prompt_override else shot.description
        path = image_generator.generate_image(prompt=prompt)
        shot.storyboard_image_path = path
        db.session.commit()
