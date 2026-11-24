import json
import os
import re
import requests
from contextlib import contextmanager
from pathlib import Path
from celery import Celery
from flask import current_app

from backend.models import db, Production, Subject, ProductionShot, ProductionShotSubject, ProductionSubject, SwarmMessage, Document
from backend.services.production_service import ProductionService
from backend.services.swarm.agents.screenwriter import Screenwriter
from backend.services.swarm.agents.cinematographer import Cinematographer, ShotPlan, ShotPlanList
from backend.services.swarm.agents.casting_director import CastingDirector
from backend.services.swarm.agents.editor import Editor
from backend.services.swarm.script_markup import parse_markup, apply_intents


def _render_kind(exc):
    """The RenderErrorKind an exception carries or is classified as (job_types)."""
    from backend.services.job_operation_gate import classify_render_exception
    return classify_render_exception(exc)


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
    # A failed stage keeps its stage name, so a second copy of the task (a
    # worker boots and resumes in-flight runs while one is already queued)
    # would pass the check above and run under a "failed" status. Retry
    # clears the failure first; nothing else re-runs it.
    if (prod.status or "").startswith("failed"):
        import logging
        logging.getLogger(__name__).info(
            "production %s: %s skipped, stage %s is marked %s (Retry re-runs it)",
            prod_id, agent_name, expected_stage, prod.status)
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
        # A stage that died in a flush or commit leaves the session unusable;
        # without the rollback the failure itself could not be recorded.
        db.session.rollback()
        ProductionService(db.session).fail_stage(prod_id, stage=expected_stage, error=str(e), kind=_render_kind(e))
        pass
    else:
        # Clean exit
        advanced = ProductionService(db.session).advance_if_predecessor(prod_id, expected_predecessor=expected_stage)
        if advanced and next_agent:
            from backend.celery_app import celery
            celery.send_task(f"production.run_{next_agent}", args=[prod_id])


def _default_ollama_llm(*, system: str, user: str, model: str = "gemma4:e4b") -> str:
    from backend.services.plugin_bridge import ensure_plugins_for_stage
    from backend.services.ollama_chat_model import resolve_chat_model
    ensure_plugins_for_stage("film-crew", "screenwriting")  # or cinematography etc.; uses phased non-persist
    ensure_plugins_for_stage("film-crew", "cinematography")
    import ollama
    # `model` is the agent's preference; the installer may have pulled a
    # different tag of the family (gemma4:e2b on most machines), so resolve
    # against what is actually installed rather than 404 on a clean box.
    model = resolve_chat_model(model)
    from backend.utils.ollama_resource_manager import think_payload
    response = ollama.chat(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        **think_payload(model),
    )
    return response["message"]["content"]


# The storyboard curator runs by default and is advice only: it checks every
# frame, pre-ticks the ones it judges usable with its reason, and a person still
# starts the render. Set GUAARDVARK_FILM_AUTOCURATE=0 to skip it.
FILM_AUTOCURATE_DEFAULT = "1"


