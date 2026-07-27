"""One way to ask a model a question, and to know what it cost.

Every vendor worth considering speaks the OpenAI chat-completions dialect, so
this is a thin client over `requests` rather than a vendor SDK: no new
dependency, and swapping providers is a line in providers.yaml.

Two things it insists on. Cost is metered against a ceiling declared before
the run, and checked before each call rather than tallied after it, because a
loop over forty thousand employers is not something to discover the price of
afterwards. And a reply that is not valid JSON of the expected shape is a
failed call, retried once and then abandoned: a queue that silently accepts
half-parsed answers is worse than one that stops.

DeepSeek offers `response_format=json_object` but no schema enforcement, so
the shape check happens here. It is deliberately shallow — required keys and
their types — because the real gate on any answer is downstream, where the
proposal has to survive the matcher or the resolver.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests
import yaml

logger = logging.getLogger("warnlive")

PROVIDERS_PATH = Path(__file__).with_name("providers.yaml")

PROVIDER_ENV = "WARNLIVE_LLM_PROVIDER"
MODEL_ENV = "WARNLIVE_LLM_MODEL"

TIMEOUT = 300
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4
# Tries at getting one usable JSON object, counting room raised for a
# truncated answer as well as a reply that would not parse.
JSON_ATTEMPTS = 3
# Where raising the ceiling stops. Past here the batch is the problem.
MAX_OUTPUT_TOKENS = 16384


class BudgetExceeded(RuntimeError):
    """Raised before a call that would spend past the declared ceiling."""


class ModelRefused(RuntimeError):
    """The model answered, but not with the JSON shape that was asked for."""


class Truncated(RuntimeError):
    """The reply hit max_tokens and stopped mid-answer."""


@dataclass(frozen=True)
class Model:
    """A resolved (provider, model) pair, with everything needed to call it."""

    provider: str
    alias: str
    slug: str
    base_url: str
    api_key_env: str
    input_miss_per_m: float | None = None
    input_hit_per_m: float | None = None
    output_per_m: float | None = None

    @property
    def priced(self) -> bool:
        return None not in (self.input_miss_per_m, self.output_per_m)

    def __str__(self) -> str:
        return f"{self.provider}/{self.slug}"


def load_config(path: Path = PROVIDERS_PATH) -> dict:
    with open(path) as fh:
        return yaml.safe_load(fh)


def resolve(
    provider: str | None = None,
    model: str | None = None,
    path: Path = PROVIDERS_PATH,
) -> Model:
    """Which model to call: explicit argument, then environment, then file.

    The alias ("flash") is what commands and tests name; the slug
    ("deepseek-v4-flash") is what goes on the wire. Keeping them apart means
    a vendor renaming a model is a config edit, and that a decision recorded
    in the ledger names the exact slug that produced it.
    """
    cfg = load_config(path)
    name = provider or os.environ.get(PROVIDER_ENV) or cfg["default_provider"]
    alias = model or os.environ.get(MODEL_ENV) or cfg["default_model"]

    providers = cfg.get("providers") or {}
    if name not in providers:
        raise KeyError(
            f"unknown provider {name!r}; providers.yaml has "
            f"{', '.join(sorted(providers))}"
        )
    entry = providers[name]
    models = entry.get("models") or {}
    if alias not in models:
        raise KeyError(
            f"provider {name!r} has no model {alias!r}; it has "
            f"{', '.join(sorted(models))}"
        )
    spec = models[alias]
    return Model(
        provider=name,
        alias=alias,
        slug=spec["slug"],
        base_url=entry["base_url"].rstrip("/"),
        api_key_env=entry["api_key_env"],
        input_miss_per_m=spec.get("input_miss_per_m"),
        input_hit_per_m=spec.get("input_hit_per_m"),
        output_per_m=spec.get("output_per_m"),
    )


@dataclass
class Usage:
    """What a run has consumed. Cost is None while any call was unpriced."""

    calls: int = 0
    input_hit: int = 0
    input_miss: int = 0
    output: int = 0
    reasoning: int = 0
    cost: float = 0.0
    unpriced: bool = False

    @property
    def input(self) -> int:
        return self.input_hit + self.input_miss

    def add(self, model: Model, body: dict) -> float:
        """Fold one response's usage in, and return what that call cost."""
        u = body.get("usage") or {}
        total_in = int(u.get("prompt_tokens") or 0)
        # DeepSeek splits the prompt into cached and uncached halves; other
        # providers report neither, in which case none of it was discounted.
        hit = int(u.get("prompt_cache_hit_tokens") or 0)
        miss = int(u.get("prompt_cache_miss_tokens") or (total_in - hit))
        out = int(u.get("completion_tokens") or 0)
        # A reasoning model bills its thinking as output and counts it against
        # max_tokens. Tracked separately because it is usually most of both.
        self.reasoning += int(
            (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        )

        self.calls += 1
        self.input_hit += hit
        self.input_miss += miss
        self.output += out

        if not model.priced:
            self.unpriced = True
            return 0.0
        hit_rate = model.input_hit_per_m
        if hit_rate is None:
            hit_rate = model.input_miss_per_m
        spent = (
            hit * hit_rate
            + miss * model.input_miss_per_m
            + out * model.output_per_m
        ) / 1_000_000
        self.cost += spent
        return spent

    def summary(self) -> str:
        cost = "cost unknown" if self.unpriced else f"${self.cost:.4f}"
        thinking = f" ({self.reasoning:,} reasoning)" if self.reasoning else ""
        return (
            f"{self.calls} calls, {self.input:,} in "
            f"({self.input_hit:,} cached), {self.output:,} out{thinking}, {cost}"
        )


class Client:
    """Calls one model, meters what it spends, and validates what comes back."""

    def __init__(
        self,
        model: Model | None = None,
        budget: float | None = None,
        timeout: int = TIMEOUT,
    ) -> None:
        self.model = model or resolve()
        self.budget = budget
        self.timeout = timeout
        self.usage = Usage()
        self._session = requests.Session()
        self._key: str | None = None

    def key(self) -> str:
        """The API key, read once and demanded plainly when it is missing.

        Same posture as SEC_EDGAR_UA in the EDGAR client: a run that cannot
        identify itself does not start and half-produce a reference file.
        """
        if self._key is None:
            key = os.environ.get(self.model.api_key_env, "").strip()
            if not key:
                raise RuntimeError(
                    f"{self.model.api_key_env} is not set; "
                    f"{self.model.provider} cannot be called without it"
                )
            self._key = key
        return self._key

    def check_budget(self) -> None:
        """Refuse the next call if the ceiling is already reached.

        Checked before spending rather than after, and only meaningful for a
        priced model — an unpriced provider cannot be metered, so a budget
        given for one is refused up front rather than silently ignored.
        """
        if self.budget is None:
            return
        if not self.model.priced:
            raise BudgetExceeded(
                f"--budget was given but {self.model} has no prices in "
                "providers.yaml, so spending cannot be metered"
            )
        if self.usage.cost >= self.budget:
            raise BudgetExceeded(
                f"budget ${self.budget:.2f} reached "
                f"(spent ${self.usage.cost:.4f} over {self.usage.calls} calls)"
            )

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        thinking: bool = True,
    ) -> str:
        """One chat completion, retried on the failures that are transient.

        The fixed instructions go in the system message and the variable rows
        in the user message, so a provider that caches on a shared prefix
        charges the discounted rate for everything but the batch itself.

        `thinking` controls whether the model reasons before answering. It is
        worth knowing what that costs: reasoning is billed as output and spent
        out of `max_tokens`, and on this task it ran about three times the
        answer itself. Left enabled by default because the gates cannot catch
        every kind of wrong answer — a county that exists resolves whether or
        not it is the right county.
        """
        self.check_budget()
        payload = {
            "model": self.model.slug,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": max_tokens,
            "temperature": temperature,
            "thinking": {"type": "enabled" if thinking else "disabled"},
        }
        headers = {
            "Authorization": f"Bearer {self.key()}",
            "Content-Type": "application/json",
        }
        url = f"{self.model.base_url}/chat/completions"

        last: Exception | str | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                r = self._session.post(
                    url, json=payload, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                last = exc
            else:
                if r.status_code == 200:
                    body = r.json()
                    spent = self.usage.add(self.model, body)
                    logger.debug(
                        "%s: call %d, $%.6f", self.model, self.usage.calls, spent
                    )
                    choices = body.get("choices") or []
                    choice = choices[0] if choices else {}
                    text = (choice.get("message") or {}).get("content")
                    # Stopping at the ceiling is not a bad answer, it is half
                    # an answer, and it looks like malformed JSON downstream.
                    # A reasoning model reaches it easily, because its
                    # thinking is spent from the same allowance — so say what
                    # happened and let the caller give it more room.
                    if choice.get("finish_reason") == "length":
                        raise Truncated(
                            f"{self.model}: hit max_tokens={max_tokens} "
                            f"({self.usage.reasoning:,} reasoning tokens so far); "
                            "the answer stopped mid-JSON"
                        )
                    # JSON mode is documented to return empty content now and
                    # then. An empty reply is a failed call, not an answer.
                    if text and text.strip():
                        return text
                    last = "empty content"
                elif r.status_code in RETRY_STATUS:
                    last = f"HTTP {r.status_code}"
                else:
                    raise RuntimeError(
                        f"{self.model}: HTTP {r.status_code}: {r.text[:400]}"
                    )
            if attempt < MAX_ATTEMPTS:
                delay = 2 ** attempt
                logger.warning(
                    "%s: %s, retrying in %ds (attempt %d/%d)",
                    self.model, last, delay, attempt, MAX_ATTEMPTS,
                )
                time.sleep(delay)
        raise RuntimeError(f"{self.model}: giving up after {MAX_ATTEMPTS} attempts: {last}")

    def complete_json(
        self,
        system: str,
        user: str,
        required: dict[str, type | tuple[type, ...]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.0,
        thinking: bool = True,
    ) -> dict:
        """A completion parsed as a JSON object, with a shallow shape check.

        `required` maps a key to the type it must hold. The check is
        deliberately thin: it catches a model that answered a different
        question, and leaves judging the answer's substance to the gate that
        has real evidence to judge it against.

        An answer cut off at the ceiling is retried with the ceiling raised,
        rather than counted as a refusal. How much room a batch needs is not
        knowable in advance when most of it goes on reasoning, and a whole
        batch abandoned for want of a few hundred tokens is the expensive
        kind of wrong.
        """
        room, problem = max_tokens, None
        for attempt in range(1, JSON_ATTEMPTS + 1):
            try:
                text = self.complete(system, user, room, temperature, thinking)
            except Truncated as exc:
                problem = str(exc)
                if room >= MAX_OUTPUT_TOKENS:
                    break
                room = min(room * 2, MAX_OUTPUT_TOKENS)
                logger.warning("%s; retrying with max_tokens=%d", problem, room)
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError as exc:
                problem = f"not JSON ({exc})"
            else:
                problem = shape_problem(parsed, required or {})
                if problem is None:
                    return parsed
            if attempt < JSON_ATTEMPTS:
                logger.warning("%s: %s; asking once more", self.model, problem)
        raise ModelRefused(f"{self.model}: {problem}")


def shape_problem(
    parsed: object, required: dict[str, type | tuple[type, ...]]
) -> str | None:
    """None when the object has the required keys at the required types."""
    if not isinstance(parsed, dict):
        return f"expected a JSON object, got {type(parsed).__name__}"
    for key, want in required.items():
        if key not in parsed:
            return f"missing key {key!r}"
        if not isinstance(parsed[key], want):
            names = want if isinstance(want, tuple) else (want,)
            return (
                f"key {key!r} is {type(parsed[key]).__name__}, "
                f"expected {' or '.join(t.__name__ for t in names)}"
            )
    return None
