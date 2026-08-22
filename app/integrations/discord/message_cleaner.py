import re
from collections.abc import Mapping

import emoji


FENCED_CODE_BLOCK = re.compile(r"(```[\s\S]*?```)")
CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_~]+):\d+>")
URL = re.compile(r"(https?://[^\s]+)")
ABBREVIATIONS = {
    "afaik": "as far as I know",
    "brb": "be right back",
    "btw": "by the way",
    "fyi": "for your information",
    "idk": "I don't know",
    "ikr": "I know right",
    "imho": "in my humble opinion",
    "imo": "in my opinion",
    "jk": "just kidding",
    "lol": "laughing out loud",
    "ngl": "not going to lie",
    "np": "no problem",
    "omg": "oh my god",
    "pls": "please",
    "plz": "please",
    "rn": "right now",
    "smh": "shaking my head",
    "tbh": "to be honest",
    "thx": "thanks",
    "ty": "thank you",
    "yw": "you're welcome",
}
ABBREVIATION_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_./:@-])(" + "|".join(ABBREVIATIONS) + r")(?![A-Za-z0-9_./:@-])",
    re.IGNORECASE,
)


def _readable_custom_emoji(match: re.Match[str]) -> str:
    return match.group(1).replace("_", " ")


def _expand_abbreviation(match: re.Match[str]) -> str:
    return ABBREVIATIONS[match.group(1).lower()]


def _clean_non_url_text(text: str) -> str:
    text = CUSTOM_EMOJI.sub(_readable_custom_emoji, text)
    text = emoji.demojize(text, delimiters=(" ", " ")).replace("_", " ")
    return ABBREVIATION_PATTERN.sub(_expand_abbreviation, text)


def _clean_plain_text(text: str, mention_labels: Mapping[str, str]) -> str:
    for markup, label in mention_labels.items():
        text = text.replace(markup, label)
    text = "".join(
        part if index % 2 else _clean_non_url_text(part)
        for index, part in enumerate(URL.split(text))
    )
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def clean_message(
    content: str, mention_labels: Mapping[str, str] | None = None
) -> str:
    normalized_content = content.replace("\r\n", "\n").replace("\r", "\n")
    labels = mention_labels or {}
    parts = FENCED_CODE_BLOCK.split(normalized_content)
    cleaned_parts = [
        part if index % 2 else _clean_plain_text(part, labels)
        for index, part in enumerate(parts)
    ]
    return "".join(cleaned_parts).strip()
