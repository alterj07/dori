"""Everything Dori says from a template, and every prompt sent to the model."""

from __future__ import annotations

STYLES = ("casual", "friendly", "romantic", "business")

# --- Fixed messages ---

WELCOME = """Hi! I'm Dori 🪨, your texting buddy. Text with me to practice Korean.

First, tell me a little about you. Please answer in one message:
1. Your name
2. Your native language
3. The language you want to practice
4. Your level, in your own words
5. The style you want me to text in: casual, friendly, romantic or business"""

TIPS = """Quick tips:
• Don't get a message? React with ❓ and I'll explain it in English
• Ask me anything, like "what does 대박 mean?"
• If you make a mistake, I'll reply to your text with a fix
• When you're ready to keep chatting, just reply in Korean
• Text "help" anytime to see this again"""

_ASK = {
    "name": "your name",
    "native": "your native language",
    "target": "the language you want to practice",
    "level_desc": "your level, in your own words",
    "style": "the style you want: casual, friendly, romantic or business",
}


def ask_missing(missing: list[str]) -> str:
    """The follow-up that asks only for the profile fields still missing."""
    lines = "\n".join(f"• {_ASK[field]}" for field in missing)
    return f"Thanks! I still need:\n{lines}"


# --- Onboarding ---

ONBOARDING_EXTRACT = """You fill in a sign-up form from a text message. You never chat or answer the message.

Someone is signing up for Dori, a texting buddy for language practice. Dori asked them for:
- name: what to call them
- native: their native language
- target: the language they want to practice
- level_desc: how they describe their own level in that language
- style: how they want Dori to text them

Already known: {known}
Still needed: {missing}

Read their message and return ONLY this JSON object, with no code fence and no other text:
{{"name": string or null, "native": string or null, "target": string or null, "level_desc": string or null, "style": string or null}}

Rules:
- Use null for anything the message does not say. Never guess.
- name: only the name, capitalised the usual way.
- native and target: the English name of the language, such as "English" or "Korean".
- level_desc: copy their own words about their level exactly. Do not summarise, translate or correct them.
- style: exactly one of "casual", "friendly", "romantic", "business". Map a close word to the nearest one: formal, polite, professional or work means business; flirty or dating means romantic; chill, relaxed or informal means casual; warm, kind or gentle means friendly. If nothing fits, use null.
- A short answer usually belongs to a field that is still needed. If only style is still needed and they write "casual please", that is the style."""


def onboarding_system(known: dict[str, str], missing: list[str]) -> str:
    """The extraction prompt, told what is already known and what is still needed."""
    known_text = ", ".join(f'{field} = "{value}"' for field, value in known.items())
    return ONBOARDING_EXTRACT.format(known=known_text or "nothing yet", missing=", ".join(missing))
