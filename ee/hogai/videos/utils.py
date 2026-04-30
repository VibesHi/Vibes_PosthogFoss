"""FOSS stub for ee.hogai.videos.utils.

Used by Temporal session-summary workflows that aren't scheduled on FOSS.
"""


def get_video_duration_s(*args, **kwargs) -> float:
    """Returns 0.0; never actually called on FOSS."""
    return 0.0
