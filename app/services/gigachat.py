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
GIGACHAT_SCOPE = "GIGACHAT_API_PERS"
GIGACHAT_DEFAULT_MODEL = "GigaChat"


def _clean_json_text(content: str) -> str:
    cleaned = content.strip().lstrip("\ufeff")
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _repair_json_text(content: str) -> str:
    repaired = _clean_json_text(content)
    repaired = re.sub(r",(\s*[}\]])", r"\1", repaired)
    return repaired.strip()


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
            parsed = self._parse_json(content)
        except GigaChatError:
            logger.info("GigaChat json parse failed: yes")
            logger.warning("GigaChat returned non-JSON content: %r", content[:1000])
        else:
            logger.info("GigaChat json parse failed: no")
            logger.info("GigaChat retry after invalid json: no")
            return parsed

        logger.info("GigaChat retry after invalid json: yes")
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
        try:
            parsed = self._parse_json(repaired)
        except GigaChatError:
            logger.info("GigaChat json parse failed: yes")
            logger.info("GigaChat retry after invalid json: no")
            raise
        logger.info("GigaChat json parse failed: no")
        return parsed

    async def _chat_text(self, messages: list[dict[str, str]], temperature: float = 0.2) -> str:
        if not self.settings.gigachat_auth_key:
            logger.error("%s is not configured in environment variables", GIGACHAT_AUTH_KEY_ENV)
            raise GigaChatError("GigaChat credentials are not configured")

        request = {
            "model": self._chat_model(),
            "messages": messages,
            "temperature": temperature,
        }
        logger.info(
            "GigaChat chat request config: endpoint=%s model=%s",
            self.chat_url,
            request["model"],
        )
        async with httpx.AsyncClient(verify=self.settings.gigachat_verify_ssl, timeout=60) as client:
            token = await self._access_token()
            response = await self._post_chat_completion(client, request, token)
            logger.info("GigaChat chat status code: %s", response.status_code)
            token_expired = self._is_token_expired_response(response)
            logger.info("GigaChat token expired detected: %s", self._yes_no(token_expired))
            if token_expired:
                logger.warning(
                    "GigaChat chat completion token expired: endpoint=%s model=%s. "
                    "Resetting cached access token and retrying once.",
                    self.chat_url,
                    request["model"],
                )
                self._token = None
                logger.info("GigaChat retry after token refresh: yes")
                try:
                    token = await self._access_token()
                except Exception:
                    logger.info("GigaChat access token refreshed: no")
                    raise
                else:
                    logger.info("GigaChat access token refreshed: yes")
                response = await self._post_chat_completion(client, request, token)
                logger.info("GigaChat chat status code: %s", response.status_code)
                logger.info(
                    "GigaChat token expired detected: %s",
                    self._yes_no(self._is_token_expired_response(response)),
                )
            else:
                logger.info("GigaChat access token refreshed: no")
                logger.info("GigaChat retry after token refresh: no")
            self._raise_for_status(response, "GigaChat chat completion request")
            data = response.json()

        return str(data["choices"][0]["message"]["content"])

    async def _post_chat_completion(
        self,
        client: httpx.AsyncClient,
        request: dict[str, Any],
        access_token: str,
    ) -> httpx.Response:
        token = access_token.strip()
        if not token:
            logger.error("GigaChat access token is empty before chat request")
            raise GigaChatError("GigaChat authorization failed")

        authorization = self._chat_authorization_header(token)
        logger.info(
            "GigaChat chat Authorization header starts with Bearer: %s",
            self._yes_no(authorization.startswith("Bearer ")),
        )
        logger.info(
            "GigaChat chat Authorization header uses Authorization Key: %s",
            self._yes_no(
                authorization == self._oauth_authorization_header(self.settings.gigachat_auth_key)
            ),
        )
        return await client.post(
            self.chat_url,
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
            json=request,
        )

    async def _access_token(self) -> str:
        if self._token:
            return self._token

        scope = self._oauth_scope()
        auth_key = self.settings.gigachat_auth_key.strip()
        logger.info(
            "GigaChat OAuth request config: endpoint=%s scope=%s",
            self.auth_url,
            scope,
        )
        async with httpx.AsyncClient(verify=self.settings.gigachat_verify_ssl, timeout=30) as client:
            response = await client.post(
                self.auth_url,
                headers={
                    "Authorization": self._oauth_authorization_header(auth_key),
                    "RqUID": str(uuid.uuid4()),
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data={"scope": scope},
            )
            logger.info("GigaChat OAuth status code: %s", response.status_code)
            self._raise_for_status(response, "GigaChat auth request")
            token = response.json().get("access_token")
            token_received = isinstance(token, str) and bool(token.strip())
            logger.info("GigaChat access_token received: %s", self._yes_no(token_received))
            if not isinstance(token, str) or not token.strip():
                logger.error("GigaChat OAuth response did not include an access token")
                raise GigaChatError("GigaChat authorization failed")
            self._token = token.strip()
            logger.info("GigaChat access_token length: %s", len(self._token))
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
                "%s failed: status=%s url=%s",
                context,
                response.status_code,
                response.url,
            )
            raise GigaChatError(f"{context} failed with HTTP {response.status_code}") from exc

    def _log_auth_key_check(self) -> None:
        auth_key = self.settings.gigachat_auth_key.strip()
        logger.info(
            "GigaChat env variable exists: %s",
            self._yes_no(bool(auth_key)),
        )
        logger.info("GigaChat Authorization key length: %s", len(auth_key))
        logger.info(
            "GigaChat auth config: variable=%s configured_scope=%s effective_scope=%s model=%s",
            GIGACHAT_AUTH_KEY_ENV,
            self.settings.gigachat_scope,
            self._oauth_scope(),
            self._chat_model(),
        )

    @staticmethod
    def _oauth_authorization_header(auth_key: str) -> str:
        auth_key = auth_key.strip()
        if auth_key.lower().startswith("basic "):
            return auth_key
        return f"Basic {auth_key}"

    @staticmethod
    def _chat_authorization_header(access_token: str) -> str:
        return f"Bearer {access_token.strip()}"

    def _oauth_scope(self) -> str:
        configured_scope = self.settings.gigachat_scope.strip()
        if configured_scope and configured_scope != GIGACHAT_SCOPE:
            logger.warning(
                "Ignoring configured GigaChat scope %s; using required scope %s",
                configured_scope,
                GIGACHAT_SCOPE,
            )
        return GIGACHAT_SCOPE

    def _chat_model(self) -> str:
        model = self.settings.gigachat_model.strip()
        if not model:
            logger.warning(
                "GigaChat model is empty; using default model %s",
                GIGACHAT_DEFAULT_MODEL,
            )
            return GIGACHAT_DEFAULT_MODEL
        return model

    @staticmethod
    def _yes_no(value: bool) -> str:
        return "yes" if value else "no"

    @staticmethod
    def _is_token_expired_response(response: httpx.Response) -> bool:
        if response.status_code == 401:
            return True
        return "token has expired" in response.text.lower()

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
        cleaned = _clean_json_text(content)
        candidates = [cleaned]

        fenced_blocks = re.findall(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.IGNORECASE | re.DOTALL)
        candidates.extend(_clean_json_text(block) for block in fenced_blocks)

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(_clean_json_text(cleaned[start : end + 1]))

        candidates.extend(_repair_json_text(candidate) for candidate in list(candidates))

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