def _film_autocurate_enabled() -> bool:
    value = os.environ.get("GUAARDVARK_FILM_AUTOCURATE", FILM_AUTOCURATE_DEFAULT)
    return value not in ("0", "false", "False", "")


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
            # The curator only pre-ticks cards; the approval gate stays with the
            # person. run_curator no-ops unless the production reached
            # awaiting_approval.
            if _film_autocurate_enabled():
                run_curator(prod_id)

    @celery_app.task(name="production.run_curator")
    def run_curator_task(prod_id: int):
        with current_app.app_context():
            run_curator(prod_id)

    @celery_app.task(name="production.run_editor")
    def run_editor_task(prod_id: int):
        with current_app.app_context():
            run_editor(prod_id)

    @celery_app.task(name="production.regen_storyboard_shot")
    def regen_storyboard_shot_task(shot_id: int, prompt_override: str | None = None):
        from backend.services.job_operation_gate import GpuBusyError
        with current_app.app_context():
            try:
                regen_storyboard_shot(shot_id, prompt_override)
            except GpuBusyError as e:
                # Redraws of several shots arrive together, and every one after
                # the first meets the gate's cooldown. Ask again shortly rather
                # than drop it; once the storyboard is approved a redraw no-ops.
                import logging
                logging.getLogger(__name__).info(
                    "Storyboard redraw of shot %s deferred (%s); retrying in %ss", shot_id, e, _REGEN_RETRY_S)
                celery_app.send_task("production.regen_storyboard_shot",
                                     args=[shot_id, prompt_override], countdown=_REGEN_RETRY_S)

    @celery_app.task(name="production.regen_shot_plan")
    def regen_shot_plan_task(shot_id: int, feedback: str):
        with current_app.app_context():
            run_regen_shot_plan(shot_id, feedback)
        # The new image prompt only shows once the still is drawn from it.
        celery_app.send_task("production.regen_storyboard_shot", args=[shot_id, None])

    return {
        "run_screenwriter": run_screenwriter_task,
        "run_casting_director": run_casting_director_task,
        "run_cinematographer": run_cinematographer_task,
        "run_storyboard_artist": run_storyboard_artist_task,
        "run_curator": run_curator_task,
        "run_editor": run_editor_task,
        "regen_storyboard_shot": regen_storyboard_shot_task,
        "regen_shot_plan": regen_shot_plan_task,
    }


# A screenplay cue extension after a speaker's name: (V.O.), (O.S.), (CONT'D).
_CUE = r"\((?:V\.?O\.?|O\.?S\.?|O\.?C\.?|CONT'D)\)"
# A speaker's name written just before a line: `DETECTIVE (V.O.): "...`, or a
# screenplay cue on its own line above the dialogue.
_SPEAKER_BEFORE_LINE = re.compile(
    r"([A-Za-z][\w'\-]*(?: [A-Za-z][\w'\-]*){0,3})\s*"
    r"(?:" + _CUE + r")?\s*:?\s*[\"\u201c]?\s*$"
)


def _canonical_speaker(name: str | None, characters: list[str]) -> str | None:
    """The breakdown's character a shot's speaker names, matched without its cue
    extension or case (`DETECTIVE (V.O.)` -> `DETECTIVE`); the bare name when none
    matches, so a Cast Library lookup by name still works."""
    bare = re.sub(r"\s*" + _CUE + r"\s*$", "", name or "", flags=re.IGNORECASE).strip()
    if not bare:
        return None
    return {c.upper(): c for c in characters}.get(bare.upper(), bare)


def _speaker_in_script(script_text: str, dialogue: str | None, characters: list[str]) -> str | None:
    """The character whose name the script writes before this line, for a shot
    whose speaker the screenwriter model left empty. Only names of characters the
    breakdown found are accepted, so a scene heading is never taken for one."""
    probe = (dialogue or "").strip().strip('"\u201c\u201d')[:40]
    at = script_text.find(probe) if probe else -1
    if at < 0:
        return None
    m = _SPEAKER_BEFORE_LINE.search(script_text[max(0, at - 80):at])
    if not m:
        return None
    by_upper = {c.upper(): c for c in characters}
    words = m.group(1).split()
    # The longest run of trailing words that names a character: "the Detective" -> Detective.
    for k in range(len(words)):
        found = by_upper.get(" ".join(words[k:]).upper())
        if found:
            return found
    return None


