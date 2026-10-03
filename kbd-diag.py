#!/usr/bin/env python3
# Copyright (C) 2026 Alex Elph
# SPDX-License-Identifier: GPL-3.0-only
"""Диагностика дребезга: показывает быстрые повторные нажатия одной клавиши.

Читает устройство (по умолчанию виртуальную клавиатуру kbd-debounce, то есть
то, что уже прошло через фильтр) и печатает интервал «отпускание → повторное
нажатие» той же клавиши, если он короче порога. В конце — сводка по интервалам.

Использование: sudo kbd-diag.py [--name kbd-debounce] [--max-ms 300] [--seconds 30]
"""

import argparse
import sys
import time

import evdev
from evdev import InputDevice, ecodes


def find_device(name_part: str) -> InputDevice | None:
    for path in evdev.list_devices():
        try:
            dev = InputDevice(path)
        except OSError:
            continue
        if name_part.lower() in dev.name.lower() and ecodes.EV_KEY in dev.capabilities():
            return dev
        dev.close()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="kbd-debounce")
    ap.add_argument("--max-ms", type=float, default=300)
    ap.add_argument("--seconds", type=float, default=30)
    args = ap.parse_args()

    dev = find_device(args.name)
    if dev is None:
        print(f"устройство «{args.name}» не найдено", file=sys.stderr)
        return 1
    print(f"слушаю {dev.path} ({dev.name}) {args.seconds:.0f} с — печатайте текст с повторяющимися буквами", flush=True)

    last_release: dict[int, float] = {}
    fast: list[float] = []
    per_key: dict[str, list[float]] = {}
    last_press: dict[int, float] = {}     # время нажатия — для длительности удержания
    pending_fast: dict[int, float] = {}   # повторы, у которых ждём отпускания, чтобы узнать длительность
    durations: list[float] = []           # длительности обычных нажатий
    last_code: int | None = None  # последняя нажатая клавиша: повтор считаем только без других клавиш между
    presses = 0
    deadline = time.monotonic() + args.seconds
    dev.set_blocking(False) if hasattr(dev, "set_blocking") else None
    import select
    while time.monotonic() < deadline:
        r, _, _ = select.select([dev.fd], [], [], 0.5)
        if not r:
            continue
        for ev in dev.read():
            if ev.type != ecodes.EV_KEY:
                continue
            name = ecodes.KEY.get(ev.code, str(ev.code))
            if ev.value == 1:
                presses += 1
                prev = last_release.get(ev.code)
                last_press[ev.code] = ev.timestamp()
                if prev is not None and last_code == ev.code:
                    gap = (ev.timestamp() - prev) * 1000
                    if gap < args.max_ms:
                        fast.append(gap)
                        per_key.setdefault(name, []).append(gap)
                        pending_fast[ev.code] = gap
                last_code = ev.code
            elif ev.value == 0:
                last_release[ev.code] = ev.timestamp()
                if ev.code in last_press:
                    dur = (ev.timestamp() - last_press[ev.code]) * 1000
                    if ev.code in pending_fast:
                        gap = pending_fast.pop(ev.code)
                        print(f"  {name}: повтор через {gap:6.1f} мс, удержание {dur:5.1f} мс", flush=True)
                    else:
                        durations.append(dur)
    dev.close()

    print(f"\nнажатий: {presses}, быстрых повторов (<{args.max_ms:.0f} мс): {len(fast)}")
    if fast:
        buckets = [(0, 50), (50, 80), (80, 120), (120, 200), (200, args.max_ms)]
        for lo, hi in buckets:
            n = sum(1 for g in fast if lo <= g < hi)
            if n:
                print(f"  {lo:3.0f}–{hi:3.0f} мс: {n}")
        print(f"  минимум {min(fast):.1f} мс, максимум {max(fast):.1f} мс")
        if durations:
            durations.sort()
            n = len(durations)
            print(f"удержание обычных нажатий: медиана {durations[n//2]:.0f} мс, 10-й перцентиль {durations[n//10]:.0f} мс, минимум {durations[0]:.0f} мс")
        print("по клавишам:")
        for k, gaps in sorted(per_key.items(), key=lambda kv: -len(kv[1])):
            print(f"  {k}: {len(gaps)} — " + ", ".join(f"{g:.0f}" for g in gaps) + " мс")
    return 0


if __name__ == "__main__":
    sys.exit(main())
