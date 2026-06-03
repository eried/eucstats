from io import BytesIO
from datetime import datetime

import pytest
from PIL import Image

from services.identity import IdentityService, ChangeNotAllowed, process_avatar


def _img(size=(200, 150), color=(255, 0, 0, 255)) -> bytes:
    b = BytesIO()
    Image.new("RGBA", size, color).save(b, format="PNG")
    return b.getvalue()


def test_register_and_profile(db):
    svc = IdentityService(db)
    svc.register("u1", "google_play", "Alice", "NO", avatar_bytes=_img())
    p = svc.get_profile("u1")
    assert p["display_name"] == "Alice"
    assert p["flag"] == "NO"
    assert p["has_avatar"] is True


def test_avatar_resized_and_reencoded():
    out = process_avatar(_img((200, 150)))
    im = Image.open(BytesIO(out))
    assert im.size == (64, 64)
    assert im.format == "PNG"


def test_monthly_name_limit(db):
    svc = IdentityService(db)
    svc.register("u2", "google_play", "Bob", "NO")
    r = svc.repo.get("u2")
    r.last_name_change = datetime(2026, 6, 1)
    db.commit()
    with pytest.raises(ChangeNotAllowed):
        svc.update("u2", "name", "Bobby", now=datetime(2026, 6, 20))   # same month
    svc.update("u2", "name", "Bobby", now=datetime(2026, 7, 1))        # next month OK
    assert svc.get_profile("u2")["display_name"] == "Bobby"


def test_delete_hides_profile(db):
    svc = IdentityService(db)
    svc.register("u3", "google_play", "Cara", "NO")
    svc.delete("u3")
    assert svc.get_profile("u3") is None