def _production_character(prod_id: int, name: str):
    """The production's own character Subject called ``name``, or None."""
    return (
        Subject.query.join(ProductionSubject, ProductionSubject.subject_id == Subject.id)
        .filter(ProductionSubject.production_id == prod_id,
                Subject.kind == "character", Subject.name == name)
        .first()
    )


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
        # Parse deterministic casting markup BEFORE the LLM sees the script, so
        # Gemma reads natural prose (markup syntax stripped, names kept) and the
        # operator's intent is authoritative — not subject to model compliance.
        markup = parse_markup(ctx.production.script_text)
        input_data = markup.cleaned_text

        inv = agent.invoke(input_data)
        ctx.persist(inv, input_json={"script_text": input_data})

        out = inv.output
        # Reconcile Gemma's guessed subjects with the markup intents. This sets
        # each subject's final kind and whether it is an identity-locked cast
        # member (cast_required) that must be LoRA-trained before the production
        # can leave casting. Props/environments default to inline generation.
        resolved_subjects = apply_intents(
            [{"name": s.name, "kind": s.kind, "description": s.description} for s in out.subjects],
            markup.intents,
        )
        for subj in resolved_subjects:
            existing = Subject.query.filter_by(name=subj["name"], kind=subj["kind"]).first()
            if existing:
                # A Cast Library subject belongs to every production that names
                # it. Link it as it is; this script's text and cast pin stay on
                # the link. An empty description is the one gap worth filling.
                if not (existing.description or "").strip():
                    existing.description = subj["description"]
                subject_to_link = existing
            else:
                new_subj = Subject(
                    name=subj["name"], kind=subj["kind"],
                    description=subj["description"], cast_required=subj["cast_required"],
                )
                db.session.add(new_subj)
                db.session.flush()  # get ID
                subject_to_link = new_subj

            db.session.add(ProductionSubject(
                production_id=prod_id,
                subject_id=subject_to_link.id,
                script_description=subj["description"],
                cast_required=subj["cast_required"],
            ))
        
        characters = [subj["name"] for subj in resolved_subjects if subj["kind"] == "character"]
        for scene in out.scenes:
            for shot in scene.shots:
                new_shot = ProductionShot(
                    production_id=prod_id,
                    scene_number=scene.number,
                    shot_number=shot.number,
                    description=shot.description,
                    scene_mood=scene.mood,
                    character_name=(_canonical_speaker(shot.character_name, characters)
                                    or _speaker_in_script(input_data, shot.dialogue, characters)),
                    dialogue_text=shot.dialogue
                )
                db.session.add(new_shot)
        
        db.session.commit()


def run_casting_director(prod_id: int, llm=None):
    if llm is None:
        llm = _default_ollama_llm

    # ORPHAN / ADVISORY-ONLY: this task is never dispatched. STAGE_TO_AGENT
    # ["casting"] is None (user-gated), the screenwriter advances with
    # next_agent=None, and nothing calls celery.send_task("production.run_casting
    # _director"). It only writes a *recommendation* SwarmMessage and applies the
    # cheap voice_id. The LLM's `action` (use_existing_lora / train_from_* ) is
    # deliberately NOT applied here — applying it would auto-trigger GPU LoRA
    # training unprompted on the shared GPU, which is forbidden. Real casting +
    # LoRA training is USER-GATED via production_api.cast_subject / confirm_casting.
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
        
        # The built-in voices installed on this machine: the ids the Editor's
        # voiceover can speak. Read from the voice catalog the plugin ships,
        # so the list does not depend on Audio Foundry running.
        available_voices = []
        try:
            from backend.services.audio_foundry_models import kokoro_voice_choices
            available_voices = kokoro_voice_choices(installed_only=True)
        except Exception as e:
            import logging
            logging.warning(f"Could not read the Audio Foundry voice catalog: {e}")

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
    """The subjects linked to this production, and only those.

    A production with no linked subjects gets an empty list: the shot planner
    may only put a Cast LoRA on a shot when that subject belongs to the film.
    """
    return (
        db.session.query(Subject)
        .join(ProductionSubject)
        .filter(ProductionSubject.production_id == prod_id)
        .all()
    )


class _DirectorInvocation:
    """A tiny AgentInvocation look-alike so ``ctx.persist`` and the existing write-back loop
    work unchanged when the unified Director produces the shot plan (instead of the swarm
    Cinematographer). Mirrors the attributes ``_AgentRunContext.persist`` reads."""
    def __init__(self, output, *, model: str):
        self.output = output            # ShotPlanList (has .model_dump() + .plans)
        self.model = model
        self.status = "ok"
        self.error_text = None
        self.latency_ms = 0


