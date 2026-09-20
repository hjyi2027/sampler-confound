"""One adapter per provider, behind one interface.

Probe code builds a *canonical* request — the OpenAI-flavoured dict this repo has
always built, with the parameter names in CANONICAL_PARAMS — and gets back a
`Completion`. It never sees a vendor's wire format in either direction, so it
cannot branch on vendor: the same probe runs against Fireworks, Groq, or Google
by changing one string.

An adapter owns exactly the vendor-specific facts:

    qualify()  what the vendor calls the model ("accounts/fireworks/models/x")
    url()      where the request goes; Google puts the model in the path
    headers()  how the key travels; Google uses x-goog-api-key, not Bearer
    encode()   canonical request -> wire body, plus the canonical parameters
               this adapter has NO wire form for (see below)
    payload()  which part of the wire body is actually transmitted
    decode()   wire response -> Completion

**Encoding is permissive on purpose.** Whether a provider honours `top_k` is
what the distinguishability probe measures, and a static table in this file
saying "Groq has no top_k" would pre-empt the measurement with my belief. So an
adapter passes a parameter through unless the wire schema *cannot carry it*
(Mistral 422s on any unknown field; Google's generationConfig is closed), and
in that case reports it in `Completion.dropped` rather than sending nothing
and saying nothing. The provider's own 400/422 is then the finding, recorded by
`complete()` as "rejected". Wrong in the permissive direction costs one free
400; wrong in the restrictive direction silently skips a real test.

**The cache key is the wire body**, not the canonical one. What was sent is what
determined the response, and for Fireworks the wire body is byte-identical to
what `complete()` hashed before adapters existed, so the stored responses keep
hitting — tests/test_adapters.py pins a golden hash for that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# The vocabulary a probe may use. Anything else is refused at the boundary, so
# a vendor spelling ("random_seed", "topK") cannot leak into probe code.
CANONICAL_PARAMS = frozenset({
    "model", "messages", "max_tokens",
    "temperature", "top_p", "top_k", "min_p", "seed", "reasoning_effort",
})
SAMPLER_PARAMS = frozenset({"temperature", "top_p", "top_k", "min_p", "seed",
                            "reasoning_effort"})


@dataclass(frozen=True)
class Completion:
    """What every provider's response is reduced to."""

    text: str                            # assistant content; "" if the model emitted none
    reasoning: str                       # chain-of-thought where the API returns it separately
    finish_reason: str | None            # "stop" | "length" | vendor's word, lowercased
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cached_tokens: int | None
    request_id: str | None               # provenance: chase an anomaly back to the provider
    created: int | None
    served_by: str | None                # adapter name; OpenRouter reports the upstream it chose
    dropped: tuple[str, ...]             # canonical params this adapter had no wire form for
    raw: dict = field(repr=False)        # the full response, exactly as cached

    @property
    def truncated(self) -> bool:
        return self.finish_reason == "length"


