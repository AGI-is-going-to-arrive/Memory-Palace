from db.sqlite_client import SQLiteClient


def test_parse_chat_json_object_accepts_relaxed_object_keys() -> None:
    raw = """
    ```json
    {
      action: "ADD",
      target_id: "1",
      reason: "Memory migration checklist",
      method: "llm"
    }
    ```
    """
    parsed = SQLiteClient._parse_chat_json_object(raw)
    assert parsed == {
        "action": "ADD",
        "target_id": "1",
        "reason": "Memory migration checklist",
        "method": "llm",
    }


def test_parse_chat_json_object_accepts_relaxed_single_quotes() -> None:
    raw = "{'intent':'causal','confidence':0.73,'signals':['why']}"
    parsed = SQLiteClient._parse_chat_json_object(raw)
    assert parsed == {"intent": "causal", "confidence": 0.73, "signals": ["why"]}


def test_parse_chat_json_object_returns_none_for_non_json() -> None:
    assert SQLiteClient._parse_chat_json_object("plain text only") is None
