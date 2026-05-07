import json
from celery import Celery
from flask import current_app

from backend.models import db, Production, Subject, ProductionShot, ProductionShotSubject, SwarmMessage, Document
from backend.services.production_service import ProductionService
from backend.services.swarm.agents.screenwriter import Screenwriter
from backend.services.swarm.agents.cinematographer import Cinematographer
from backend.services.swarm.agents.casting_director import CastingDirector
from backend.services.swarm.agents.editor import Editor


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

    prod = db.session.get(Production, prod_id)
    if not prod or prod.current_stage != "screenwriting":
        return

    agent = Screenwriter(llm=llm)
    input_data = prod.script_text
    
    inv = agent.invoke(input_data)
    
    msg = SwarmMessage(
        production_id=prod_id,
        agent_name="screenwriter",
        input_json={"script_text": input_data},
        output_json=inv.output.model_dump() if inv.output else None,
        latency_ms=inv.latency_ms,
        model=inv.model,
        status=inv.status,
        error_text=inv.error_text
    )
    db.session.add(msg)
    db.session.commit()

    if inv.status != "ok":
        ProductionService(db.session).fail_stage(prod_id, stage="screenwriting", error=inv.error_text or inv.status)
        return

    out = inv.output
    for subj in out.subjects:
        existing = Subject.query.filter_by(name=subj.name, kind=subj.kind).first()
        if existing:
            existing.description = subj.description
        else:
            new_subj = Subject(name=subj.name, kind=subj.kind, description=subj.description)
            db.session.add(new_subj)
    
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
    ProductionService(db.session).advance_if_predecessor(prod_id, expected_predecessor="screenwriting")


def run_casting_director(prod_id: int, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    prod = db.session.get(Production, prod_id)
    if not prod:
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
    
    msg = SwarmMessage(
        production_id=prod_id,
        agent_name="casting_director",
        input_json=input_data,
        output_json=inv.output.model_dump() if inv.output else None,
        latency_ms=inv.latency_ms,
        model=inv.model,
        status=inv.status,
        error_text=inv.error_text
    )
    db.session.add(msg)
    db.session.commit()


def run_cinematographer(prod_id: int, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    prod = db.session.get(Production, prod_id)
    if not prod or prod.current_stage != "cinematography":
        return

    agent = Cinematographer(llm=llm)

    shots = ProductionShot.query.filter_by(production_id=prod_id).all()
    subjects = Subject.query.all()
    valid_subject_ids = {s.id for s in subjects}

    input_data = {
        "shots": [{"scene_number": s.scene_number, "shot_number": s.shot_number, "description": s.description} for s in shots],
        "subjects": [{"id": s.id, "name": s.name, "description": s.description} for s in subjects]
    }

    inv = agent.invoke(input_data)

    msg = SwarmMessage(
        production_id=prod_id,
        agent_name="cinematographer",
        input_json=input_data,
        output_json=inv.output.model_dump() if inv.output else None,
        latency_ms=inv.latency_ms,
        model=inv.model,
        status=inv.status,
        error_text=inv.error_text
    )
    db.session.add(msg)
    db.session.commit()

    if inv.status != "ok":
        ProductionService(db.session).fail_stage(prod_id, stage="cinematography", error=inv.error_text or inv.status)
        return

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

    advanced = ProductionService(db.session).advance_if_predecessor(prod_id, expected_predecessor="cinematography")
    if advanced:
        from backend.celery_app import celery
        celery.send_task("production.run_storyboard_artist", args=[prod_id])


def run_storyboard_artist(prod_id: int, image_generator=None):
    prod = db.session.get(Production, prod_id)
    if not prod or prod.current_stage != "storyboard_gen":
        return

    shots = ProductionShot.query.filter_by(production_id=prod_id).all()
    
    for i, shot in enumerate(shots):
        if image_generator:
            # The prompt says: "All shots with their image_prompt + LoRAs... call image_generator.generate_image(...)"
            # Let's just pass dummy args for now, as it's stubbed.
            path = image_generator.generate_image(prompt=shot.description)
            shot.storyboard_image_path = path
        else:
            shot.storyboard_image_path = f"/tmp/storyboards/{prod_id}/shot_{i+1}.png"
            
    db.session.commit()
    ProductionService(db.session).advance_if_predecessor(prod_id, expected_predecessor="storyboard_gen")


def run_editor(prod_id: int, i2v=None, audio_foundry=None, ffmpeg=None):
    prod = db.session.get(Production, prod_id)
    if not prod or prod.current_stage != "rendering":
        return

    shots = ProductionShot.query.filter_by(production_id=prod_id, approved=True).all()
    if not shots:
        ProductionService(db.session).fail_stage(prod_id, stage="rendering", error="No approved shots")
        return

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
        production_name=prod.name,
        shots=shot_inputs,
        output_dir=output_dir
    )
    
    for i, shot in enumerate(shots):
        if i < len(res.clip_paths):
            shot.video_clip_path = res.clip_paths[i]
            
    from backend.services.production_documents import register_production_output
    register_production_output(production=prod, file_path=res.final_mp4_path, category="final")
    
    db.session.commit()
    ProductionService(db.session).advance_if_predecessor(prod_id, expected_predecessor="rendering")


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
