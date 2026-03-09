from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class ClientResponse(BaseModel):
    id: int
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    location: Optional[str] = None
    logo_path: Optional[str] = None
    notes: Optional[str] = None
    project_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    # RAG Enhancement fields (updated to support arrays)
    industry: Optional[list] = None
    target_audience: Optional[list] = None
    unique_selling_points: Optional[list] = None
    competitor_urls: Optional[list] = None
    brand_voice_examples: Optional[str] = None
    keywords: Optional[list] = None
    content_goals: Optional[list] = None
    regulatory_constraints: Optional[str] = None
    geographic_coverage: Optional[list] = None


class SimpleClientInfo(BaseModel):
    id: int
    name: str
    logo_path: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    client_id: Optional[int] = None
    name: str
    description: Optional[str] = None
    client: Optional[SimpleClientInfo] = None
    website_count: int
    document_count: int
    task_count: int
    rule_count: int
    primary_rule_count: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
