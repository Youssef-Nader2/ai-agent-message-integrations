from app.integrations.discord.message_cleaner import clean_message


def test_cleaner_normalizes_plain_text_and_empty_text():
    assert clean_message("  hello   world  ") == "hello world"
    assert clean_message("") == ""


def test_cleaner_normalizes_crlf_and_excessive_blank_lines():
    assert clean_message("one\r\n\r\n\r\n\r\ntwo") == "one\n\ntwo"


def test_cleaner_preserves_unresolved_mentions_and_replaces_mapped_mentions():
    assert clean_message("<@1> <#2> <@&3>") == "<@1> <#2> <@&3>"
    assert clean_message(
        "<@1> <#2>", {"<@1>": "@alice", "<#2>": "#general"}
    ) == "@alice #general"


def test_cleaner_normalizes_custom_emoji_and_preserves_unicode_and_urls():
    cleaned = clean_message("<:party:123> <a:dance:456> 😀 https://example.test/ISSUE-1")
    assert cleaned == ":party: :dance: 😀 https://example.test/ISSUE-1"


def test_cleaner_preserves_fenced_code_blocks():
    content = "before   text\n```python\nvalue =  1\n\nprint(value)\n```\n\n\n after"
    cleaned = clean_message(content)

    assert "before text" in cleaned
    assert "value =  1\n\nprint(value)" in cleaned
    assert cleaned.endswith("after")
