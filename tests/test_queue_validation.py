from warnlive.adjudicate.queue import _answers_by_id


def test_batch_ids_must_be_exact_unique_integers():
    body = {"results": [
        {"id": 1, "answer": "valid"},
        {"id": True, "answer": "boolean"},
        {"id": 2.0, "answer": "float"},
        {"id": "2", "answer": "string"},
        {"id": 3, "answer": "first"},
        {"id": 3, "answer": "conflicting"},
        {"id": 4, "answer": "out of range"},
    ]}
    assert _answers_by_id(body, 3) == {1: {"id": 1, "answer": "valid"}}


def test_batch_rejects_invalid_nested_confidence():
    body = {"results": [
        {"id": 1, "confidence": float("nan")},
        {"id": 2, "confidence": True},
        {"id": 3, "confidence": 1.1},
    ]}
    assert _answers_by_id(body, 3) == {}
