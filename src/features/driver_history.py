"""Driver feature history store for online serve parity with offline CPE models.

Definitions:
- past_trips: Cumulative count of completed/scored trips associated with driver_id
  (or fallback spatial h3_cell if driver_id is absent or has no prior history) strictly prior
  to the current pricing evaluation call.
- avg_surge: Cumulative expanding mean of surge_bonus values for prior trips of the driver
  (or fallback cell) strictly prior to the current pricing evaluation call. Returns 0.0
  if past_trips == 0.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional


class DriverHistoryStore:
    """In-memory driver history store with optional SQLite database bootstrap."""

    def __init__(self, db_path: str | Path | None = None):
        self._driver_trips: dict[str, float] = {}
        self._driver_surge_sum: dict[str, float] = {}
        self._cell_trips: dict[str, float] = {}
        self._cell_surge_sum: dict[str, float] = {}
        self.db_path = db_path
        if db_path and Path(db_path).exists():
            self.bootstrap_from_db(db_path)

    def bootstrap_from_db(self, db_path: str | Path) -> None:
        """Bootstrap driver and cell history from simulation_analytics SQLite table if present.

        If spatial columns (h3_cell or h3_index) exist in simulation_analytics, cell history
        is also populated. If missing, cell history bootstrap is skipped gracefully.
        """
        path = Path(db_path)
        if not path.exists():
            return
        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='simulation_analytics'"
            )
            if not cursor.fetchone():
                conn.close()
                return

            cursor.execute("PRAGMA table_info(simulation_analytics)")
            columns = [row[1] for row in cursor.fetchall()]

            has_driver = "driver_id" in columns
            h3_col = None
            if "h3_cell" in columns:
                h3_col = "h3_cell"
            elif "h3_index" in columns:
                h3_col = "h3_index"

            if not has_driver and not h3_col:
                conn.close()
                return

            select_cols = []
            if has_driver:
                select_cols.append("driver_id")
            else:
                select_cols.append("NULL as driver_id")

            select_cols.append("surge_bonus")

            if h3_col:
                select_cols.append(h3_col)
            else:
                select_cols.append("NULL as h3_cell")

            query = f"""
                SELECT {", ".join(select_cols)}
                FROM simulation_analytics
                ORDER BY timestamp ASC
            """
            rows = cursor.fetchall()
            conn.close()

            for driver_id, surge_bonus, h3_cell in rows:
                s_bonus = float(surge_bonus or 0.0)
                if driver_id is not None:
                    d_id = str(driver_id)
                    self._driver_trips[d_id] = self._driver_trips.get(d_id, 0.0) + 1.0
                    self._driver_surge_sum[d_id] = self._driver_surge_sum.get(d_id, 0.0) + s_bonus

                if h3_cell is not None:
                    c_id = str(h3_cell)
                    self._cell_trips[c_id] = self._cell_trips.get(c_id, 0.0) + 1.0
                    self._cell_surge_sum[c_id] = self._cell_surge_sum.get(c_id, 0.0) + s_bonus

            if not h3_col:
                print(
                    "[DriverHistoryStore] Bootstrap note: No spatial column (h3_cell/h3_index) "
                    "found in simulation_analytics. Cell stats remain runtime-only."
                )
        except Exception as e:
            print(f"[DriverHistoryStore] Bootstrap warning: {e}")

    def get_driver_features(
        self, driver_id: Optional[str], h3_cell: Optional[str] = None
    ) -> tuple[float, float]:
        """Return (past_trips, avg_surge) for a driver or fallback cell aggregates.

        Definitions:
        - past_trips: Cumulative count of completed/scored trips associated with driver_id
          (or fallback spatial h3_cell if driver_id is absent or has no prior history) strictly prior
          to the current pricing evaluation call.
        - avg_surge: Cumulative expanding mean of surge_bonus values for prior trips of the driver
          (or fallback cell) strictly prior to the current pricing evaluation call. Returns 0.0
          if past_trips == 0.

        Args:
            driver_id: Unique driver identifier if provided.
            h3_cell: Spatial H3 cell identifier for fallback.

        Returns:
            Tuple of (past_trips: float, avg_surge: float).
        """
        if driver_id:
            d_id = str(driver_id)
            trips = self._driver_trips.get(d_id, 0.0)
            if trips > 0.0:
                surge_sum = self._driver_surge_sum.get(d_id, 0.0)
                avg_surge = (surge_sum / trips) if trips > 0 else 0.0
                return float(trips), float(avg_surge)

        if h3_cell:
            c_id = str(h3_cell)
            trips = self._cell_trips.get(c_id, 0.0)
            if trips > 0.0:
                surge_sum = self._cell_surge_sum.get(c_id, 0.0)
                avg_surge = (surge_sum / trips) if trips > 0 else 0.0
                return float(trips), float(avg_surge)

        return 0.0, 0.0

    def record_trip(
        self, driver_id: Optional[str], surge_bonus: float = 0.0, h3_cell: Optional[str] = None
    ) -> None:
        """Record a completed or scored trip for history tracking."""
        s_bonus = float(surge_bonus)
        if driver_id:
            d_id = str(driver_id)
            self._driver_trips[d_id] = self._driver_trips.get(d_id, 0.0) + 1.0
            self._driver_surge_sum[d_id] = self._driver_surge_sum.get(d_id, 0.0) + s_bonus

        if h3_cell:
            c_id = str(h3_cell)
            self._cell_trips[c_id] = self._cell_trips.get(c_id, 0.0) + 1.0
            self._cell_surge_sum[c_id] = self._cell_surge_sum.get(c_id, 0.0) + s_bonus

