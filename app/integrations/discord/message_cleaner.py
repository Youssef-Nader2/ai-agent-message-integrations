import re
from collections.abc import Mapping


FENCED_CODE_BLOCK = re.compile(r"(```[\s\S]*?```)")
CUSTOM_EMOJI = re.compile(r"<a?:([A-Za-z0-9_~]+):\d+>")


def _clean_plain_text(text: str, mention_labels: Mapping[str, str]) -> str:
    for markup, label in mention_labels.items():
        text = text.replace(markup, label)
    text = CUSTOM_EMOJI.sub(lambda match: f":{match.group(1)}:", text)
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
