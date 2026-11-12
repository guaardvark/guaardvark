"""The infographics outreach may attach: which text an image was asked to
show, how a read-back is judged, and when an image is usable."""

import json
from datetime import datetime, timedelta

import pytest

from backend.models import Document, SocialOutreachLog, db
from backend.services.social_outreach import infographics, run_result

HEAD = (
    'A tall vertical infographic poster. Across the top of the poster, a large bold headline reads '
    '"GUAARDVARK ON WINDOWS 11". The first label reads "INSTALL THROUGH WSL2". The second label reads '
    '"OR RUN IT IN DOCKER DESKTOP". A solid banner runs along the bottom edge with the text '
    '"CHAT, PICTURES AND VIDEO MADE ON YOUR PC" in clean white letters.'
)
TAIL = (
    "\n```\n**Structured fields**\n- Title: `GUAARDVARK ON WINDOWS 11`\n"
    '- Scene: `A poster whose headline reads "SCENE TEXT IS NOT DRAWN"`\n'
    "- Callouts:\n  ```\n  INSTALL THROUGH WSL2\n  OR RUN IT IN DOCKER DESKTOP\n  ```\n"
    "- Footer: `CHAT, PICTURES AND VIDEO MADE ON YOUR PC`"
)
EXPECTED = [
    "GUAARDVARK ON WINDOWS 11",
    "INSTALL THROUGH WSL2",
    "OR RUN IT IN DOCKER DESKTOP",
    "CHAT, PICTURES AND VIDEO MADE ON YOUR PC",
]
CLEAN = "GUAARDVARK\nON WINDOWS 11\nINSTALL THROUGH\nWSL2\nOR RUN IT IN\nDOCKER DESKTOP\nCHAT, PICTURES AND VIDEO MADE ON YOUR PC"


# --- Expected text ---------------------------------------------------------

def test_structured_spec_gives_title_callouts_footer_and_hashtags():
    meta = {"source": "infographic", "infographic": {
        "title": "Three numbers", "scene": 'a sign that says "not this"', "footer": "guaardvark.com",
        "hashtags": ["LocalAI", "#OpenSource"], "callouts": ['"16 GB"', "0 uploads", ""],
        "style": "flat_vector", "aspect": "1:1",
    }}
    assert infographics.expected_text(meta) == [
        "Three numbers", "16 GB", "0 uploads", "guaardvark.com", "#LocalAI #OpenSource",
    ]


def test_raw_prompt_gives_its_quoted_strings_in_order():
    meta = {"source": "infographic", "infographic": {"raw_prompt": HEAD, "aspect": "9:16"}}
    assert infographics.expected_text(meta) == EXPECTED


def test_markdown_tail_is_not_text_to_draw():
    meta = {"source": "infographic", "infographic": {"raw_prompt": HEAD + TAIL}}
    assert infographics.expected_text(meta) == EXPECTED


def test_tail_fields_are_used_when_the_prompt_quotes_nothing():
    meta = {"source": "infographic", "infographic": {"raw_prompt": "A poster about Windows." + TAIL}}
    assert infographics.expected_text(meta) == EXPECTED


def test_prompt_only_metadata_and_curly_quotes():
    assert infographics.expected_text({"source": "infographic", "prompt": 'Headline reads “Three numbers”.'}) == [
        "Three numbers"]
    assert infographics.expected_text({"source": "infographic", "original_prompt": 'Reads "A". Reads "a".'}) == ["A"]
    assert infographics.expected_text({"source": "infographic"}) == []


# --- Comparison ------------------------------------------------------------

def test_wrapped_lines_case_and_punctuation_still_match():
    verdict = infographics.compare(EXPECTED, CLEAN.replace("CHAT, PICTURES", "Chat  pictures"), HEAD)
    assert verdict == {"passed": True, "missing": [], "extra": [], "reasons": []}


