from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GameTime:
	"""
	与 Godot `GameTime.gd` 对齐的最小时间结构。
	"""

	total_ticks: int = 0

	# --- 常量（与 GDScript 版本一致）---
	TICKS_PER_MINUTE: int = 1
	MINUTES_PER_HOUR: int = 60
	HOURS_PER_DAY: int = 24
	DAYS_PER_WEEK: int = 7
	WEEKS_PER_MONTH: int = 4
	MONTHS_PER_YEAR: int = 12

	@property
	def ticks_per_hour(self) -> int:
		return self.TICKS_PER_MINUTE * self.MINUTES_PER_HOUR

	@property
	def ticks_per_day(self) -> int:
		return self.ticks_per_hour * self.HOURS_PER_DAY

	def advance_ticks(self, ticks_to_add: int) -> bool:
		old_day = self.total_ticks // self.ticks_per_day
		self.total_ticks += int(ticks_to_add)
		new_day = self.total_ticks // self.ticks_per_day
		return new_day > old_day

	def advance_minutes(self, minutes_to_add: int) -> bool:
		return self.advance_ticks(int(minutes_to_add) * self.TICKS_PER_MINUTE)

	def get_year(self) -> int:
		den = self.ticks_per_day * self.DAYS_PER_WEEK * self.WEEKS_PER_MONTH * self.MONTHS_PER_YEAR
		return 1 + (self.total_ticks // den)

	def get_month(self) -> int:
		den_year = self.ticks_per_day * self.DAYS_PER_WEEK * self.WEEKS_PER_MONTH * self.MONTHS_PER_YEAR
		den_month = self.ticks_per_day * self.DAYS_PER_WEEK * self.WEEKS_PER_MONTH
		ticks_in_year = self.total_ticks % den_year
		return 1 + (ticks_in_year // den_month)

	def get_day_of_month(self) -> int:
		# 假设存在：更精确的日历计算（考虑月/周边界）
		# 用意：给 UI/日志提供准确日期；必要性：目前仅用于调试输出，MVP 可先简化
		return 1

	def get_hour(self) -> int:
		ticks_in_day = self.total_ticks % self.ticks_per_day
		return ticks_in_day // self.ticks_per_hour

	def get_minute(self) -> int:
		ticks_in_day = self.total_ticks % self.ticks_per_day
		ticks_in_hour = ticks_in_day % self.ticks_per_hour
		return ticks_in_hour // self.TICKS_PER_MINUTE

	def time_to_string(self) -> str:
		return "Year %d, Month %d, Day %d, %02d:%02d" % (
			self.get_year(),
			self.get_month(),
			self.get_day_of_month(),
			self.get_hour(),
			self.get_minute(),
		)

