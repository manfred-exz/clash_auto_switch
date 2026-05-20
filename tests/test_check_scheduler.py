import unittest

from clash_auto_switch.core.check_scheduler import (
    AdaptiveCheckScheduler,
    MAX_CHECK_INTERVAL_SEC,
    MIN_CHECK_INTERVAL_SEC,
    format_interval,
)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class AdaptiveCheckSchedulerTest(unittest.TestCase):
    def test_success_extends_interval_and_blocks_until_due(self) -> None:
        clock = FakeClock()
        scheduler = AdaptiveCheckScheduler(clock=clock)

        self.assertTrue(scheduler.can_check("youtube_music"))
        state = scheduler.record_result("youtube_music", True)

        self.assertEqual(state.interval_sec, MIN_CHECK_INTERVAL_SEC * 2)
        self.assertFalse(scheduler.can_check("youtube_music"))
        clock.value = state.interval_sec
        self.assertTrue(scheduler.can_check("youtube_music"))

    def test_failure_shortens_interval_to_minimum(self) -> None:
        clock = FakeClock()
        scheduler = AdaptiveCheckScheduler(clock=clock)

        for _ in range(4):
            scheduler.record_result("youtube_music", True)
            clock.value += scheduler.state("youtube_music").interval_sec

        self.assertGreater(scheduler.state("youtube_music").interval_sec, MIN_CHECK_INTERVAL_SEC)
        state = scheduler.record_result("youtube_music", False)

        self.assertEqual(state.failure_streak, 1)
        self.assertGreaterEqual(state.interval_sec, MIN_CHECK_INTERVAL_SEC)

    def test_interval_is_capped(self) -> None:
        scheduler = AdaptiveCheckScheduler()

        for _ in range(20):
            state = scheduler.record_result("youtube_music", True)

        self.assertEqual(state.interval_sec, MAX_CHECK_INTERVAL_SEC)

    def test_force_bypasses_interval(self) -> None:
        scheduler = AdaptiveCheckScheduler()
        scheduler.record_result("youtube_music", True)

        self.assertFalse(scheduler.can_check("youtube_music"))
        self.assertTrue(scheduler.can_check("youtube_music", force=True))

    def test_format_interval(self) -> None:
        self.assertEqual(format_interval(5), "5 秒")
        self.assertEqual(format_interval(90), "1.5 分钟")


if __name__ == "__main__":
    unittest.main()
