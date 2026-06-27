from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import Settings
from app.models import DishOption, FamilySettings, Recipe

try:
    from postgrest.exceptions import APIError
except ImportError:  # pragma: no cover - postgrest is installed with supabase
    APIError = None  # type: ignore[assignment]

try:
    from httpx import TransportError
except ImportError:  # pragma: no cover - httpx is installed with supabase
    TransportError = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from supabase import Client

logger = logging.getLogger(__name__)


class SupabaseStorage:
    def __init__(self, settings: Settings) -> None:
        self.client: Client | None = None
        self.local_favorites_path = Path("data") / "favorites.json"
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

    async def add_favorite(self, user_id: int, request_id: int | None, recipe: Recipe) -> bool:
        if not self.client:
            return self._add_local_favorite(user_id, request_id, recipe.title, self.recipe_to_text(recipe), recipe.child_note)
        response = self._execute(
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
        if response is None:
            return self._add_local_favorite(user_id, request_id, recipe.title, self.recipe_to_text(recipe), recipe.child_note)
        if request_id is not None:
            self._execute(
                self.client.table("food_requests")
                .update({"status": "favorite_added", "updated_at": self._now()})
                .eq("id", request_id)
            )
        return True

    async def add_favorite_from_request(self, user_id: int, request_id: int | None) -> bool:
        if not self.client:
            return False

        query = (
            self.client.table("food_requests")
            .select("id, selected_recipe_text, child_note")
            .eq("user_id", user_id)
            .not_.is_("selected_recipe_text", "null")
            .order("created_at", desc=True)
            .limit(1)
        )
        if request_id is not None:
            query = query.eq("id", request_id)

        response = self._execute(query)
        if response is None:
            return False
        rows = response.data or []
        if not rows:
            return False

        request = rows[0]
        recipe_text = (request.get("selected_recipe_text") or "").strip()
        if not recipe_text:
            return False

        response = self._execute(
            self.client.table("favorites").insert(
                {
                    "user_id": user_id,
                    "food_request_id": request.get("id"),
                    "title": recipe_text.splitlines()[0][:120] or "Рецепт",
                    "recipe": recipe_text,
                    "child_note": request.get("child_note"),
                }
            )
        )
        if response is None:
            return False

        self._execute(
            self.client.table("food_requests")
            .update({"status": "favorite_added", "updated_at": self._now()})
            .eq("id", request.get("id"))
        )
        return True

    async def list_favorites(self, user_id: int, limit: int = 10) -> list[dict[str, Any]]:
        if not self.client:
            return self._list_local_favorites(user_id, limit)
        response = self._execute(
            self.client.table("favorites")
            .select("title, recipe, child_note, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
        )
        if response is None:
            return self._list_local_favorites(user_id, limit)
        local_favorites = self._list_local_favorites(user_id, limit)
        favorites = (response.data or []) + local_favorites
        return sorted(
            favorites,
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )[:limit]

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
            if self._is_connection_error(error):
                logger.warning(
                    "Supabase is unavailable (%s). Persistent storage is disabled "
                    "for this run.",
                    error,
                )
                self.client = None
                return None
            if self._is_api_error(error):
                logger.warning(
                    "Supabase API request failed (%s). Falling back to local storage "
                    "for this run.",
                    error,
                )
                self.client = None
                return None
            raise

    @staticmethod
    def _is_missing_schema_error(error: Exception) -> bool:
        if APIError is not None and not isinstance(error, APIError):
            return False
        return any(code in str(error) for code in ("PGRST204", "PGRST205"))

    @staticmethod
    def _is_connection_error(error: Exception) -> bool:
        if TransportError is not None and isinstance(error, TransportError):
            return True
        return isinstance(error, OSError)

    @staticmethod
    def _is_api_error(error: Exception) -> bool:
        return APIError is not None and isinstance(error, APIError)

    def _add_local_favorite(
        self,
        user_id: int,
        request_id: int | None,
        title: str,
        recipe_text: str,
        child_note: str | None,
    ) -> bool:
        try:
            favorites = self._read_local_favorites()
            favorites.append(
                {
                    "user_id": user_id,
                    "food_request_id": request_id,
                    "title": title,
                    "recipe": recipe_text,
                    "child_note": child_note,
                    "created_at": self._now(),
                }
            )
            self.local_favorites_path.parent.mkdir(parents=True, exist_ok=True)
            self.local_favorites_path.write_text(
                json.dumps(favorites, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception:
            logger.exception("Failed to save favorite to local fallback storage")
            return False

    def _list_local_favorites(self, user_id: int, limit: int) -> list[dict[str, Any]]:
        favorites = [
            item
            for item in self._read_local_favorites()
            if item.get("user_id") == user_id
        ]
        return sorted(
            favorites,
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )[:limit]

    def _read_local_favorites(self) -> list[dict[str, Any]]:
        if not self.local_favorites_path.exists():
            return []
        try:
            data = json.loads(self.local_favorites_path.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to read local favorites")
            return []
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]

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
