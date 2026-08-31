"""Models on the core db. Foreign keys reach core tables by name; core models
never reference an extension's, so an extension can be removed cleanly."""
from datetime import datetime

from backend.models import db


class TemplateNote(db.Model):
    __tablename__ = "template_notes"

    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    document_id = db.Column(db.Integer, db.ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(), nullable=False)