def _cinematographer_via_director(input_data: dict):
    """PRIMARY cinematographer path: route the script breakdown through the unified Director
    (``director_service.plan(SCRIPT_SCENES)``) — batched, treatment-driven, CREATIVE-sampled,
    a clear upgrade over the single-call swarm agent.

    Returns a ``_DirectorInvocation`` (status='ok') wrapping a ``ShotPlanList`` whose plans map
    1:1, BY INPUT ORDER, back to the input shots — so scene/shot numbers come from the DB rows
    (robust) and the existing persistence loop + FK filter run unchanged. Returns ``None`` on
    any failure / thin result so the caller falls back to the swarm Cinematographer. NEVER
    raises (preserves the never-raise stage contract)."""
    try:
        from backend.services.director_service import plan, DirectorBrief, DirectorMode, DIRECTOR_MODEL
        in_shots = input_data.get("shots", []) or []
        if not in_shots:
            return None
        # Flatten the production shots into the single-scene shape the SCRIPT_SCENES planner
        # expects; order is preserved so we can zip results back by index.
        scenes = [{
            "location": "", "mood": "",
            "shots": [{"description": s.get("description", ""), "subjects_in_shot": []} for s in in_shots],
        }]
        result = plan(DirectorBrief(
            mode=DirectorMode.SCRIPT_SCENES,
            scenes=scenes,
            subjects=input_data.get("subjects", []) or [],
            # cast is intentionally NOT passed: identity is injected at RENDER time in
            # _shot_loras_and_prompt (via shared cast_lock) — that survives prompt edits and
            # stays per-shot scoped, which plan-time global injection would lose. Leaving
            # brief.cast empty here is what keeps the lock applied exactly once (no double-lock).
        ))
        directed = result.shots or []
        # Require a usable, full-coverage result; otherwise fall back to the agent.
        if len(directed) < len(in_shots) or any(not (d.prompt or "").strip() for d in directed[:len(in_shots)]):
            return None
        plans = []
        for i, s in enumerate(in_shots):
            d = directed[i]
            plans.append(ShotPlan(
                scene_number=int(s.get("scene_number", 0)),
                shot_number=int(s.get("shot_number", i)),
                camera_angle=(d.camera or "medium"),
                framing=(d.framing or ""),
                duration_seconds=float(d.duration) if isinstance(d.duration, (int, float)) else 4.0,
                mood=(d.mood or ""),
                image_prompt=d.prompt.strip(),
                subjects_in_shot=[int(x) for x in (d.subjects or [])],
            ))
        return _DirectorInvocation(ShotPlanList(plans=plans), model=f"director:{DIRECTOR_MODEL}")
    except Exception as e:  # noqa: BLE001 — director must never fail the stage; fall back
        import logging
        logging.getLogger(__name__).warning(
            "cinematographer via director failed (%s); falling back to swarm agent", e
        )
        return None


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

        # PRIMARY: unified Director (batched, treatment-driven). FALLBACK: swarm Cinematographer.
        inv = _cinematographer_via_director(input_data)
        if inv is None:
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
    """Collect a shot's character LoRA paths and the raw *scene* prompt.

    Identity lock (trigger + class + short marks) is applied once inside
    ``render_character_still`` when subjects are passed — do not pre-lock here
    or the core is doubled.
    """
    from backend.services.cast_lock import subjects_to_lock
    subjects = [pss.subject for pss in shot.shot_subjects if pss.subject]
    lora_paths, _lock = subjects_to_lock(subjects, include_bible=False)
    base = (
        getattr(shot, "image_prompt", None)
        or getattr(shot, "description", None)
        or ""
    ).strip()
    return lora_paths, base


def _shot_identity_subjects(shot) -> list:
    """The shot's subjects that carry an identity into its storyboard still.

    A subject with a trained LoRA, or one this production requires as a cast
    member, is locked. Props, places and characters cast as described have no
    LoRA and are drawn from the shot's own words; passing them would trip the
    still pipeline's refusal to render a cast member with nothing to load.
    """
    from backend.services.swarm.script_markup import effective_cast_required
    subjects = [pss.subject for pss in shot.shot_subjects if pss.subject]
    if not subjects:
        return []
    pins = {
        link.subject_id: link.cast_required
        for link in ProductionSubject.query.filter_by(production_id=shot.production_id)
    }
    locked = []
    for s in subjects:
        pinned = pins.get(s.id)
        required = effective_cast_required(
            pinned if pinned is not None else s.cast_required, s.kind
        )
        if s.lora_path or required:
            locked.append(s)
    return locked


