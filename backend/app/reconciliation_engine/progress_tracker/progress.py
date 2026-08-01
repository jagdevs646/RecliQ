from __future__ import annotations

from typing import Callable, Optional


class ProgressTracker:
    """
    Real-time progress reporting tracker with step messages and percentages.
    """

    def __init__(self, callback: Optional[Callable[[int, str], None]] = None):
        self.callback = callback
        self.current_progress = 0
        self.current_step = "Initialized"

    def update(self, progress: int, step: str) -> None:
        self.current_progress = max(0, min(100, progress))
        self.current_step = step
        if self.callback:
            try:
                self.callback(self.current_progress, self.current_step)
            except Exception:
                pass

    def reading_excel(self) -> None:
        self.update(20, "Reading Excel files...")

    def building_indexes(self) -> None:
        self.update(35, "Building hash indexes...")

    def matching_records(self) -> None:
        self.update(55, "Matching records with indexed lookup...")

    def comparing_columns(self) -> None:
        self.update(75, "Comparing mapped rule columns...")

    def generating_report(self) -> None:
        self.update(90, "Generating Excel report...")

    def finalized(self) -> None:
        self.update(100, "Reconciliation finalized")
