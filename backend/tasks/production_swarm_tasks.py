import json
import os
from contextlib import contextmanager
from pathlib import Path
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

    @celery_app.task(name="production.regen_shot_plan")
    def regen_shot_plan_task(shot_id: int, feedback: str):
        with current_app.app_context():
            run_regen_shot_plan(shot_id, feedback)

    return {
        "run_screenwriter": run_screenwriter_task,
        "run_casting_director": run_casting_director_task,
        "run_cinematographer": run_cinematographer_task,
        "run_storyboard_artist": run_storyboard_artist_task,
        "run_editor": run_editor_task,
        "regen_storyboard_shot": regen_storyboard_shot_task,
        "regen_shot_plan": regen_shot_plan_task,
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
                    scene_mood=scene.mood,
                    character_name=shot.character_name,
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
        
        # Fetch available voices from Audio Foundry
        available_voices = []
        try:
            flask_port = os.environ.get("FLASK_PORT", "5002")
            resp = requests.get(f"http://localhost:{flask_port}/api/audio-foundry/voices", timeout=5)
            if resp.status_code == 200:
                available_voices = resp.json().get("voices", [])
        except Exception as e:
            import logging
            logging.warning(f"Could not fetch available voices: {e}")

        input_data = {
            "subjects": script_subjects,
            "library": [{"id": s.id, "name": s.name, "kind": s.kind, "description": s.description, "voice_id": s.voice_id} for s in library],
            "available_voices": available_voices
        }
        
        inv = agent.invoke(input_data)
        ctx.persist(inv, input_json=input_data)

        # Apply the casting plan to the Subjects
        out = inv.output
        for action in out.actions:
            subj = Subject.query.filter_by(name=action.subject_name).first()
            if subj:
                if action.voice_id:
                    subj.voice_id = action.voice_id
                
                # Also link to production if not already
                existing_ps = ProductionSubject.query.filter_by(production_id=prod_id, subject_id=subj.id).first()
                if not existing_ps:
                    db.session.add(ProductionSubject(production_id=prod_id, subject_id=subj.id))
        
        db.session.commit()


def _subjects_for_production(prod_id: int) -> list[Subject]:
    """Prefer production-scoped cast, with a fallback for legacy unlinked rows."""
    subjects = (
        db.session.query(Subject)
        .join(ProductionSubject)
        .filter(ProductionSubject.production_id == prod_id)
        .all()
    )
    return subjects or Subject.query.all()


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

        subjects = _subjects_for_production(prod_id)
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


def _shot_loras_and_prompt(shot) -> tuple[list[str], str]:
    """Collect a shot's character LoRA paths and build the generation prompt
    with each Subject's trigger word prepended — the LoRA only locks identity
    when the token it was trained on is actually present at inference."""
    lora_paths: list[str] = []
    triggers: list[str] = []
    for pss in shot.shot_subjects:
        subj = pss.subject
        if subj.lora_path:
            lora_paths.append(subj.lora_path)
            triggers.append((subj.trigger_word or subj.name or "").strip())
    triggers = [t for t in triggers if t]
    prompt = shot.description
    if triggers:
        prompt = f"{', '.join(triggers)}, {prompt}"
    return lora_paths, prompt


def _storyboard_path(prod_id: int, shot_number: int) -> str:
    from backend.config import STORAGE_DIR
    out_dir = Path(STORAGE_DIR) / "outputs" / "storyboards" / str(prod_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"shot_{shot_number}.png")


def run_storyboard_artist(prod_id: int, image_generator=None):
    with _agent_run(prod_id, agent_name="storyboard_artist", expected_stage="storyboard_gen", next_agent=None) as ctx:
        if ctx is None:
            return

        if image_generator is None:
            from backend.services.comfyui_image_generator import ComfyUIImageGenerator
            image_generator = ComfyUIImageGenerator()

        shots = ProductionShot.query.filter_by(production_id=prod_id).all()

        for i, shot in enumerate(shots):
            lora_paths, prompt = _shot_loras_and_prompt(shot)
            output_path = _storyboard_path(prod_id, shot.shot_number or (i + 1))
            shot.storyboard_image_path = image_generator.generate_image(
                prompt=prompt, loras=lora_paths, output_path=output_path,
            )

        db.session.commit()

def run_editor(prod_id: int, i2v=None, audio_foundry=None, ffmpeg=None):
    with _agent_run(prod_id, agent_name="editor", expected_stage="rendering", next_agent=None) as ctx:
        if ctx is None:
            return

        if i2v is None:
            # SVD animates the LoRA-consistent storyboard frame — identity rides
            # in the image, so no video-side LoRA is needed.
            from backend.services.comfyui_video_generator import SvdI2VGenerator
            i2v = SvdI2VGenerator()

        # Real service clients with graceful degradation: ffmpeg always present
        # (system binary), audio only when the plugin is up, timeline compose
        # only when the video_editor plugin is up. A missing plugin downgrades
        # the output (video-only / no editable .mlt) rather than failing.
        from backend.services.swarm.clients import (
            AudioFoundryClient, FfmpegRunner, VideoEditorComposeClient,
        )
        if ffmpeg is None:
            ffmpeg = FfmpegRunner()
        if audio_foundry is None:
            _af = AudioFoundryClient()
            audio_foundry = _af if _af.available() else None

        shots = ProductionShot.query.filter_by(production_id=prod_id, approved=True).all()
        if not shots:
            ctx.fail("No approved shots")

        flask_port = os.environ.get("FLASK_PORT", "5002")
        backend_url = f"http://localhost:{flask_port}/api"
        ve_client = VideoEditorComposeClient(backend_url)

        editor = Editor(
            i2v=i2v,
            audio_foundry=audio_foundry,
            ffmpeg=ffmpeg,
            video_editor=ve_client,
        )

        from backend.services.swarm.agents.editor import ShotInput
        shot_inputs = []
        for s in shots:
            # Resolve the shot's cast once: LoRAs stack from every cast Subject,
            # and the speaking Subject (whose voice_id drives the VO) is drawn
            # from that SAME cast — one casting decision yields both a consistent
            # face and a consistent voice.
            cast = [pss.subject for pss in s.shot_subjects]
            lora_paths = [c.lora_path for c in cast if c.lora_path]

            char_cast = [c for c in cast if c.kind == "character"]
            speaker = None
            if s.character_name:
                speaker = next((c for c in char_cast if c.name == s.character_name), None)
            if speaker is None:
                speaker = char_cast[0] if char_cast else None
            if speaker is None and s.character_name:
                # Back-compat: shot wasn't explicitly cast — fall back to a
                # library-wide lookup by the screenwriter's character name.
                speaker = Subject.query.filter_by(name=s.character_name, kind="character").first()
            voice_id = speaker.voice_id if speaker else None
            if speaker is not None and s.voice_subject_id != speaker.id:
                # Stamp who speaks this shot (the previously-dead FK) so the
                # choice is traceable and reusable downstream.
                s.voice_subject_id = speaker.id

            shot_inputs.append(ShotInput(
                shot_number=s.shot_number,
                storyboard_image_path=s.storyboard_image_path or "",
                image_prompt=s.description,
                duration_seconds=s.duration_seconds,
                dialogue_text=s.dialogue_text,
                lora_paths=lora_paths,
                voice_id=voice_id,
                scene_number=s.scene_number,
                scene_mood=s.scene_mood
            ))
            
        import tempfile
        output_dir = tempfile.mkdtemp(prefix=f"prod_{prod_id}_")

        # Ride the unified job rail: a VIDEO_RENDER progress process so the
        # production render shows up in /api/jobs/active and the gate snapshot,
        # exactly like a standalone editor render.
        from backend.utils.unified_progress_system import get_unified_progress, ProcessType
        from backend.services.job_operation_gate import get_gate
        from backend.services.job_types import JobKind
        from backend.services.production_documents import register_production_output

        progress = get_unified_progress()
        gate = get_gate()
        render_id = f"prod_{prod_id}"
        job_id = progress.create_process(
            ProcessType.VIDEO_RENDER,
            f"Rendering production {prod_id}: {ctx.production.name}",
            additional_data={"production_id": prod_id},
        )
        gate.register_running(JobKind.VIDEO_RENDER, render_id)
        try:
            progress.update_process(job_id, 5, f"Rendering {len(shot_inputs)} shots")
            res = editor.render(
                production_id=prod_id,
                production_name=ctx.production.name,
                shots=shot_inputs,
                output_dir=output_dir,
            )

            for i, shot in enumerate(shots):
                if i < len(res.clip_paths):
                    shot.video_clip_path = res.clip_paths[i]

            final_doc = register_production_output(
                production=ctx.production, file_path=res.final_mp4_path, category="final",
            )
            # The editable Shotcut/MLT timeline, when the video_editor plugin
            # composed one (None when the plugin is down — final.mp4 still ships).
            if res.mlt_path:
                register_production_output(
                    production=ctx.production, file_path=res.mlt_path, category="timeline",
                )

            db.session.commit()
            progress.complete_process(
                job_id, "Production render complete",
                additional_data={"document_id": final_doc.id, "mlt_path": res.mlt_path},
            )
        except Exception as e:
            progress.error_process(job_id, f"Production render failed: {e}")
            raise
        finally:
            gate.unregister_running(JobKind.VIDEO_RENDER, render_id)


def regen_storyboard_shot(shot_id: int, prompt_override: str | None = None, image_generator=None):
    shot = db.session.get(ProductionShot, shot_id)
    if not shot or shot.production.current_stage != "awaiting_approval":
        import logging
        logging.warning("Regen storyboard shot called when not awaiting approval")
        return

    if image_generator is None:
        from backend.services.comfyui_image_generator import ComfyUIImageGenerator
        image_generator = ComfyUIImageGenerator()

    lora_paths, base_prompt = _shot_loras_and_prompt(shot)
    prompt = prompt_override if prompt_override else base_prompt
    output_path = _storyboard_path(shot.production_id, shot.shot_number or shot.id)
    shot.storyboard_image_path = image_generator.generate_image(
        prompt=prompt, loras=lora_paths, output_path=output_path,
    )
    db.session.commit()

def run_regen_shot_plan(shot_id: int, feedback: str, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    from backend.models import ProductionShot, Subject, ProductionShotSubject
    shot = db.session.get(ProductionShot, shot_id)
    if not shot:
        return

    from backend.services.swarm.agents.cinematographer import Cinematographer
    agent = Cinematographer(llm=llm)

    subjects = _subjects_for_production(shot.production_id)
    
    # We only care about THIS shot
    input_data = {
        "shots": [{"scene_number": shot.scene_number, "shot_number": shot.shot_number, "description": shot.description}],
        "subjects": [{"id": s.id, "name": s.name, "description": s.description} for s in subjects],
        "feedback": feedback
    }

    inv = agent.invoke(input_data)
    
    out = inv.output
    if out.plans:
        plan = out.plans[0]
        shot.camera_angle = plan.camera_angle
        shot.duration_seconds = plan.duration_seconds
        # Preserve original description but update the image prompt
        base_desc = shot.description.split("\n\nIMAGE PROMPT: ")[0]
        shot.description = f"{base_desc}\n\nIMAGE PROMPT: {plan.image_prompt}"
        
        # Update subjects
        ProductionShotSubject.query.filter_by(shot_id=shot.id).delete()
        valid_subject_ids = {s.id for s in subjects}
        for subj_id in plan.subjects_in_shot:
            if subj_id in valid_subject_ids:
                db.session.add(ProductionShotSubject(shot_id=shot.id, subject_id=subj_id))
        
        shot.regen_count += 1
        db.session.commit()