class Adapter:
    """Base: the OpenAI chat-completions shape with a Bearer key.

    Six of the seven providers speak this shape, so they subclass with facts
    only (URL, env var, renames). Google overrides the methods.
    """

    name: str
    base: str
    env: str
    signup: str
    prefix: str = ""
    # canonical name -> wire name, for vendors that spell a parameter differently
    rename: dict[str, str] = {}
    # canonical params the wire schema cannot carry at all (reported, not sent)
    no_wire_form: frozenset[str] = frozenset()

    def qualify(self, model: str) -> str:
        if self.prefix and not model.startswith(self.prefix):
            return self.prefix + model
        return model

    def url(self, wire: dict) -> str:
        return self.base

    def headers(self, key: str) -> dict:
        return {"Authorization": f"Bearer {key}"}

    def encode(self, req: dict) -> tuple[dict, tuple[str, ...]]:
        unknown = set(req) - CANONICAL_PARAMS
        if unknown:
            raise ValueError(f"not a canonical request parameter: {sorted(unknown)}; "
                             f"probe code uses {sorted(CANONICAL_PARAMS)} only")
        wire, dropped = {}, []
        for k, v in req.items():
            if k in self.no_wire_form:
                dropped.append(k)
            elif k == "model":
                wire["model"] = self.qualify(v)
            else:
                wire[self.rename.get(k, k)] = v
        return wire, tuple(dropped)

    def payload(self, wire: dict) -> dict:
        return wire

    def catalogue_url(self) -> str:
        return self.base.rsplit("/chat/completions", 1)[0] + "/models"

    def catalogue(self, raw: dict) -> list[dict]:
        """The provider's model listing, reduced to {"id", "chat"}.

        `chat` is True/False where the listing says so and None where it does
        not; the caller decides what to do with None (a name filter, usually).
        """
        out = []
        for m in raw.get("data") or raw.get("models") or []:
            mid = m.get("id") or m.get("name") or ""
            mid = mid[len(self.prefix):] if self.prefix and mid.startswith(self.prefix) else mid
            chat = m.get("supports_chat")
            out.append({"id": mid, "chat": chat if isinstance(chat, bool) else None})
        return out

    def decode(self, raw: dict, dropped: tuple[str, ...] = ()) -> Completion:
        choice = raw["choices"][0]
        msg = choice.get("message") or {}
        usage = raw.get("usage") or {}
        return Completion(
            text=msg.get("content") or "",
            # Fireworks and NIM: reasoning_content. Groq and OpenRouter: reasoning.
            reasoning=msg.get("reasoning_content") or msg.get("reasoning") or "",
            finish_reason=_lower(choice.get("finish_reason")),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            reasoning_tokens=(usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            cached_tokens=(usage.get("prompt_tokens_details") or {}).get("cached_tokens"),
            request_id=raw.get("id"),
            created=raw.get("created"),
            served_by=self.name,
            dropped=dropped,
            raw=raw,
        )


def _lower(x) -> str | None:
    return x.lower() if isinstance(x, str) else x


class Fireworks(Adapter):
    name = "fireworks"
    base = "https://api.fireworks.ai/inference/v1/chat/completions"
    env = "FIREWORKS_API_KEY"
    signup = "https://fireworks.ai/"
    prefix = "accounts/fireworks/models/"


class Groq(Adapter):
    name = "groq"
    base = "https://api.groq.com/openai/v1/chat/completions"
    env = "GROQ_API_KEY"
    signup = "https://console.groq.com/keys"


class Cerebras(Adapter):
    name = "cerebras"
    base = "https://api.cerebras.ai/v1/chat/completions"
    env = "CEREBRAS_API_KEY"
    signup = "https://cloud.cerebras.ai/"


class OpenRouter(Adapter):
    name = "openrouter"
    base = "https://openrouter.ai/api/v1/chat/completions"
    env = "OPENROUTER_API_KEY"
    signup = "https://openrouter.ai/keys"

    def decode(self, raw: dict, dropped: tuple[str, ...] = ()) -> Completion:
        c = super().decode(raw, dropped)
        # OpenRouter is a router. Which upstream served a call can change between
        # replicates of the identical request — a confound for a determinism
        # probe, so it is recorded per completion rather than assumed constant.
        upstream = raw.get("provider")
        served = f"openrouter/{upstream}" if upstream else self.name
        return _replace(c, served_by=served)


class Nvidia(Adapter):
    name = "nvidia"
    base = "https://integrate.api.nvidia.com/v1/chat/completions"
    env = "NVIDIA_API_KEY"
    signup = "https://build.nvidia.com/"


class Mistral(Adapter):
    name = "mistral"
    base = "https://api.mistral.ai/v1/chat/completions"
    env = "MISTRAL_API_KEY"
    signup = "https://console.mistral.ai/api-keys"
    rename = {"seed": "random_seed"}
    # Mistral validates the body and 422s on any field it does not define;
    # reasoning_effort is not one of its fields (Magistral reasons by default).
    # top_k and min_p are deliberately NOT listed: they are the parameters under
    # test, and Mistral's 422 on them is the measurement.
    no_wire_form = frozenset({"reasoning_effort"})


class Google(Adapter):
    """AI Studio's native generateContent, not its OpenAI-compatible shim.

    The shim has no top_k. The native API does (topK), and a probe of whether
    top_k is honoured has to be able to send it, so this adapter speaks the
    native shape: model in the URL, key in a header, generationConfig for the
    sampler, candidates/parts on the way back.
    """

    name = "google"
    base = "https://generativelanguage.googleapis.com/v1beta/models"
    env = "GOOGLE_API_KEY"
    signup = "https://aistudio.google.com/apikey"
    _config = {"temperature": "temperature", "top_p": "topP", "top_k": "topK",
               "min_p": "minP",             # not a field; Google's 400 is the finding
               "seed": "seed", "max_tokens": "maxOutputTokens"}

    def url(self, wire: dict) -> str:
        return f"{self.base}/{wire['model']}:generateContent"

    def headers(self, key: str) -> dict:
        return {"x-goog-api-key": key}

    def encode(self, req: dict) -> tuple[dict, tuple[str, ...]]:
        unknown = set(req) - CANONICAL_PARAMS
        if unknown:
            raise ValueError(f"not a canonical request parameter: {sorted(unknown)}")
        system = [m["content"] for m in req["messages"] if m["role"] == "system"]
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]}
                    for m in req["messages"] if m["role"] != "system"]
        config: dict = {"candidateCount": 1}
        for k, wk in self._config.items():
            if k in req:
                config[wk] = req[k]
        if "reasoning_effort" in req:
            # The direct semantic equivalent. Models without thinkingLevel
            # return 400, which complete() records as rejected — visible, not
            # a silently altered request.
            config["thinkingConfig"] = {"thinkingLevel": req["reasoning_effort"]}
        wire = {"model": self.qualify(req["model"]), "contents": contents,
                "generationConfig": config}
        if system:
            wire["systemInstruction"] = {"parts": [{"text": "\n\n".join(system)}]}
        return wire, ()

    def payload(self, wire: dict) -> dict:
        return {k: v for k, v in wire.items() if k != "model"}

    def catalogue_url(self) -> str:
        return f"{self.base}?pageSize=200"

    def catalogue(self, raw: dict) -> list[dict]:
        out = []
        for m in raw.get("models") or []:
            name = m.get("name", "")
            mid = name[len("models/"):] if name.startswith("models/") else name
            methods = m.get("supportedGenerationMethods") or []
            out.append({"id": mid, "chat": ("generateContent" in methods) if methods else None})
        return out

    def decode(self, raw: dict, dropped: tuple[str, ...] = ()) -> Completion:
        cands = raw.get("candidates") or [{}]
        parts = ((cands[0].get("content") or {}).get("parts")) or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        thought = "".join(p.get("text", "") for p in parts if p.get("thought"))
        usage = raw.get("usageMetadata") or {}
        finish = _lower(cands[0].get("finishReason"))
        return Completion(
            text=text, reasoning=thought,
            finish_reason={"max_tokens": "length"}.get(finish, finish),
            prompt_tokens=usage.get("promptTokenCount"),
            completion_tokens=usage.get("candidatesTokenCount"),
            reasoning_tokens=usage.get("thoughtsTokenCount"),
            cached_tokens=usage.get("cachedContentTokenCount"),
            request_id=raw.get("responseId"),
            created=None,
            served_by=self.name, dropped=dropped, raw=raw,
        )


def _replace(c: Completion, **kw) -> Completion:
    from dataclasses import replace
    return replace(c, **kw)


ADAPTERS: dict[str, Adapter] = {a.name: a for a in
                                (Fireworks(), Groq(), Cerebras(), Google(),
                                 OpenRouter(), Nvidia(), Mistral())}
