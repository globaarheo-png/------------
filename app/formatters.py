from __future__ import annotations

from html import escape

from app.models import DishOption, Recipe


def format_options(options: list[DishOption], warning: str | None = None) -> str:
    lines: list[str] = []
    if warning:
        lines.append(escape(warning))
        lines.append("")

    for option in options:
        lines.append(
            f"<b>{option.number}. {escape(option.title)}</b> - {escape(option.time)} · "
            f"{escape(option.meal_type)} · ребенку: {escape(option.child_fit)}"
        )
        lines.append(f"Коротко: {escape(option.short_reason)}")
        lines.append("")

    lines.append("Выбери вариант кнопкой или напиши 1, 2 или 3.")
    return "\n".join(lines).strip()


def format_recipe(recipe: Recipe) -> str:
    ingredients = "\n".join(f"- {escape(item)}" for item in recipe.ingredients)
    if not ingredients:
        ingredients = "- по списку из запроса"
    steps = "\n".join(f"{index}. {escape(step)}" for index, step in enumerate(recipe.steps, start=1))

    return (
        f"<b>{escape(recipe.title)}</b>\n"
        f"Время: {escape(recipe.time)}\n"
        f"Порции: {escape(recipe.portions)}\n\n"
        f"<b>Нужно:</b>\n{ingredients}\n\n"
        f"<b>Как приготовить:</b>\n{steps}\n\n"
        f"<b>Для ребенка:</b> {escape(recipe.child_note)}\n\n"
        f"<b>Важно:</b> {escape(recipe.important_note)}"
    )