def test_a_misspelled_line_is_missing():
    verdict = infographics.compare(EXPECTED, CLEAN.replace("DESKTOP", "DESKTP"), HEAD)
    assert verdict["passed"] is False
    assert verdict["missing"] == ["OR RUN IT IN DOCKER DESKTOP"]
    assert "DOCKER DESKTP" in verdict["extra"]


def test_a_command_must_show_its_punctuation():
    expected = ["RUN IT", "./start-docker.sh --gpu"]
    assert infographics.compare(expected, "RUN IT\n./start-docker.sh --gpu")["passed"] is True
    assert infographics.compare(expected, "RUN IT\n./start-docker.sh  --GPU")["passed"] is True
    assert infographics.compare(expected, "RUN IT\nstart docker sh gpu")["missing"] == ["./start-docker.sh --gpu"]


@pytest.mark.parametrize("leak", [
    "**Structured fields**", "- Title: `GUAARDVARK ON WINDOWS 11`", "Callouts:", "`WSL2`",
])
def test_prompt_markup_on_the_image_fails(leak):
    verdict = infographics.compare(EXPECTED, CLEAN + "\n" + leak, HEAD + TAIL)
    assert verdict["passed"] is False
    assert leak in verdict["extra"]
    assert any("markup" in reason for reason in verdict["reasons"])


def test_prompt_instructions_drawn_as_text_fail():
    verdict = infographics.compare(EXPECTED, CLEAN + "\nlarge bold headline", HEAD)
    assert verdict["passed"] is False
    assert any("instructions" in reason for reason in verdict["reasons"])


def test_a_line_drawn_twice_fails():
    verdict = infographics.compare(EXPECTED, CLEAN + "\nINSTALL THROUGH WSL2", HEAD)
    assert verdict["passed"] is False
    assert verdict["extra"] == ["INSTALL THROUGH WSL2"]


def test_a_line_contained_in_another_is_not_a_repeat():
    expected = ["GUAARDVARK", "GUAARDVARK ON WINDOWS"]
    assert infographics.compare(expected, "GUAARDVARK\nGUAARDVARK ON WINDOWS")["passed"] is True


def test_small_decoration_is_listed_but_does_not_fail():
    verdict = infographics.compare(EXPECTED, CLEAN + "\nAI\n01", HEAD)
    assert verdict["passed"] is True
    assert verdict["extra"] == ["AI", "01"]


def test_a_line_of_unasked_text_fails():
    verdict = infographics.compare(EXPECTED, CLEAN + "\nSUBSCRIBE FOR MORE TIPS", HEAD)
    assert verdict["passed"] is False
    assert verdict["missing"] == []


# --- Read-back -------------------------------------------------------------

META = {"source": "infographic", "infographic": {"raw_prompt": HEAD + TAIL, "aspect": "9:16"}}


def _clean(path):
    return ""


def test_read_passes_a_clean_transcript_and_keeps_the_asked_text_as_expected():
    result = infographics.read(META, None, transcriber=lambda path: (CLEAN, "reader:1"), inspector=_clean)
    assert result["status"] == "passed"
    assert result["expected"] == EXPECTED
    assert result["transcript"] == CLEAN
    assert result["model"] == "reader:1"
    assert result["checked_at"]


def test_read_fails_a_bad_transcript():
    result = infographics.read(META, None, transcriber=lambda path: (CLEAN.replace("WSL2", "WSL"), "reader:1"))
    assert result["status"] == "failed"
    assert result["missing"] == ["INSTALL THROUGH WSL2"]


def test_damaged_lettering_fails_a_transcript_that_reads_clean():
    result = infographics.read(META, None, transcriber=lambda path: (CLEAN, "reader:1"),
                               inspector=lambda path: 'the word "THE" is overlapped by a shape')
    assert result["status"] == "failed"
    assert result["missing"] == [] and result["extra"] == []
    assert result["reasons"] == ['Damaged lettering: the word "THE" is overlapped by a shape']


def test_a_lettering_check_that_could_not_run_never_passes():
    def broken(path):
        raise infographics.ReadbackUnavailable("the lettering check gave no verdict")

    result = infographics.read(META, None, transcriber=lambda path: (CLEAN, "reader:1"), inspector=broken)
    assert result["status"] == "unread"


