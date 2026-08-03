# state_manager.py
# Handles persistent memory across GitHub Actions runs, since each run happens
# in a fresh, disposable VM with no memory of previous executions.
# The actual state.json file lives on a separate git branch ("state"),
# not on main, so it never mixes with source code history.

import json
import os
from datetime import datetime, timezone, timedelta

# Default cooldown window for repeated alerts on the same ticker+metric (hours)
DEFAULT_COOLDOWN_HOURS = 24

# Minimum severity ratio (new / last) required to break the cooldown early
ESCALATION_FACTOR = 1.3

# How many days a cached fundamentals entry stays valid before refreshing
FUNDAMENTALS_CACHE_DAYS = 7


class StateManager:
    """
    Wraps state.json and exposes methods for the three responsibilities:
    1. Alert cooldown (anti-spam) with severity-escalation exception
    2. Fundamentals caching (avoids refetching data that barely changes daily)
    3. Monthly LLM call counting (cost control)
    """

    def __init__(self, state_path="state/state.json"):
        self.state_path = state_path
        self.state = self._load()

    def _load(self):
        """
        Load state.json if it exists and is valid; otherwise return a fresh,
        empty state structure. This makes the very first run (no prior state)
        behave the same as any other run, with no special-casing needed elsewhere.
        """
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                # Corrupted or unreadable file: fail safe by starting fresh
                # rather than crashing the whole run.
                pass

        return {
            "alerts": {},
            "fundamentals_cache": {},
            "llm_calls": {"month": self._current_month(), "count": 0},
        }

    def save(self):
        """Persist the current in-memory state back to disk as JSON."""
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        with open(self.state_path, "w") as f:
            json.dump(self.state, f, indent=2)

    @staticmethod
    def _now():
        """Single source of truth for 'now' — always UTC, always timezone-aware."""
        return datetime.now(timezone.utc)

    @staticmethod
    def _current_month():
        return datetime.now(timezone.utc).strftime("%Y-%m")

    # ------------------------------------------------------------------
    # 1. Alert cooldown
    # ------------------------------------------------------------------

    def should_alert(self, ticker, metric, severity,
                      cooldown_hours=DEFAULT_COOLDOWN_HOURS,
                      escalation_factor=ESCALATION_FACTOR):
        """
        Decide whether a new alert for this ticker+metric should be sent.

        Returns True if:
        - there is no prior alert recorded for this ticker+metric, OR
        - the cooldown window has already elapsed, OR
        - the new severity is at least `escalation_factor` times the last
          severity that was actually alerted on (a genuinely worsening
          situation breaks the silence early).
        """
        key = f"{ticker}_{metric}"
        entry = self.state["alerts"].get(key)

        if entry is None:
            return True

        last_alert_ts = datetime.fromisoformat(entry["last_alert_ts"])
        elapsed = self._now() - last_alert_ts

        if elapsed >= timedelta(hours=cooldown_hours):
            return True

        last_severity = entry.get("last_severity", 0)
        if last_severity > 0 and (severity / last_severity) >= escalation_factor:
            return True

        return False

    def record_alert(self, ticker, metric, severity):
        """Record that an alert was just sent, for future cooldown checks."""
        key = f"{ticker}_{metric}"
        self.state["alerts"][key] = {
            "last_alert_ts": self._now().isoformat(),
            "last_severity": severity,
        }

    # ------------------------------------------------------------------
    # 2. Fundamentals cache
    # ------------------------------------------------------------------

    def get_cached_fundamentals(self, ticker, max_age_days=FUNDAMENTALS_CACHE_DAYS):
        """
        Return cached fundamentals data for a ticker if it exists and is still
        fresh (within max_age_days). Returns None if there's no cache entry
        or it has expired, signaling the caller to fetch fresh data.
        """
        entry = self.state["fundamentals_cache"].get(ticker)
        if entry is None:
            return None

        cached_at = datetime.fromisoformat(entry["cached_at"])
        if self._now() - cached_at > timedelta(days=max_age_days):
            return None

        return entry["data"]

    def cache_fundamentals(self, ticker, data):
        """Store freshly fetched fundamentals data with the current timestamp."""
        self.state["fundamentals_cache"][ticker] = {
            "data": data,
            "cached_at": self._now().isoformat(),
        }

    # ------------------------------------------------------------------
    # 3. Monthly LLM call counter
    # ------------------------------------------------------------------

    def increment_llm_call_count(self):
        """
        Increment the monthly LLM call counter. Automatically resets to 1
        (not 0) if the current month differs from the stored month, so the
        rollover requires no separate cron job or external logic.
        """
        current_month = self._current_month()
        if self.state["llm_calls"]["month"] != current_month:
            self.state["llm_calls"] = {"month": current_month, "count": 1}
        else:
            self.state["llm_calls"]["count"] += 1

    def get_llm_call_count(self):
        """Return the number of LLM calls made so far in the current month."""
        current_month = self._current_month()
        if self.state["llm_calls"]["month"] != current_month:
            return 0
        return self.state["llm_calls"]["count"]