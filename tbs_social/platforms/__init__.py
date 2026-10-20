from tbs_social.config import Settings
from tbs_social.platforms.base import Platform, PlatformError


def build_platforms(settings: Settings) -> dict[str, Platform]:
    platforms: dict[str, Platform] = {}
    for name in settings.platforms:
        if name == "mock":
            from tbs_social.platforms.mock import MockPlatform
            platforms[name] = MockPlatform()
        elif name == "facebook":
            from tbs_social.platforms.meta import FacebookPage
            platforms[name] = FacebookPage(settings.meta_page_id, settings.meta_page_token,
                                           settings.meta_graph_version)
        elif name == "instagram":
            from tbs_social.platforms.meta import InstagramBusiness
            platforms[name] = InstagramBusiness(settings.meta_ig_user_id, settings.meta_page_token,
                                                settings.meta_graph_version)
        elif name == "google":
            from tbs_social.platforms.google import GoogleBusinessProfile
            platforms[name] = GoogleBusinessProfile(
                settings.gbp_account_id, settings.gbp_location_id, settings.google_client_id,
                settings.google_client_secret, settings.google_refresh_token)
        else:
            raise PlatformError(f"Unknown platform '{name}'")
    return platforms
