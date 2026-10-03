#!/usr/bin/env python3
# Copyright (C) 2026 Alex Elph
# SPDX-License-Identifier: GPL-3.0-only
"""Фильтр дребезга клавиатуры (антидубль) на уровне evdev.

Захватывает клавиатуру, пробрасывает события через uinput и отсекает ложные
повторные нажатия двумя ступенями:

1. Повтор клавиши раньше --ms после её отпускания — дребезг, отбрасывается сразу.
2. Повтор в окне --ms…--slow-ms той же клавиши, что нажималась последней (без
   других клавиш между), придерживается до --hold-ms. Если за это время клавиша
   отпущена — это дребезг (ложное нажатие держится 30–55 мс против ≥80 мс у
   человека), и нажатие с отпусканием выбрасываются. Если клавиша всё ещё
   удерживается или нажата другая — нажатие отдаётся с задержкой ≤ --hold-ms.

При исчезновении устройства (ENODEV) завершается с кодом 0; повторный запуск
обеспечивает udev-правило /etc/udev/rules.d/60-kbd-debounce.rules.

Использование: kbd-debounce.py [--name "Apple SPI Keyboard"] [--ms 50] [--slow-ms 250] [--hold-ms 55] [--log]
"""

import argparse
import errno
import select
import sys
import time

import evdev
from evdev import InputDevice, InputEvent, UInput, ecodes


def find_device(name_part: str) -> InputDevice | None:
    """Ищет устройство по части имени; исчезнувшие по дороге узлы пропускает."""
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
    ap.add_argument("--name", default="Apple SPI Keyboard", help="часть имени устройства")
    ap.add_argument("--ms", type=float, default=50, help="порог безусловного отсечения, мс")
    ap.add_argument("--slow-ms", type=float, default=250, help="верхняя граница окна подозрительных повторов, мс")
    ap.add_argument("--hold-ms", type=float, default=55, help="сколько придерживать подозрительное нажатие, мс")
    ap.add_argument("--log", action="store_true", help="писать в журнал каждый отброшенный повтор (пауза и удержание)")
    args = ap.parse_args()
    fast = args.ms / 1000.0
    slow = args.slow_ms / 1000.0
    hold = args.hold_ms / 1000.0

    # Ждём появления устройства (при старте на этапе загрузки его может ещё не быть)
    dev = None
    while dev is None:
        dev = find_device(args.name)
        if dev is None:
            time.sleep(2)

    print(f"устройство: {dev.path} ({dev.name}), порог {args.ms} мс, окно {args.slow_ms} мс, удержание {args.hold_ms} мс", flush=True)

    ui = UInput.from_device(dev, name="kbd-debounce")
    dev.grab()

    last_release: dict[int, float] = {}
    suppressed: dict[int, tuple[float, float]] = {}  # отброшенные нажатия (пауза, время нажатия): ждём отпускания
    pending: dict[int, tuple[InputEvent, float]] = {}  # придержанные нажатия: событие и срок
    last_press_code: int | None = None

    def emit(ev: InputEvent) -> None:
        ui.write_event(ev)
        ui.syn()

    def flush_pending() -> None:
        """Отдать все придержанные нажатия как настоящие."""
        for code in list(pending):
            ev, _ = pending.pop(code)
            emit(ev)

    try:
        while True:
            timeout = None
            if pending:
                timeout = max(0.0, min(d for _, d in pending.values()) - time.time())
            ready, _, _ = select.select([dev.fd], [], [], timeout)
            if not ready:
                # Срок вышел, отпускания не было — клавиша удерживается, нажатие настоящее
                now = time.time()
                for code in [c for c, (_, d) in pending.items() if d <= now]:
                    ev, _ = pending.pop(code)
                    emit(ev)
                continue
            for ev in dev.read():
                if ev.type != ecodes.EV_KEY:
                    if ev.type == ecodes.EV_SYN and pending:
                        continue  # пустой отчёт для придержанного события не нужен
                    ui.write_event(ev)
                    continue
                code, value, ts = ev.code, ev.value, ev.timestamp()
                if value == 1:  # нажатие
                    if code in pending:
                        continue  # дубль нажатия без отпускания
                    gap = ts - last_release.get(code, 0.0)
                    if gap < fast:
                        suppressed[code] = (gap, ts)
                        continue
                    if gap < slow and last_press_code == code:
                        pending[code] = (ev, time.time() + hold)
                        continue
                    flush_pending()  # нажата другая клавиша — придержанные были настоящими
                    last_press_code = code
                    ui.write_event(ev)
                elif value == 2:  # автоповтор
                    if code in suppressed or code in pending:
                        continue
                    ui.write_event(ev)
                else:  # отпускание
                    if code in suppressed:
                        gap, pressed = suppressed.pop(code)
                        if args.log:
                            print(f"дребезг {ecodes.KEY.get(code, code)}: пауза {gap*1000:.0f} мс, удержание {(ts-pressed)*1000:.0f} мс (быстрый)", flush=True)
                        continue
                    if code in pending:
                        pev, _ = pending.pop(code)   # быстро отпустили — дребезг, выбрасываем пару
                        if args.log:
                            print(f"дребезг {ecodes.KEY.get(code, code)}: пауза {(pev.timestamp()-last_release.get(code, 0.0))*1000:.0f} мс, удержание {(ts-pev.timestamp())*1000:.0f} мс (придержан)", flush=True)
                        last_release[code] = ts
                        continue
                    last_release[code] = ts
                    ui.write_event(ev)
    except KeyboardInterrupt:
        pass
    except OSError as e:
        # Устройство отключено — например, драйвер applespi перевязан после сна.
        if e.errno != errno.ENODEV:
            raise
        print("устройство отключено, завершаюсь", flush=True)
    finally:
        try:
            dev.ungrab()
        except OSError:
            pass
        ui.close()
        dev.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
