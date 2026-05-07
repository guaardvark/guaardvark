import pytest

try:
    from flask import Flask
    from pathlib import Path
    from backend.models import db, Production, Project, Folder, Document
    from backend.services.production_documents import register_production_output
except Exception:
    pytest.skip("Backend modules not available", allow_module_level=True)


@pytest.fixture
def app():
    app = Flask(__name__)
    app.config.update(
        {"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"}
    )
    db.init_app(app)
    with app.app_context():
        db.create_all()
        yield app
        db.session.remove()
        db.drop_all()


def _setup_production(project_id=None):
    prod = Production(name="P", script_text="x", project_id=project_id,
                      status="rendering", current_stage="rendering", settings_json={})
    db.session.add(prod); db.session.commit()
    return prod


def test_registers_storyboard_under_project_tree(app, tmp_path):
    proj = Project(name="MyProj"); db.session.add(proj); db.session.commit()
    prod = _setup_production(project_id=proj.id)

    image = tmp_path / "shot_1.png"
    image.write_bytes(b"fake_png_bytes")

    doc = register_production_output(
        production=prod, file_path=str(image), category="storyboard",
    )

    assert doc.id is not None
    assert doc.filename == "shot_1.png"
    assert doc.path == str(image)
    assert doc.size == len(b"fake_png_bytes")

    # Folder hierarchy: project_<id>/productions/<prod_id>/storyboard
    leaf = db.session.get(Folder, doc.folder_id)
    assert leaf.name == "storyboard"
    parent_prod = db.session.get(Folder, leaf.parent_id)
    assert parent_prod.name == str(prod.id)
    parent_productions = db.session.get(Folder, parent_prod.parent_id)
    assert parent_productions.name == "productions"
    parent_root = db.session.get(Folder, parent_productions.parent_id)
    assert parent_root.name == f"project_{proj.id}"
    assert parent_root.parent_id is None


def test_orphan_production_lands_under_orphan_root(app, tmp_path):
    prod = _setup_production(project_id=None)
    f = tmp_path / "final.mp4"
    f.write_bytes(b"mp4")
    doc = register_production_output(
        production=prod, file_path=str(f), category="final",
    )
    leaf = db.session.get(Folder, doc.folder_id)
    parent_prod = db.session.get(Folder, leaf.parent_id)
    parent_productions = db.session.get(Folder, parent_prod.parent_id)
    parent_root = db.session.get(Folder, parent_productions.parent_id)
    assert parent_root.name == "orphan"
    assert parent_root.parent_id is None


def test_reuses_existing_folder_hierarchy(app, tmp_path):
    """Two outputs in the same category must land in the same leaf folder."""
    proj = Project(name="P"); db.session.add(proj); db.session.commit()
    prod = _setup_production(project_id=proj.id)

    f1 = tmp_path / "shot_1.png"; f1.write_bytes(b"a")
    f2 = tmp_path / "shot_2.png"; f2.write_bytes(b"b")

    d1 = register_production_output(production=prod, file_path=str(f1), category="storyboard")
    d2 = register_production_output(production=prod, file_path=str(f2), category="storyboard")

    assert d1.folder_id == d2.folder_id
    assert Folder.query.filter_by(name="storyboard").count() == 1


def test_invalid_category_raises(app, tmp_path):
    prod = _setup_production(project_id=None)
    f = tmp_path / "x.png"; f.write_bytes(b"x")
    with pytest.raises(ValueError, match="category"):
        register_production_output(production=prod, file_path=str(f), category="not_a_category")


def test_size_zero_when_file_missing(app, tmp_path):
    """If the file isn't on disk yet (registration before write), size is 0."""
    prod = _setup_production(project_id=None)
    fake_path = str(tmp_path / "does_not_exist.mp4")
    doc = register_production_output(
        production=prod, file_path=fake_path, category="clips",
    )
    assert doc.size == 0


def test_separate_categories_get_separate_folders(app, tmp_path):
    proj = Project(name="P"); db.session.add(proj); db.session.commit()
    prod = _setup_production(project_id=proj.id)

    fs = tmp_path / "shot.png"; fs.write_bytes(b"s")
    fc = tmp_path / "shot.mp4"; fc.write_bytes(b"c")

    d_story = register_production_output(production=prod, file_path=str(fs), category="storyboard")
    d_clip = register_production_output(production=prod, file_path=str(fc), category="clips")
    assert d_story.folder_id != d_clip.folder_id
