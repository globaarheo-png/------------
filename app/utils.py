from __future__ import annotations

import re

from app.models import DishOption, FamilySettings


NUMBER_WORDS = {
    "один": 1,
    "одна": 1,
    "одно": 1,
    "одного": 1,
    "один ребенок": 1,
    "два": 2,
    "две": 2,
    "двое": 2,
    "двух": 2,
    "три": 3,
    "трое": 3,
    "трех": 3,
    "четыре": 4,
    "четверо": 4,
    "четырех": 4,
    "пять": 5,
    "пятеро": 5,
    "пяти": 5,
}


PRODUCT_HINTS = {
    "курица", "мясо", "фарш", "рыба", "индейка", "говядина", "свинина",
    "яйцо", "яйца", "творог", "сыр", "молоко", "кефир", "йогурт",
    "гречка", "рис", "макароны", "паста", "картошка", "картофель",
    "морковь", "лук", "капуста", "кабачок", "помидор", "огурец",
    "овощи", "крупа", "овсянка", "хлеб", "лаваш", "фасоль",
    "чечевица", "горох", "тыква", "сметана", "сливки", "масло",
}


def looks_like_food_request(text: str) -> bool:
    normalized = text.lower()
    if len(normalized.split()) >= 5 and ("," in normalized or ";" in normalized):
        return True
    return any(hint in normalized for hint in PRODUCT_HINTS)


def has_few_products(text: str) -> bool:
    rough_items = re.split(r"[,;\n]+|\s+и\s+", text.lower())
    products = [item.strip() for item in rough_items if len(item.strip()) > 2]
    return len(products) <= 2


def parse_settings_text(text: str) -> FamilySettings:
    lowered = text.lower()
    people_count = None
    adults_count = _extract_count_before(lowered, r"взросл\w*")
    children_count = _extract_count_before(
        lowered,
        r"(?:реб[её]н(?:ок|ка|ку|ком)?|дет(?:и|ей|ям|ьми)?|малыш\w*|доч\w*|сын\w*)",
    )

    people_match = re.search(r"(\d+)\s*(?:человек|чел|порц)", lowered)
    if people_match:
        people_count = int(people_match.group(1))
    elif adults_count is not None or children_count is not None:
        people_count = (adults_count or 0) + (children_count or 0)
    if adults_count is None and people_count is not None and children_count is not None:
        adults_count = max(people_count - children_count, 0)

    child_ages = _extract_child_ages(lowered)
    child_age = ", ".join(child_ages) if child_ages else None

    allergies = _extract_list_after(text, ["аллергии", "аллергия"])
    excluded = _extract_list_after(text, ["исключить", "исключи", "нельзя", "без"])
    return FamilySettings(
        people_count=people_count,
        adults_count=adults_count,
        children_count=children_count,
        child_age=child_age,
        child_ages=child_ages,
        allergies=allergies,
        excluded_products=excluded,
    )


def find_excluded_in_text(text: str, settings: FamilySettings) -> list[str]:
    lowered = text.lower()
    excluded = settings.allergies + settings.excluded_products
    return [item for item in excluded if item.lower() and item.lower() in lowered]


def family_portions_text(settings: FamilySettings) -> str | None:
    if settings.adults_count is not None and settings.children_count is not None:
        return (
            f"{settings.adults_count} {_plural(settings.adults_count, 'взрослый', 'взрослых', 'взрослых')} "
            f"и {settings.children_count} {_plural(settings.children_count, 'ребенок', 'ребенка', 'детей')}"
        )
    if settings.people_count is not None:
        return f"{settings.people_count} {_plural(settings.people_count, 'человек', 'человека', 'человек')}"
    if settings.adults_count is not None:
        return f"{settings.adults_count} {_plural(settings.adults_count, 'взрослый', 'взрослых', 'взрослых')}"
    if settings.children_count is not None:
        return f"{settings.children_count} {_plural(settings.children_count, 'ребенок', 'ребенка', 'детей')}"
    return None


def choose_simplest(options: list[DishOption]) -> DishOption:
    return sorted(options, key=lambda option: (option.simplicity_score, _minutes(option.time)))[0]


def _plural(value: int, one: str, few: str, many: str) -> str:
    if 11 <= value % 100 <= 14:
        return many
    if value % 10 == 1:
        return one
    if 2 <= value % 10 <= 4:
        return few
    return many


def _minutes(text: str) -> int:
    match = re.search(r"\d+", text)
    return int(match.group(0)) if match else 999


def _extract_list_after(text: str, markers: list[str]) -> list[str]:
    pattern = "|".join(re.escape(marker) for marker in markers)
    match = re.search(rf"(?:{pattern})\s*:?\s*([^.;\n]+)", text, flags=re.IGNORECASE)
    if not match:
        return []
    raw = match.group(1)
    return [item.strip(" ,") for item in re.split(r",| и ", raw) if item.strip(" ,")]


def _extract_count_before(text: str, noun_pattern: str) -> int | None:
    number_pattern = r"\d+|" + "|".join(re.escape(word) for word in NUMBER_WORDS)
    match = re.search(rf"\b({number_pattern})\s+{noun_pattern}\b", text, flags=re.IGNORECASE)
    if not match:
        return None
    value = match.group(1).lower()
    if value.isdigit():
        return int(value)
    return NUMBER_WORDS.get(value)


def _extract_child_ages(text: str) -> list[str]:
    ages: list[str] = []
    for match in re.finditer(
        r"(?:\d+\s*)?(?:реб[её]н(?:ок|ка|ку|ком)?|малыш\w*|доч\w*|сын\w*)[^\d\n.;,]{0,20}"
        r"(\d+\s*(?:месяц(?:ев|а)?|мес|года|год|лет))",
        text,
        flags=re.IGNORECASE,
    ):
        age = match.group(1).strip()
        if age not in ages:
            ages.append(age)
    return ages
