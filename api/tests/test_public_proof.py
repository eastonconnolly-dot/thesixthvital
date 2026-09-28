from extensions import db
from models import ConsentRequest


def test_public_proof_empty_state(client):
    resp = client.get("/public/proof")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["testimonials"] == []
    assert len(data["stats"]) == 4


def test_public_proof_shows_only_approved_granted_testimonials(app, client):
    with app.app_context():
        db.session.add(ConsentRequest(
            kind="testimonial", participant_name="Dana Ortiz", token="tok1",
            granted=True, approved=True, response_text="This changed how I deliver bad news.",
        ))
        db.session.add(ConsentRequest(
            kind="testimonial", participant_name="Not Approved Yet", token="tok2",
            granted=True, approved=False, response_text="Great session.",
        ))
        db.session.add(ConsentRequest(
            kind="testimonial", participant_name="Declined", token="tok3",
            granted=False, approved=False, response_text=None,
        ))
        db.session.add(ConsentRequest(
            kind="clip_consent", participant_name="Wrong Kind", token="tok4",
            granted=True, approved=True, response_text="Sure, use my clip.",
        ))
        db.session.commit()

    resp = client.get("/public/proof")
    data = resp.get_json()
    assert len(data["testimonials"]) == 1
    assert data["testimonials"][0]["quote"] == "This changed how I deliver bad news."
    assert data["testimonials"][0]["attribution"] == "Dana Ortiz"
