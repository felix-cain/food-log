"""Stand-ins for Claude and Garmin. All numbers are made up."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from backend.summary import ClaudeAnalysis
from backend.vision import FoodItem, MealAnalysis


def meal(description: str = "Peanut butter toast", grams: float = 80, calories: int = 200) -> MealAnalysis:
    return MealAnalysis(
        description=description,
        items=[FoodItem(name="sourdough", estimated_grams=grams, calories_estimate=calories,
                        protein_g=7, carbs_g=38, fat_g=1, confidence="medium")],
    )


class FakeAnthropic:
    """Replaces anthropic.Anthropic(): records each call and returns canned output.

    Set `fail` to an exception to make calls raise, or `stop_reason` to e.g.
    "refusal". Meal analyses come back as "version 2" when the prompt is a
    correction, so tests can tell a re-estimate from the first one.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.fail: Exception | None = None
        self.stop_reason = "end_turn"
        self.analysis = ClaudeAnalysis(observations=["Placeholder observation."], suggestions=["Placeholder suggestion."])
        self.messages = self

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        if kwargs["output_format"] is MealAnalysis:
            corrected = "full revised list" in self.prompt()
            out = meal("Toast (version 2)" if corrected else "Toast (version 1)", grams=160 if corrected else 80)
        else:
            out = self.analysis
        return SimpleNamespace(stop_reason=self.stop_reason, parsed_output=out)

    def prompt(self, index: int = -1) -> str:
        content = self.calls[index]["messages"][0]["content"]
        if isinstance(content, str):
            return content
        return next(b["text"] for b in content if b["type"] == "text")

    def images(self, index: int = -1) -> int:
        content = self.calls[index]["messages"][0]["content"]
        return 0 if isinstance(content, str) else sum(b["type"] == "image" for b in content)


class FakeGarmin:
    """Replaces garmin.GarminClient with Garmin-shaped synthetic responses.

    Every third day has no sleep (watch not worn); each day has a daily
    summary; there's one run on the last day of any activity search.
    """

    calls: list[tuple] = []
    fail: Exception | None = None

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.fail = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass

    def _record(self, *call) -> None:
        FakeGarmin.calls.append(call)
        if FakeGarmin.fail:
            raise FakeGarmin.fail

    def daily_sleep(self, day: str):
        self._record("sleep", day)
        if date.fromisoformat(day).day % 3 == 0:
            # The real empty response: every field null, two booleans set
            return {"id": None, "calendarDate": None, "sleepTimeSeconds": None, "deepSleepSeconds": None,
                    "deviceRemCapable": True, "retro": False}
        return {"calendarDate": day, "sleepTimeSeconds": 25200, "deepSleepSeconds": 5400,
                "lightSleepSeconds": 14400, "remSleepSeconds": 4500, "awakeSleepSeconds": 900,
                "sleepScores": {"overall": {"value": 78}},
                "sleepStartTimestampLocal": 1761433200000, "sleepEndTimestampLocal": 1761459300000}

    def hrv_summaries(self, start: str, end: str):
        self._record("hrv", start, end)
        return {"hrvSummaries": [{"calendarDate": d, "lastNightAvg": 55, "status": "BALANCED"}
                                 for d in _days(start, end)]}

    def daily_summaries(self, start: str, end: str):
        self._record("daily", start, end)
        return {"data": [{"calendarDate": d, "totalSteps": 9000, "activeKilocalories": 600, "bmrKilocalories": 1800,
                          "totalKilocalories": 2400, "restingHeartRate": 52} for d in _days(start, end)]}

    def body_battery(self, start: str, end: str):
        self._record("body_battery", start, end)
        return [{"date": start, "charged": 60, "drained": 55, "bodyBatteryValuesArray": [[1, 50]]}]

    def search_activities(self, start_date: str, end_date: str, start: int = 0, limit: int = 100):
        self._record("activities", start_date, end_date, start)
        return [{"activityId": int(end_date.replace("-", "")), "activityName": "Morning Run",
                 "activityType": {"typeKey": "running"}, "startTimeLocal": f"{end_date} 07:30:00",
                 "duration": 2700.0, "distance": 8000.0, "calories": 520.0, "averageHR": 148.0, "maxHR": 172.0,
                 "activityTrainingLoad": 140.0, "aerobicTrainingEffect": 3.4}]


def _days(start: str, end: str) -> list[str]:
    d, stop, out = date.fromisoformat(start), date.fromisoformat(end), []
    while d <= stop:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out