def test_a_failed_model_call_never_passes():
    def broken(path):
        raise infographics.ReadbackUnavailable("Ollama timed out after 240s")

    result = infographics.read(META, None, transcriber=broken)
    assert result["status"] == "unread"
    assert "timed out" in result["error"]


def test_nothing_to_check_stays_unread():
    result = infographics.read({"source": "infographic", "prompt": "a poster"}, None,
                               transcriber=lambda path: ("ANYTHING", "reader:1"))
    assert result["status"] == "unread"
    assert result["error"]


# --- Library rows ----------------------------------------------------------

@pytest.fixture(autouse=True)
def _uploads(tmp_path, monkeypatch):
    """The uploads folder is the test's own temporary folder."""
    monkeypatch.setattr("backend.config.UPLOAD_DIR", str(tmp_path))


def _add(tmp_path, name, meta, when=None, write=True):
    if write:
        (tmp_path / name).write_bytes(b"\x89PNG")
    doc = Document(filename=name, path=name, type=".png", file_metadata=json.dumps(meta),
                   uploaded_at=when or datetime.now())
    db.session.add(doc)
    db.session.commit()
    return doc.id


BATCH_META = {
    "source": "infographic", "batch_id": "InfographicBatch_x", "original_prompt": HEAD + TAIL,
    "model_used": "moss-image", "seed_used": 7, "dimensions": "720x1280", "style": "editorial",
    "infographic": {"raw_prompt": HEAD + TAIL, "style": "editorial", "aspect": "9:16"},
}
SINGLE_META = {
    "source": "infographic", "model": "krea2-turbo", "seed": 3, "prompt": "composed", "width": 1280, "height": 720,
    "infographic": {"title": "Three numbers", "scene": "navy", "footer": "guaardvark.com", "hashtags": [],
                    "callouts": ["16 GB"], "style": "flat_vector", "aspect": "16:9"},
}


def test_items_lists_both_metadata_shapes_newest_first(app, tmp_path):
    old = _add(tmp_path, "a.png", SINGLE_META, datetime.now() - timedelta(days=1))
    new = _add(tmp_path, "b.png", BATCH_META)
    _add(tmp_path, "c.png", {"source": "batch_generation", "original_prompt": "an infographic"})

    listed = infographics.items()
    assert [item["document_id"] for item in listed] == [new, old]
    batch, single = listed
    assert (batch["headline"], batch["lines"]) == (EXPECTED[0], EXPECTED[1:])
    assert (batch["width"], batch["height"], batch["model"], batch["aspect"]) == (720, 1280, "moss-image", "9:16")
    assert (single["headline"], single["lines"]) == ("Three numbers", ["16 GB", "guaardvark.com"])
    assert (single["width"], single["height"], single["model"]) == (1280, 720, "krea2-turbo")
    assert batch["image_url"] == f"/api/social-outreach/infographics/{new}/image"
    assert batch["keeper"] is False and batch["usable"] is False
    assert batch["readback"]["status"] == "unread"
    assert infographics.items(keepers_only=True) == []


def test_marking_a_keeper_queues_the_read_back_and_keeps_every_other_key(app, tmp_path, monkeypatch):
    doc_id = _add(tmp_path, "b.png", BATCH_META)
    queued = []
    monkeypatch.setattr(infographics, "_dispatch", queued.append)

    item = infographics.mark(doc_id, True, topics=["windows", " "])
    assert queued == [doc_id]
    assert item["keeper"] is True and item["topics"] == ["windows"]
    assert item["readback"]["status"] == "reading"
    assert item["usable"] is False

    stored = json.loads(db.session.get(Document, doc_id).file_metadata)
    assert {k: v for k, v in stored.items() if k != "outreach"} == BATCH_META
    assert stored["outreach"]["keeper"] is True


