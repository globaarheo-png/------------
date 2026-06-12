from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.models import DishOption, FamilySettings, Recipe

try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover - postgrest is installed with supabase
    APIError = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


class SupabaseStorage:
    def __init__(self, settings: Settings) -> None:
        self.client: Client | None = None
        if settings.supabase_url and settings.supabase_key:
            try:
                from supabase import create_client
            except ImportError:
                logger.warning(
                    "Supabase package is not installed. Persistent storage is disabled."
                )
                return
            self.client = create_client(settings.supabase_url, settings.supabase_key)

    @property
    def enabled(self) -> bool:
        return self.client is not None

    async def upsert_user(self, user_id: int, name: str | None) -> None:
        if not self.client:
            return
        self._execute(
            self.client.table("users").upsert(
                {
                    "id": user_id,
                    "name": name,
                    "first_started_at": self._now(),
                },
                on_conflict="id",
            )
        )

    async def get_family_settings(self, user_id: int) -> FamilySettings:
        if not self.client:
            return FamilySettings()
        response = self._execute(
            self.client.table("family_settings").select("*").eq("user_id", user_id).maybe_single()
        )
        if response is None:
            return FamilySettings()
        data = response.data
        if not data:
            return FamilySettings()
        return FamilySettings(
            people_count=data.get("people_count"),
            adults_count=data.get("adults_count"),
            children_count=data.get("children_count"),
            child_age=data.get("child_age"),
            child_ages=data.get("child_ages") or [],
            allergies=data.get("allergies") or [],
            excluded_products=data.get("excluded_products") or [],
        )

    async def save_family_settings(self, user_id: int, settings: FamilySettings) -> None:
        if not self.client:
            return
        self._execute(
            self.client.table("family_settings").upsert(
                {
                    "user_id": user_id,
                    "people_count": settings.people_count,
                    "adults_count": settings.adults_count,
                    "children_count": settings.children_count,
                    "child_age": settings.child_age,
                    "child_ages": settings.child_ages,
                    "allergies": settings.allergies,
                    "excluded_products": settings.excluded_products,
                    "updated_at": self._now(),
                },
                on_conflict="user_id",
            )
        )

    async def create_food_request(
        self,
        user_id: int,
        raw_text: str,
        options: list[DishOption],
    ) -> int | None:
        if not self.client:
            return None
        response = self._execute(
            self.client.table("food_requests")
            .insert(
                {
                    "user_id": user_id,
                    "raw_text": raw_text,
                    "options_json": [asdict(option) for option in options],
                    "status": "options_shown",
                }
            )
        )
        if response is None:
            return None
        rows = response.data or []
        return rows[0]["id"] if rows else None

    async def update_selected_recipe(
        self,
        request_id: int | None,
        selected_number: int,
        recipe: Recipe,
    ) -> None:
        if not self.client or request_id is None:
            return
        self._execute(
            self.client.table("food_requests")
            .update(
                {
                    "selected_option_number": selected_number,
                    "selected_recipe_text": self.recipe_to_text(recipe),
                    "child_note": recipe.child_note,
                    "status": "recipe_selected",
                    "updated_at": self._now(),
                }
            )
            .eq("id", request_id)
        )

    async def add_favorite(self, user_id: int, request_id: int | None, recipe: Recipe) -> None:
        if not self.client:
            return
        self._execute(
            self.client.table("favorites").insert(
                {
                    "user_id": user_id,
                    "food_request_id": request_id,
                    "title": recipe.title,
                    "recipe": self.recipe_to_text(recipe),
                    "child_note": recipe.child_note,
                }
            )
        )
        if request_id is not None:
            self._execute(
                self.client.table("food_requests")
                .update({"status": "favorite_added", "updated_at": self._now()})
                .eq("id", request_id)
            )

    async def list_favorites(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        if not self.client:
            return []
        response = self._execute(
            self.client.table("favorites")
            .select("title, recipe, child_note, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if response is None:
            return []
        return response.data or []

    async def list_history(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        if not self.client:
            return []
        response = self._execute(
            self.client.table("food_requests")
            .select("raw_text, options_json, status, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if response is None:
            return []
        return response.data or []

    def _execute(self, query: Any) -> Any | None:
        try:
            return query.execute()
        except Exception as error:
            if self._is_missing_schema_error(error):
                logger.error(
                    "Supabase tables are missing. Run supabase/schema.sql in the "
                    "Supabase SQL editor. Persistent storage is disabled for this run."
                )
                self.client = None
                return None
            raise

    @staticmethod
    def _is_missing_schema_error(error: Exception) -> bool:
        if APIError is not None and not isinstance(error, APIError):
            return False
        return "PGRST205" in str(error)

    @staticmethod
    def recipe_to_text(recipe: Recipe) -> str:
        ingredients = "\n".join(f"- {item}" for item in recipe.ingredients)
        steps = "\n".join(f"{index}. {step}" for index, step in enumerate(recipe.steps, start=1))
        return (
            f"{recipe.title}\n"
            f"Время: {recipe.time}\n"
            f"Порции: {recipe.portions}\n\n"
            f"Нужно:\n{ingredients}\n\n"
            f"Как приготовить:\n{steps}\n\n"
            f"Для ребенка: {recipe.child_note}\n"
            f"Важно: {recipe.important_note}"
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
