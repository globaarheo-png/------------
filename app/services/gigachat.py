from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx

from app.config import Settings
from app.models import DishOption, FamilySettings, Recipe


logger = logging.getLogger(__name__)
GIGACHAT_AUTH_KEY_ENV = "GIGACHAT_AUTH_KEY"


class GigaChatError(RuntimeError):
    pass


class GigaChatClient:
    auth_url = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    chat_url = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._token: str | None = None
        self._log_auth_key_check()

    async def generate_options(
        self,
        user_text: str,
        family_settings: FamilySettings,
        excluded_warning: list[str],
    ) -> list[DishOption]:
        payload = await self._chat_json(
            self._options_prompt(user_text, family_settings, excluded_warning)
        )
        options = payload.get("options", [])
        if not isinstance(options, list) or len(options) < 3:
            raise GigaChatError("GigaChat returned fewer than 3 options")
        return [DishOption.from_dict(item, index) for index, item in enumerate(options[:3], start=1)]

    async def generate_recipe(
        self,
        user_text: str,
        option: DishOption,
        family_settings: FamilySettings,
    ) -> Recipe:
        payload = await self._chat_json(self._recipe_prompt(user_text, option, family_settings))
        return Recipe.from_dict(payload)

    async def _chat_json(self, prompt: str) -> dict[str, Any]:
        content = await self._chat_text(
            [
                {
                    "role": "system",
                    "content": (
                        "Ты помощник для подбора простых домашних блюд для семьи. "
                        "Отвечай только валидным JSON. Не добавляй Markdown, пояснения и текст вокруг JSON."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
        )

        try:
            return self._parse_json(content)
        except GigaChatError:
            logger.warning("GigaChat returned non-JSON content: %r", content[:1000])

        repaired = await self._chat_text(
            [
                {
                    "role": "system",
                    "content": "Ты исправляешь ответы в строгий валидный JSON без Markdown и пояснений.",
                },
                {
                    "role": "user",
                    "content": (
                        "Преобразуй этот ответ в валидный JSON. "
                        "Верни только JSON-объект, без текста вокруг:\n\n"
                        f"{content}"
                    ),
                },
            ],
            temperature=0.0,
        )
        return self._parse_json(repaired)

    async def _chat_text(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        if not self.settings.gigachat_auth_key:
            logger.error("%s is not configured in environment variables", GIGACHAT_AUTH_KEY_ENV)
            raise GigaChatError("GigaChat credentials are not configured")

        token = await self._access_token()
        request = {
            "model": self.settings.gigachat_model,
            "messages": messages,
            "temperature": temperature,
        }
        async with httpx.AsyncClient(verify=self.settings.gigachat_verify_ssl, timeout=60) as client:
            response = await client.post(
                self.chat_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=request,
            )
            self._raise_for_status(response, "GigaChat chat completion request")
            data = response.json()

        return str(data["choices"][0]["message"]["content"])

    async def _access_token(self) -> str:
        if self._token:
            return self._token

        async with httpx.AsyncClient(verify=self.settings.gigachat_verify_ssl, timeout=30) as client:
            response = await client.post(
                self.auth_url,
                headers={
                    "Authorization": self._authorization_header(self.settings.gigachat_auth_key),
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": self.settings.gigachat_scope},
            )
            self._raise_for_status(response, "GigaChat auth request")
            self._token = response.json()["access_token"]
            return self._token

    @staticmethod
    def _raise_for_status(response: httpx.Response, context: str) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if context == "GigaChat auth request" and response.status_code == 401:
                logger.exception(
                    "%s rejected Authorization Key: status=%s url=%s. "
                    "Check %s in .env; it must be the GigaChat Authorization Key, "
                    "not an access token.",
                    context,
                    response.status_code,
                    response.url,
                    GIGACHAT_AUTH_KEY_ENV,
                )
                raise GigaChatError("GigaChat authorization failed") from exc

            logger.exception(
                "%s failed: status=%s url=%s response_body=%r",
                context,
                response.status_code,
                response.url,
                response.text[:2000],
            )
            raise GigaChatError(f"{context} failed with HTTP {response.status_code}") from exc

    def _log_auth_key_check(self) -> None:
        auth_key = self.settings.gigachat_auth_key.strip()
        logger.info(
            "GigaChat auth config: variable=%s found=%s length_gt_20=%s scope=%s",
            GIGACHAT_AUTH_KEY_ENV,
            bool(auth_key),
            len(auth_key) > 20,
            self.settings.gigachat_scope,
        )

    @staticmethod
    def _authorization_header(auth_key: str) -> str:
        auth_key = auth_key.strip()
        if auth_key.lower().startswith("basic "):
            return auth_key
        return f"Basic {auth_key}"

    @classmethod
    def _parse_json(cls, content: str) -> dict[str, Any]:
        candidates = cls._json_candidates(content)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                return parsed
        raise GigaChatError("GigaChat returned invalid JSON")

    @staticmethod
    def _json_candidates(content: str) -> list[str]:
        cleaned = content.strip()
        candidates = [cleaned]

        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(block.strip() for block in fenced_blocks)

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(cleaned[start : end + 1])

        # Preserve order, drop duplicates and empty strings.
        unique: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate and candidate not in seen:
                unique.append(candidate)
                seen.add(candidate)
        return unique

    @staticmethod
    def _settings_text(settings: FamilySettings) -> str:
        return (
            f"Количество людей: {settings.people_count or 'не указано'}; "
            f"взрослых: {settings.adults_count or 'не указано'}; "
            f"детей: {settings.children_count or 'не указано'}; "
            f"возрасты детей: {', '.join(settings.child_ages) or settings.child_age or 'не указаны'}; "
            f"аллергии: {', '.join(settings.allergies) or 'нет'}; "
            f"исключить: {', '.join(settings.excluded_products) or 'нет'}."
        )

    def _options_prompt(
        self,
        user_text: str,
        settings: FamilySettings,
        excluded_warning: list[str],
    ) -> str:
        excluded_line = ", ".join(excluded_warning) or "нет"
        return f"""
Пользователь написал:
{user_text}

Настройки семьи:
{self._settings_text(settings)}

Продукты из запроса, которые нельзя использовать: {excluded_line}

Сгенерируй 3 коротких варианта блюд из разрешенных продуктов.
Если запрещенный продукт есть в запросе, не используй его.
Блюда должны быть простыми, домашними, на 10-30 минут.
Оценка для ребенка бытовая: блюдо не острое, мягкое, простое, без явно неподходящих продуктов.
Не давай медицинских рекомендаций.

Верни строго JSON-объект по этой схеме:
{{
  "options": [
    {{
      "title": "Название блюда",
      "time": "15 минут",
      "meal_type": "ужин",
      "child_fit": "да",
      "short_reason": "1 короткая фраза, почему вариант подходит",
      "simplicity_score": 1
    }}
  ]
}}
В массиве options должно быть ровно 3 объекта. Чем проще блюдо, тем меньше simplicity_score.
"""

    def _recipe_prompt(self, user_text: str, option: DishOption, settings: FamilySettings) -> str:
        return f"""
Пользователь написал:
{user_text}

Настройки семьи:
{self._settings_text(settings)}

Выбранное блюдо:
{option.title}

Составь подробный, но короткий рецепт выбранного блюда.
Не используй аллергии и исключенные продукты из настроек.
Шагов приготовления должно быть 3-6.
Добавь бытовую адаптацию для ребенка.
Не давай медицинских рекомендаций.

Верни строго JSON-объект по этой схеме:
{{
  "title": "Название блюда",
  "time": "20 минут",
  "portions": "2 взрослых и ребенок",
  "ingredients": ["продукт 1", "продукт 2"],
  "steps": ["шаг 1", "шаг 2", "шаг 3"],
  "child_note": "короткая бытовая адаптация",
  "important_note": "если есть аллергии или исключенные продукты, не использовать их"
}}
"""
