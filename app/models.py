from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class FamilySettings:
    people_count: int | None = None
    adults_count: int | None = None
    children_count: int | None = None
    child_age: str | None = None
    child_ages: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    excluded_products: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DishOption:
    number: int
    title: str
    time: str
    meal_type: str
    child_fit: str
    short_reason: str
    simplicity_score: int = 50

    @classmethod
    def from_dict(cls, data: dict[str, Any], number: int) -> "DishOption":
        return cls(
            number=number,
            title=str(data.get("title") or f"Вариант {number}").strip(),
            time=str(data.get("time") or "20 минут").strip(),
            meal_type=str(data.get("meal_type") or "ужин").strip(),
            child_fit=str(data.get("child_fit") or "с адаптацией").strip(),
            short_reason=str(data.get("short_reason") or "Простое домашнее блюдо.").strip(),
            simplicity_score=int(data.get("simplicity_score") or 50),
        )


@dataclass(slots=True)
class Recipe:
    title: str
    time: str
    portions: str
    ingredients: list[str]
    steps: list[str]
    child_note: str
    important_note: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Recipe":
        return cls(
            title=str(data.get("title") or "Рецепт").strip(),
            time=str(data.get("time") or "20 минут").strip(),
            portions=str(data.get("portions") or "семейная порция").strip(),
            ingredients=[str(item).strip() for item in data.get("ingredients", []) if str(item).strip()],
            steps=[str(item).strip() for item in data.get("steps", []) if str(item).strip()],
            child_note=str(
                data.get("child_note")
                or "Для ребенка сделайте вкус мягче и не добавляйте острые специи."
            ).strip(),
            important_note=str(
                data.get("important_note")
                or "Если есть аллергии или исключенные продукты, не используйте их."
            ).strip(),
        )