def _still_canvas(production) -> dict:
    """width/height for a storyboard still in the production's frame shape;
    empty, so the stills model keeps its default, when none is set."""
    from backend.services.production_service import film_frame_shape
    shape = film_frame_shape(getattr(production, "settings_json", None))
    if not shape:
        return {}
    width, height = shape["still"]
    return {"width": width, "height": height}


# Seconds before a storyboard redraw refused by the GPU gate asks again; the
# gate's cooldown between jobs is shorter.
_REGEN_RETRY_S = 15


def _storyboard_path(prod_id: int, scene_number: int, shot_number: int) -> str:
    from backend.config import STORAGE_DIR
    out_dir = Path(STORAGE_DIR) / "outputs" / "storyboards" / str(prod_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"shot_{scene_number}_{shot_number}.png")


def run_storyboard_artist(prod_id: int, image_generator=None):
    with _agent_run(prod_id, agent_name="storyboard_artist", expected_stage="storyboard_gen", next_agent=None) as ctx:
        if ctx is None:
            return

        shots = ProductionShot.query.filter_by(production_id=prod_id).all()

        # Claim the GPU exclusively around the still generate loop. Storyboard
        # gen rides VIDEO_RENDER; Z-Image cast LoRAs go offline via
        # character_still_pipeline (never force Comfy/SDXL).
        from backend.services.job_types import JobKind
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.character_still_pipeline import render_character_still
        with gpu_session(
            JobKind.VIDEO_RENDER,
            f"storyboard_{prod_id}",
            evict_ollama=True,
            free_comfyui=True,
            vram_estimate_mb=11000,
            require_fit=True,
            cross_process=True,
        ):
            for i, shot in enumerate(shots):
                lora_paths, prompt = _shot_loras_and_prompt(shot)
                output_path = _storyboard_path(
                    prod_id,
                    shot.scene_number or 1,
                    shot.shot_number or (i + 1),
                )
                subjects = _shot_identity_subjects(shot)
                if lora_paths or image_generator is None:
                    still = render_character_still(
                        prompt,
                        subjects=subjects,
                        include_bible=True,
                        source="filmcrew",
                        output_path=output_path,
                        keep_pipeline=True,
                        **_still_canvas(ctx.production),
                    )
                    if not still.success:
                        raise RuntimeError(still.error or f"storyboard still failed for shot {shot.shot_number}")
                    shot.storyboard_image_path = still.image_path
                else:
                    # Injected test double (legacy Comfy generator).
                    shot.storyboard_image_path = image_generator.generate_image(
                        prompt=prompt, loras=lora_paths, output_path=output_path,
                    )

            db.session.commit()

def run_curator(prod_id: int) -> dict:
    """Storyboard curation, advice only: the vision model judges each frame and
    pre-ticks ProductionShot.approved on the ones it judges usable. The stage stays
    at awaiting_approval even when every shot passes; the render starts only when a
    person approves the storyboard. Idempotent (no-ops unless at awaiting_approval)."""
    import logging
    from backend.services.film_curator_service import auto_curate
    log = logging.getLogger(__name__)
    summary = auto_curate(prod_id, do_advance=False)
    if not summary.get("skipped"):
        log.info("Curator pre-ticked %s of %s shots for production %s (flagged %s) -> awaiting human approval",
                 summary.get("approved"), summary.get("total"), prod_id, summary.get("flagged_shots"))
    return summary


