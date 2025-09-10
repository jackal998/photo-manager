from __future__ import annotations

from typing import Iterable, List, Tuple

from core.models import PhotoGroup, PhotoRecord
from core.services.interfaces import ISortService


class SortService(ISortService):
    def sort(self, groups: Iterable[PhotoGroup], sort_keys: List[Tuple[str, bool]]) -> None:
        if not sort_keys:
            return

        def build_key_func(record: PhotoRecord):
            values: list = []
            for field_name, ascending in sort_keys:
                value = getattr(record, field_name, None)
                # Normalize for None to ensure consistent comparisons
                if value is None:
                    value = "" if ascending else chr(0x10FFFF)
                values.append(value)
            return tuple(values)

        for group in groups:
            # Apply multi-key sort; handle ascending per key by transforming values
            # Since Python's sort supports reverse as a single flag, we adapt values
            # by negating numbers for descending and using tuple of adjusted values.
            decorated: list[tuple] = []
            for item in group.items:
                row: list = []
                for field_name, ascending in sort_keys:
                    v = getattr(item, field_name, None)
                    if v is None:
                        v = 0 if isinstance(v, (int, float)) else ""
                    if not ascending:
                        if isinstance(v, (int, float)):
                            v = -v
                        else:
                            # For strings, invert by reversing sort order via tuple (1, str) vs (0, str)
                            v = (1, str(v))
                    else:
                        if not isinstance(v, (int, float)):
                            v = (0, str(v))
                    row.append(v)
                decorated.append((tuple(row), item))
            decorated.sort(key=lambda x: x[0])
            group.items = [it for _, it in decorated]