def test_usable_needs_both_the_keeper_mark_and_a_passed_read_back(app, tmp_path, monkeypatch):
    doc_id = _add(tmp_path, "b.png", BATCH_META)
    monkeypatch.setattr(infographics, "_dispatch", lambda document_id: None)

    passed = infographics.readback(doc_id, transcriber=lambda path: (CLEAN, "reader:1"), inspector=_clean)
    assert passed["readback"]["status"] == "passed" and passed["usable"] is False
    with pytest.raises(infographics.InfographicNotUsable):
        infographics.media_for(doc_id)

    kept = infographics.mark(doc_id, True)
    assert kept["usable"] is True
    assert kept["readback"]["status"] == "passed"
    assert [item["document_id"] for item in infographics.items(keepers_only=True)] == [doc_id]

    failed = infographics.readback(doc_id, transcriber=lambda path: (CLEAN + "\n**Structured fields**", "reader:1"), inspector=_clean)
    assert failed["keeper"] is True and failed["usable"] is False
    assert infographics.mark(doc_id, False)["keeper"] is False


def test_a_read_back_that_could_not_run_leaves_the_image_unread(app, tmp_path, monkeypatch):
    doc_id = _add(tmp_path, "b.png", BATCH_META)

    def broken(path):
        raise RuntimeError("connection refused")

    item = infographics.readback(doc_id, transcriber=broken)
    assert item["readback"]["status"] == "unread"
    assert "connection refused" in item["readback"]["error"]

    def unqueued(document_id):
        raise RuntimeError("broker down")

    item = infographics.queue_readback(doc_id, dispatch=unqueued)
    assert item["readback"]["status"] == "unread"
    assert "broker down" in item["readback"]["error"]


def test_a_read_back_left_reading_too_long_reads_as_unread(app, tmp_path):
    started = (datetime.now() - timedelta(seconds=infographics.READING_STALE_S + 60)).isoformat()
    meta = {**BATCH_META, "outreach": {"keeper": True, "readback": {"status": "reading", "started_at": started}}}
    doc_id = _add(tmp_path, "b.png", meta)
    assert infographics.describe(doc_id)["readback"]["status"] == "unread"


def test_file_path_and_missing_files(app, tmp_path):
    doc_id = _add(tmp_path, "b.png", BATCH_META)
    assert infographics.file_path(doc_id) == tmp_path / "b.png"
    gone = _add(tmp_path, "gone.png", BATCH_META, write=False)
    with pytest.raises(infographics.InfographicFileMissing):
        infographics.file_path(gone)
    other = _add(tmp_path, "c.png", {"source": "batch_generation"})
    with pytest.raises(infographics.InfographicNotFound):
        infographics.describe(other)
    assert infographics.readback(gone, transcriber=lambda path: (CLEAN, "r"), inspector=_clean)["readback"]["status"] == "unread"


def test_media_on_an_outreach_row(app, tmp_path, monkeypatch):
    doc_id = _add(tmp_path, "b.png", BATCH_META)
    monkeypatch.setattr(infographics, "_dispatch", lambda document_id: None)
    infographics.readback(doc_id, transcriber=lambda path: (CLEAN, "reader:1"), inspector=_clean)
    infographics.mark(doc_id, True)

    media = infographics.media_for(doc_id)
    assert media == {"document_id": doc_id, "path": "b.png", "alt": ". ".join(EXPECTED)}

    row = SocialOutreachLog(platform="facebook", action="share", draft_text="hello", status="drafted",
                            media_json=json.dumps(media))
    plain = SocialOutreachLog(platform="facebook", action="share", draft_text="hello", status="drafted")
    db.session.add_all([row, plain])
    db.session.commit()

    assert infographics.media_of(row) == media
    shown = run_result.presentable(row)
    assert shown["image_document_id"] == doc_id
    assert shown["image_url"] == f"/api/social-outreach/infographics/{doc_id}/image"
    assert shown["image_alt"] == ". ".join(EXPECTED)

    bare = run_result.presentable(plain)
    assert (bare["image_url"], bare["image_alt"], bare["image_document_id"]) == (None, None, None)
    plain.media_json = "not json"
    assert infographics.media_of(plain) is None