def run_editor(prod_id: int, i2v=None, audio_foundry=None, ffmpeg=None):
    with _agent_run(prod_id, agent_name="editor", expected_stage="rendering", next_agent=None) as ctx:
        if ctx is None:
            return

        # A production may name the video model that renders its shots
        # (settings_json.video_model, chosen in the create dialog). A model that
        # declares audio_out renders each scene as one window with its own
        # soundtrack instead of per-shot silent clips plus TTS.
        settings = dict(getattr(ctx.production, "settings_json", None) or {})
        video_model = str(settings.get("video_model") or "").strip() or None
        scene_renderer = None
        vram_estimate_mb = 14000
        from backend.services.video_model_registry import (
            resolve_active_video_model, model_capabilities, admission_vram_mb,
        )
        resolved, resolve_err = resolve_active_video_model(
            "i2v", video_model, surface="film-crew",
        )
        from backend.services.plugin_bridge import job_service_start_enabled
        if job_service_start_enabled() and (video_model or resolved):
            # Start ComfyUI when the model renders there and it is down, the
            # way a Video Gen batch does, then resolve again against it.
            from backend.services.video_model_registry import prepare_video_model
            ready, prep_err = prepare_video_model(video_model or resolved)
            if ready and resolve_err:
                resolved, resolve_err = resolve_active_video_model(
                    "i2v", video_model, surface="film-crew",
                )
            elif not ready and not resolve_err:
                resolve_err = prep_err
        if resolve_err:
            # ComfyUI down: the sentence every tool gives for a stopped service.
            from backend.services.video_model_registry import comfyui_down_failure
            ctx.fail(comfyui_down_failure(resolve_err, "film-crew"))
            return
        caps = model_capabilities(resolved)
        vram_estimate_mb = admission_vram_mb(resolved, default=14000)
        if caps.get("audio_out") and (caps.get("supports_i2v") or "ref2v" in caps.get("modes", [])):
            from backend.services.comfyui_video_generator import MiniMaxH3SceneGenerator
            scene_renderer = MiniMaxH3SceneGenerator(model=resolved)

        if i2v is None:
            # Animate each LoRA-consistent storyboard frame into a clip. Identity
            # rides in the frame either way. GUAARDVARK_FILM_I2V=cogvideox reverts
            # to the CogVideoX-backed adapter. Otherwise the resolved registry id.
            engine = os.environ.get("GUAARDVARK_FILM_I2V", "").strip().lower()
            if engine in ("cogvideox", "cog", "svd"):
                from backend.services.comfyui_video_generator import SvdI2VGenerator
                i2v = SvdI2VGenerator()
            else:
                from backend.services.comfyui_video_generator import Wan22I2VGenerator
                from backend.services.production_service import film_frame_shape
                shape = film_frame_shape(settings)
                i2v = Wan22I2VGenerator(
                    model=resolved,
                    fps=int(caps.get("native_fps") or 24),
                    canvas=shape["clip"] if shape else None,
                )

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

        # Script order: the clips are cut together in this order, and without
        # it the database returns rows in whatever order they were last updated.
        shots = (ProductionShot.query.filter_by(production_id=prod_id, approved=True)
                 .order_by(ProductionShot.scene_number, ProductionShot.shot_number).all())
        if not shots:
            ctx.fail("No approved shots")

        from backend.utils.backend_http import backend_base_url
        backend_url = f"{backend_base_url()}/api"
        ve_client = VideoEditorComposeClient(backend_url)

        editor = Editor(
            i2v=i2v,
            audio_foundry=audio_foundry,
            ffmpeg=ffmpeg,
            video_editor=ve_client,
            scene_renderer=scene_renderer,
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
                # The named speaker, in the shot or not: a voice-over narrator is
                # cast in the production but rarely appears in the shot.
                speaker = (next((c for c in char_cast if c.name == s.character_name), None)
                           or _production_character(prod_id, s.character_name))
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
                scene_mood=s.scene_mood,
                character_name=(speaker.name if speaker else s.character_name),
                ref_image_paths=list((speaker.ref_image_paths if speaker else None) or []),
                character_description=(speaker.description if speaker else None),
            ))
            
        # The film, its clips and its timeline are registered as documents by
        # path, so they live under the data folder, not a temp folder that a
        # reboot empties. Each render gets its own folder; earlier cuts stay.
        from datetime import datetime
        from backend.config import STORAGE_DIR
        output_dir = str(
            Path(STORAGE_DIR) / "outputs" / "productions" / str(prod_id)
            / datetime.now().strftime("render_%Y%m%d_%H%M%S")
        )
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Ride the unified job rail: a VIDEO_RENDER progress process so the
        # production render shows up in /api/jobs/active and the gate snapshot,
        # exactly like a standalone editor render.
        from backend.utils.unified_progress_system import get_unified_progress, ProcessType
        from backend.services.job_operation_gate import GpuBusyError
        from backend.services.gpu_resource_policy import gpu_session
        from backend.services.job_types import JobKind
        from backend.services.production_documents import register_production_output

        progress = get_unified_progress()
        render_id = f"prod_{prod_id}"
        job_id = progress.create_process(
            ProcessType.VIDEO_RENDER,
            f"Rendering production {prod_id}: {ctx.production.name}",
            additional_data={"production_id": prod_id},
        )
        # Claim the GPU exclusively for the render via the unified front door.
        # gpu_session delegates to the in-memory gate (same fail-fast GpuBusyError,
        # caught below -> progress.error_process; _agent_run marks the stage failed)
        # and, once we hold the slot, evicts Ollama (a chat's resident gemma can't
        # fight WAN on 16GB — parity with the music-video render) and frees ComfyUI
        # so the storyboard stage's resident FLUX doesn't OOM the first shot's i2v.
        # (Per-shot i2v<->TTS interleave reclaim is a separate P0.3c refinement.)
        try:
            # vram_estimate_mb debits the GPU orchestrator budget so this ~14GB render is
            # VISIBLE to it (it can evict competing in-process models) — without it the
            # render and a resident chat model both think they own the 16GB card.
            with gpu_session(
                JobKind.VIDEO_RENDER,
                render_id,
                evict_ollama=True,
                free_comfyui=True,
                vram_estimate_mb=vram_estimate_mb,
                require_fit=True,
                cross_process=True,
            ):
                progress.update_process(job_id, 5, f"Rendering {len(shot_inputs)} shots")
                res = editor.render(
                    production_id=prod_id,
                    production_name=ctx.production.name,
                    shots=shot_inputs,
                    output_dir=output_dir,
                )

                voice_records = list(getattr(res, "voice_records", None) or [])
                for i, shot in enumerate(shots):
                    if i < len(res.clip_paths):
                        shot.video_clip_path = res.clip_paths[i]
                    # Which voice spoke the line, so a fallback shows in the
                    # production view instead of only in a log.
                    record = voice_records[i] if i < len(voice_records) else None
                    shot.voice_record = record if isinstance(record, dict) else None
                    if shot.voice_record and shot.voice_record.get("fallbacks"):
                        import logging
                        logging.getLogger(__name__).warning(
                            "production %s shot %s/%s voice fallback: %s", prod_id,
                            shot.scene_number, shot.shot_number,
                            "; ".join(f["message"] for f in shot.voice_record["fallbacks"]))

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
        except GpuBusyError as e:
            progress.error_process(job_id, f"Production render deferred — GPU busy: {e}")
            raise
        except Exception as e:
            progress.error_process(job_id, f"Production render failed: {e}")
            raise


