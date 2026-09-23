from fastapi.testclient import TestClient

from alenna.http import app

client = TestClient(app)
URL = "/articles/apply-suggestions"


def test_apply_suggestions_endpoint():
    payload = {
        "article": {
            "id": "a1",
            "segments": [
                {"id": "s1", "text": "The resuts shows. "},
                {"id": "s2", "text": "The metod works."},
            ],
        },
        "suggestions": [
            {"id": "x", "segment_id": "s1", "start": 4, "end": 10, "original": "resuts", "replacement": "results"},
            {"id": "y", "segment_id": "s1", "start": 11, "end": 16, "original": "shows", "replacement": "show"},
            {"id": "z", "segment_id": "s2", "start": 4, "end": 9, "original": "metod", "replacement": "method"},
        ],
        "decisions": [
            {"suggestion_id": "x", "action": "accept"},
            {"suggestion_id": "z", "action": "reject"},
        ],
    }
    response = client.post(URL, json=payload)

    assert response.status_code == 200
    body = response.json()
    assert [s["text"] for s in body["article"]["segments"]] == ["The results shows. ", "The metod works."]
    assert body["results"] == [
        {"suggestion_id": "x", "outcome": "applied", "reason": None},
        {"suggestion_id": "y", "outcome": "pending", "reason": None},
        {"suggestion_id": "z", "outcome": "rejected", "reason": None},
    ]
    assert body["remaining_suggestions"] == [
        {"id": "y", "segment_id": "s1", "start": 12, "end": 17, "original": "shows", "replacement": "show"},
    ]
    assert body["errors"] == []


def test_malformed_payload_returns_422():
    response = client.post(URL, json={"article": {"id": "a1"}, "decisions": [{"suggestion_id": "x", "action": "maybe"}]})
    assert response.status_code == 422


def test_duplicate_segments_returns_400():
    payload = {"article": {"id": "a1", "segments": [{"id": "s1", "text": "a"}, {"id": "s1", "text": "b"}]}}
    response = client.post(URL, json=payload)
    assert response.status_code == 400
    assert "s1" in response.json()["detail"]
