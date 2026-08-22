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
    assert cleaned == "party dance grinning face https://example.test/ISSUE-1"


def test_cleaner_preserves_fenced_code_blocks():
    content = "before   text\n```python\nvalue =  1\n\nprint(value)\n```\n\n\n after"
    cleaned = clean_message(content)

    assert "before text" in cleaned
    assert "value =  1\n\nprint(value)" in cleaned
    assert cleaned.endswith("after")


def test_cleaner_converts_unicode_emoji_only_message_to_readable_words():
    assert "face with tears of joy" in clean_message("😂")


def test_cleaner_converts_multiple_unicode_emojis_to_readable_words():
    cleaned = clean_message("😢🔥")
    assert "crying face" in cleaned
    assert "fire" in cleaned


def test_cleaner_preserves_text_while_converting_unicode_emoji():
    cleaned = clean_message("I love this ❤️")
    assert "I love this" in cleaned
    assert "red heart" in cleaned


def test_cleaner_converts_discord_custom_emoji_names_to_readable_words():
    assert clean_message("<:party_time:123>") == "party time"
    assert clean_message("<a:dance:456>") == "dance"


def test_cleaner_preserves_emojis_inside_fenced_code_blocks():
    assert clean_message("```\n😂 <:party_time:123>\n```") == "```\n😂 <:party_time:123>\n```"


def test_cleaner_expands_common_abbreviations_case_insensitively():
    assert clean_message("btw this is good") == "by the way this is good"
    assert clean_message("BTW this is good") == "by the way this is good"
    assert clean_message("idk imo tbh") == "I don't know in my opinion to be honest"


def test_cleaner_expands_abbreviation_only_message_without_corrupting_words():
    assert clean_message("idk") == "I don't know"
    assert clean_message("between") == "between"


def test_cleaner_preserves_abbreviations_in_fenced_code_and_urls():
    assert clean_message("```\nbtw idk\n```") == "```\nbtw idk\n```"
    assert clean_message("https://example.com/btw") == "https://example.com/btw"