def regen_storyboard_shot(shot_id: int, prompt_override: str | None = None, image_generator=None):
    shot = db.session.get(ProductionShot, shot_id)
    if not shot or shot.production.current_stage != "awaiting_approval":
        import logging
        logging.warning("Regen storyboard shot called when not awaiting approval")
        return

    lora_paths, base_prompt = _shot_loras_and_prompt(shot)
    prompt = prompt_override if prompt_override else base_prompt
    output_path = _storyboard_path(
        shot.production_id,
        shot.scene_number or 1,
        shot.shot_number or shot.id,
    )
    from backend.services.job_types import JobKind
    from backend.services.gpu_resource_policy import gpu_session
    from backend.services.character_still_pipeline import render_character_still
    with gpu_session(
        JobKind.VIDEO_RENDER,
        f"storyboard_{shot.production_id}",
        evict_ollama=True,
        free_comfyui=True,
        vram_estimate_mb=11000,
        require_fit=True,
        cross_process=True,
    ):
        if image_generator is not None:
            shot.storyboard_image_path = image_generator.generate_image(
                prompt=prompt, loras=lora_paths, output_path=output_path,
            )
        else:
            subjects = _shot_identity_subjects(shot)
            still = render_character_still(
                prompt,
                subjects=subjects,
                include_bible=True,
                source="filmcrew",
                output_path=output_path,
                keep_pipeline=False,
                **_still_canvas(db.session.get(Production, shot.production_id)),
            )
            if not still.success:
                raise RuntimeError(still.error or "storyboard regen failed")
            shot.storyboard_image_path = still.image_path
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
