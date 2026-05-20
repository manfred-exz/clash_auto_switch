from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceHostPatterns:
    """Host patterns used for service traffic detection.

    trigger_hosts should stay narrow because they decide when auto mode starts
    a service check. connection_hosts may be broader because they are used after
    a service is already known, for connection display and cleanup.
    """

    trigger_hosts: tuple[str, ...]
    extra_connection_hosts: tuple[str, ...] = ()

    @property
    def connection_match_hosts(self) -> tuple[str, ...]:
        return self.extra_connection_hosts or self.trigger_hosts


SERVICE_HOST_PATTERNS: dict[str, ServiceHostPatterns] = {
    "bilibili_mainland": ServiceHostPatterns(
        trigger_hosts=("bilibili.com", "bilibili.cn", "bilivideo.com", "biligame.net"),
    ),
    "bilibili_hk_mc_tw": ServiceHostPatterns(
        trigger_hosts=("bilibili.com", "bilibili.tv", "bilivideo.com"),
    ),
    "chatgpt": ServiceHostPatterns(
        trigger_hosts=(
            "chat.openai.com",
            "chatgpt.com",
            "api.openai.com",
            "oaistatic.com",
            "oaiusercontent.com",
        ),
    ),
    "claude": ServiceHostPatterns(
        trigger_hosts=("claude.ai", "anthropic.com"),
    ),
    "gemini": ServiceHostPatterns(
        trigger_hosts=(
            "gemini.google.com",
            "generativelanguage.googleapis.com",
            "aistudio.google.com",
            "ai.google.dev",
        ),
    ),
    "youtube_music": ServiceHostPatterns(
        trigger_hosts=("music.youtube.com", "youtubei.googleapis.com"),
        extra_connection_hosts=(
            "googlevideo.com",
            "ytimg.com",
            "youtube.com",
        ),
    ),
    "youtube_premium": ServiceHostPatterns(
        trigger_hosts=(
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtubei.googleapis.com",
            "googlevideo.com",
            "ytimg.com",
            "music.youtube.com",
        ),
    ),
    "bahamut_anime": ServiceHostPatterns(
        trigger_hosts=("ani.gamer.com.tw", "gamer.com.tw"),
    ),
    "netflix": ServiceHostPatterns(
        trigger_hosts=("netflix.com", "nflxvideo.net", "nflximg.net", "nflxext.com", "fast.com"),
    ),
    "disney_plus": ServiceHostPatterns(
        trigger_hosts=("disneyplus.com", "disney.api.edge.bamgrid.com", "bamgrid.com", "disney-plus.net"),
        extra_connection_hosts=("disneyplus.com", "bamgrid.com", "disney-plus.net"),
    ),
    "prime_video": ServiceHostPatterns(
        trigger_hosts=("primevideo.com", "amazonvideo.com", "media-amazon.com", "pv-cdn.net"),
    ),
    "emby_as174": ServiceHostPatterns(
        trigger_hosts=("emby.as174.de",),
    ),
}


def auto_trigger_host_patterns() -> dict[str, tuple[str, ...]]:
    return {
        service_name: patterns.trigger_hosts
        for service_name, patterns in SERVICE_HOST_PATTERNS.items()
    }


def connection_host_patterns(service_name: str) -> tuple[str, ...]:
    patterns = SERVICE_HOST_PATTERNS.get(service_name)
    if patterns is None:
        return ()
    return patterns.connection_match_hosts
