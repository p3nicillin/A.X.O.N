"""Generate the model prompt and JSON schema from the live intent specs, and
validate model output against them.

Everything here derives from the :class:`IntentSpec` list (which itself comes
from the skill registry), so the prompt, the constrained-decoding schema, and
the validator can never drift from the real capability set. This is §4 — the
"make-or-break" structured-output requirement.
"""
from __future__ import annotations

import json

from .base import IntentSpec

# Persona-free parsing instruction. The model is a translator, not a chatbot.
_PREAMBLE = (
    "You are the intent parser for A.X.O.N, a local-first voice desktop agent. "
    "You do NOT execute anything and you are NOT a chatbot. Translate the user's "
    "single utterance into ONE structured intent for the skill engine plus a "
    "short spoken reply in a concise, formal British style addressing the user "
    "as \"sir\".\n\n"
    "Respond with a SINGLE JSON object and nothing else, in exactly this shape:\n"
    "{\n"
    '  "thought": "<one short reasoning sentence>",\n'
    '  "intent": { "type": "<intent_type>", "parameters": { ... } },\n'
    '  "response_text": "<concise spoken reply>",\n'
    '  "confidence": <number 0.0-1.0>\n'
    "}\n"
)

_RULES = (
    "Rules:\n"
    "- Choose exactly one intent type from the list below; never invent a type.\n"
    "- Only use parameter names listed for the chosen intent; never add others.\n"
    "- Use \"chat\" only for greetings/thanks/small talk (no action).\n"
    "- Use \"answer\" for general knowledge, explanations, writing, coding, "
    "or advice that needs no live data or action; put the useful answer in "
    "response_text so it stays inside AXON.\n"
    "- Questions beginning explain/why/how, requests to write/summarise, and "
    "coding or educational questions MUST use \"answer\" unless a dedicated "
    "skill is required. Do not reject ordinary knowledge questions.\n"
    "- Prefer a dedicated skill over web_search. Use web_search only for "
    "current/external information not covered by another skill.\n"
    "- Use \"unknown\" if the request matches no listed capability. Do NOT invent "
    "or simulate a capability that is not listed.\n"
    "- Set confidence below 0.5 when the request is ambiguous.\n"
    "- response_text is spoken aloud; be concise but fully answer the request.\n"
    "- Output the JSON object only — no markdown, no prose."
)


def intent_catalogue_text(specs: list[IntentSpec]) -> str:
    lines = []
    for s in specs:
        params = ", ".join(s.parameters) if s.parameters else "(none)"
        lines.append(f"- {s.name}: params [{params}] — {s.description}")
    return "\n".join(lines)


def _few_shot(specs: list[IntentSpec]) -> str:
    """A handful of utterance->JSON examples, always including an explicit
    unknown/no-op so the model has a safe escape hatch. Only emit examples whose
    intent is actually available."""
    names = {s.name for s in specs}
    pool = [
        ("get_time", '{"thought":"time query","intent":{"type":"get_time",'
         '"parameters":{}},"response_text":"It is just past nine, sir.",'
         '"confidence":0.99}'),
        ("open_app", '{"thought":"launch app","intent":{"type":"open_app",'
         '"parameters":{"app":"notepad"}},"response_text":"Opening Notepad, sir.",'
         '"confidence":0.97}'),
        ("web_search", '{"thought":"web lookup","intent":{"type":"web_search",'
         '"parameters":{"query":"speed of light"}},"response_text":'
         '"Searching now, sir.","confidence":0.9}'),
        ("answer", '{"thought":"general explanation","intent":{"type":"answer",'
         '"parameters":{}},"response_text":"Recursion is when a function solves '
         'a problem by calling itself on a smaller version until it reaches a '
         'base case, sir.","confidence":0.96}'),
    ]
    examples = ["User: \"what time is it\"\n" + pool[0][1]] if "get_time" in names else []
    for name, js in pool[1:]:
        if name in names:
            utter = {"open_app": "open notepad",
                     "web_search": "look up the speed of light",
                     "answer": "explain recursion in one sentence"}[name]
            examples.append(f'User: "{utter}"\n{js}')
    # the all-important escape hatch
    examples.append(
        'User: "teleport me to Mars"\n'
        '{"thought":"no matching capability","intent":{"type":"unknown",'
        '"parameters":{}},"response_text":"I\'m afraid I can\'t do that, sir.",'
        '"confidence":0.95}')
    return "Examples:\n" + "\n\n".join(examples)


def build_system_prompt(specs: list[IntentSpec], *, extra: str = "") -> str:
    """The full system prompt: instruction + live catalogue + few-shot."""
    parts = [_PREAMBLE,
             "Available intent types:\n" + intent_catalogue_text(specs),
             _RULES,
             _few_shot(specs)]
    prompt = "\n\n".join(parts)
    return prompt + ("\n\n" + extra if extra else "")


def build_json_schema(specs: list[IntentSpec]) -> dict:
    """A JSON schema for constrained decoding (Ollama format / OpenAI
    response_format / convertible to GBNF). The intent ``type`` is constrained to
    the exact allowed enum; per-intent parameter validation happens in code."""
    names = [s.name for s in specs]
    return {
        "type": "object",
        "properties": {
            "thought": {"type": "string"},
            "intent": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": names},
                    "parameters": {"type": "object"},
                },
                "required": ["type", "parameters"],
            },
            "response_text": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["thought", "intent", "response_text", "confidence"],
    }


def validate_packet_dict(data: object, specs: list[IntentSpec]) -> tuple[bool, str]:
    """Validate raw model output against the spec. Returns (ok, error_message).

    Enforces the existing unknown-intent / unknown-parameter rejection: the
    intent type must be one of the allowed names and every parameter key must be
    declared for that intent.
    """
    if not isinstance(data, dict):
        return False, "output is not a JSON object"
    intent = data.get("intent")
    if not isinstance(intent, dict):
        return False, "missing 'intent' object"
    itype = intent.get("type")
    by_name = {s.name: s for s in specs}
    if itype not in by_name:
        return False, f"unknown intent type {itype!r}"
    params = intent.get("parameters", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return False, "'parameters' must be an object"
    allowed = set(by_name[itype].parameters)
    extra = set(map(str, params)) - allowed
    if extra:
        return False, f"unexpected parameter(s) {sorted(extra)} for {itype!r}"
    return True, ""


def repair_instruction(error: str, schema: dict) -> str:
    """A terse follow-up message asking the model to fix invalid output."""
    return (f"Your previous reply was invalid: {error}. "
            "Reply again with ONLY a corrected JSON object that conforms to this "
            "schema (no prose, no markdown):\n" + json.dumps(schema))
