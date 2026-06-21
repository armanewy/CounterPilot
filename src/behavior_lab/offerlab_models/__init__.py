from __future__ import annotations

__all__ = ["build_research_leaderboards", "run_sample_research_suite"]


def build_research_leaderboards(*args, **kwargs):
    from behavior_lab.offerlab_models.suite import build_research_leaderboards as impl

    return impl(*args, **kwargs)


def run_sample_research_suite(*args, **kwargs):
    from behavior_lab.offerlab_models.suite import run_sample_research_suite as impl

    return impl(*args, **kwargs)
