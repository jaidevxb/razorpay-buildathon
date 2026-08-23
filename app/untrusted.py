"""Screening for hostile text in customer replies.

A customer reply is attacker-controlled text that we feed to a language
model. That makes it an injection surface. This module screens it BEFORE the
model ever sees it — deliberately with plain regex, not AI.

Why not ask the model whether the text is an attack? Because the text is the
attack. Asking the compromised channel to judge itself is how you get talked
out of the right answer. The screen has to sit outside the model, which means
it has to be deterministic.

Failure mode by design: a false positive costs a human ten seconds of review.
A false negative costs the merchant a written-off debt. The screen is tuned to
prefer the former, and a quarantine never moves money in any direction.
"""

import re

# Each pattern targets injection SHAPE, not ordinary rude or negative words.
# "I'm ignoring your messages" must not trip; "ignore previous instructions"
# must.
PATTERNS = [
    (r"\b(ignore|disregard|forget)\b[^.]{0,40}?"
     r"\b(instruction|instructions|prompt|above|previous|rule|rules)\b",
     "tries to override earlier instructions"),
    (r"^\s*(system|assistant|developer)\s*:",
     "impersonates a system or assistant message"),
    (r"\b(you are now|act as|pretend to be|from now on)\b",
     "tries to reassign the assistant's role"),
    (r"\b(respond|reply|output|answer|return)\b[^.]{0,30}?"
     r"(with\s*)?(exactly\s*)?[\{\[\"]",
     "dictates the literal output format"),
    (r"\b(intent|days_until_pay|recovery_status|claims_paid|promise_to_pay)\b",
     "references the system's own internal field names"),
    (r"\bthis is the (merchant|admin|owner|support)\b",
     "claims merchant or staff authority"),
    (r"\b(mark|set|update|change)\b[^.]{0,30}?"
     r"\b(as paid|to paid|as captured|as recovered|status)\b",
     "instructs a state change directly"),
]

COMPILED = [(re.compile(p, re.IGNORECASE | re.MULTILINE), why)
            for p, why in PATTERNS]

MAX_REPLY_CHARS = 500   # a genuine SMS reply is short; a payload is usually not


def screen(text: str) -> str | None:
    """Return a reason string if the text looks hostile, else None."""
    if text is None:
        return None
    if len(text) > MAX_REPLY_CHARS:
        return (f"reply is {len(text)} characters — far longer than a genuine "
                f"SMS reply, a common carrier for injected instructions")
    for pattern, why in COMPILED:
        if pattern.search(text):
            return why
    return None


def wrap_untrusted(text: str) -> str:
    """Fence customer text so the model can tell data from instructions.

    Defence in depth only — the screen above is the real control. Any closing
    tag inside the text is neutralised so the fence can't be broken out of.
    """
    safe = text.replace("</customer_reply>", "[/]")
    return f"<customer_reply>\n{safe}\n</customer_reply>"
