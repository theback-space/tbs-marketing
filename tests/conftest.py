import pytest

from tbs_social.ai import OfflineAI
from tbs_social.config import Brand, Settings
from tbs_social.db import DB
from tbs_social.engine import Engine
from tbs_social.platforms.mock import MockPlatform

BRAND = Brand(
    name="Test Clinic",
    contact_email="hello@clinic.test",
    disclaimer="General information only.",
    private_channel="Please message us privately.",
    banned_phrases=["cure", "guaranteed"],
    content_pillars=["Tips", "Myths"],
    hashtags=["#Back"],
)


@pytest.fixture
def make_engine(tmp_path):
    def _make(automation="approve", ai=None, platform=None):
        settings = Settings(db_path=str(tmp_path / "t.db"), automation=automation, platforms=("mock",))
        platform = platform or MockPlatform(seed=1)
        return Engine(DB(settings.db_path), {"mock": platform}, ai or OfflineAI(BRAND), BRAND, settings)
    return _make
