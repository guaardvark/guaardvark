# backend/utils/pydantic_models.py
# Version 1.0: Defines Pydantic models for structured LLM output.

from typing import List

from pydantic import BaseModel, Field


class SeoArticle(BaseModel):
    """Data model for a single SEO-optimized article."""

    title: str = Field(description="The main title of the article.")
    content: str = Field(description="The full HTML content of the article body.")
    meta_description: str = Field(
        description="The meta description for SEO purposes, between 155-160 characters."
    )
    slug: str = Field(
        description="The URL-friendly slug for the article, based on the title."
    )
    tags: List[str] = Field(
        description="A list of 3-5 relevant tags or keywords for the article."
    )


PYDANTIC_MODELS = {
    "SeoArticle": SeoArticle,
}


def get_pydantic_model_by_name(name: str):
    """Retrieves a Pydantic model class from the registry by its string name."""

    return PYDANTIC_MODELS.get(name)
