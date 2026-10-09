# app/schedulers/reevaluation_auto_scheduler_runner.py

from __future__ import annotations

import os
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.reevaluation_autoschedular_service import (
    run_reevaluation_scheduler_once,
)


# ============================================================
# CONFIGURATION
# ============================================================

TIMEZONE = ZoneInfo(
    os.getenv(
        "REEVALUATION_SCHEDULER_TIMEZONE",
        "Asia/Kolkata",
    )
)

RUN_HOUR = int(
    os.getenv(
        "REEVALUATION_SCHEDULER_HOUR",
        "0",
    )
)

RUN_MINUTE = int(
    os.getenv(
        "REEVALUATION_SCHEDULER_MINUTE",
        "5",
    )
)

CHECK_INTERVAL_SECONDS = int(
    os.getenv(
        "REEVALUATION_SCHEDULER_CHECK_SECONDS",
        "60",
    )
)


# ============================================================
# HELPERS
# ============================================================

def _scheduled_time_for_date(now: datetime) -> datetime:
    return now.replace(
        hour=RUN_HOUR,
        minute=RUN_MINUTE,
        second=0,
        microsecond=0,
    )


def _should_run_today(
    now: datetime,
    last_run_date,
) -> bool:

    scheduled_time = (
        _scheduled_time_for_date(
            now
        )
    )

    return (
        now >= scheduled_time
        and last_run_date != now.date()
    )


# ============================================================
# MAIN PROCESS
#
# Run this as ONE dedicated Docker service.
#
# Default:
#   Every day at 00:05 Asia/Kolkata.
#
# If container starts later than 00:05:
#   it runs once after startup for that day.
#
# Duplicate emails are still prevented by the scheduler-log
# table and active-reevaluation checks in the service.
# ============================================================

def main() -> None:

    print(
        "[REEVALUATION SCHEDULER] Started",
        flush=True,
    )

    print(
        (
            "[REEVALUATION SCHEDULER] "
            f"Timezone={TIMEZONE}, "
            f"schedule={RUN_HOUR:02d}:"
            f"{RUN_MINUTE:02d}"
        ),
        flush=True,
    )

    last_run_date = None

    while True:

        now = datetime.now(
            TIMEZONE
        )

        if _should_run_today(
            now,
            last_run_date,
        ):

            try:

                print(
                    (
                        "[REEVALUATION SCHEDULER] "
                        f"Running at {now.isoformat()}"
                    ),
                    flush=True,
                )

                result = (
                    run_reevaluation_scheduler_once()
                )

                print(
                    (
                        "[REEVALUATION SCHEDULER] "
                        f"Result: {result}"
                    ),
                    flush=True,
                )

                last_run_date = (
                    now.date()
                )

            except Exception:

                print(
                    (
                        "[REEVALUATION SCHEDULER] "
                        "Run failed."
                    ),
                    flush=True,
                )

                traceback.print_exc()

                # Do not mark the date as completed.
                # The process will retry on the next check.

        time.sleep(
            CHECK_INTERVAL_SECONDS
        )


if __name__ == "__main__":
    main()
