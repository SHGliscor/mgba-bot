#!/usr/bin/env python3
"""Ruby v1.1 RTC diagnostics for Pokebot3DS-CFW."""

S_ERROR_STATUS = 0x03000458
S_RTC = 0x03000460
S_PROBE_RESULT = 0x0300046C
G_RNG_VALUE = 0x03004818

def bcd_to_int(value):
    hi = (value >> 4) & 0xF
    lo = value & 0xF
    if hi > 9 or lo > 9:
        raise ValueError(f"invalid BCD byte 0x{value:02X}")
    return hi * 10 + lo

def is_leap_year(year):
    return (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)

def ruby_rev1_day_count(year, month, day):
    days_in_month = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    count = 0

    # Match the original Ruby/Sapphire Berry Glitch-era routine:
    # for (i = year - 1; i > 0; i--)
    for i in range(year - 1, 0, -1):
        count += 365
        if is_leap_year(i):
            count += 1

    for i in range(month - 1):
        count += days_in_month[i]

    if month > 2 and is_leap_year(year):
        count += 1

    count += day
    return count

def ruby_rev1_seed_from_raw_rtc(raw):
    if len(raw) < 8:
        raise ValueError("RTC snapshot must contain at least 8 bytes")

    year = bcd_to_int(raw[0])
    month = bcd_to_int(raw[1])
    day = bcd_to_int(raw[2])
    day_of_week = bcd_to_int(raw[3])
    hour = bcd_to_int(raw[4] & 0x3F)
    minute = bcd_to_int(raw[5])
    second = bcd_to_int(raw[6])
    status = raw[7]

    day_count = ruby_rev1_day_count(year, month, day)

    # Match pokeruby exactly. RtcGetMinuteCount converts the date through
    # RtcGetDayCount, but then adds sRtc.hour and sRtc.minute directly.
    # Those fields are still raw BCD bytes.
    minute_count = (
        1440 * day_count
        + 60 * (raw[4] & 0x3F)
        + raw[5]
    )
    seed = ((minute_count >> 16) ^ (minute_count & 0xFFFF)) & 0xFFFF

    return {
        "year": year,
        "month": month,
        "day": day,
        "day_of_week": day_of_week,
        "hour": hour,
        "minute": minute,
        "second": second,
        "status": status,
        "day_count": day_count,
        "minute_count": minute_count,
        "seed": seed,
        "raw_hex": raw.hex().upper(),
    }

def read_rtc_snapshot(bridge):
    error_status = int.from_bytes(bridge.read(S_ERROR_STATUS, 2), "little")
    raw = bridge.read(S_RTC, 12)
    probe_result = bridge.read(S_PROBE_RESULT, 1)[0]
    rng = int.from_bytes(bridge.read(G_RNG_VALUE, 4), "little")

    result = ruby_rev1_seed_from_raw_rtc(raw)
    result.update({
        "error_status": error_status,
        "probe_result": probe_result,
        "rng": rng,
    })

    result["timestamp"] = (
        f"20{result['year']:02d}-{result['month']:02d}-{result['day']:02d} "
        f"{result['hour']:02d}:{result['minute']:02d}:{result['second']:02d}"
    )

    dummy = (
        result["year"] == 0
        and result["month"] == 1
        and result["day"] == 1
        and result["hour"] == 0
        and result["minute"] == 0
        and result["second"] == 0
    )
    result["dummy_clock"] = dummy
    result["dead_seed"] = dummy and result["seed"] == 0x05A0

    probe_ok = (probe_result & 0x0F) != 0
    result["rtc_ok"] = probe_ok and error_status == 0 and not dummy

    if result["dead_seed"]:
        result["mode"] = "DEAD / DUMMY RTC"
    elif result["rtc_ok"]:
        result["mode"] = "LIVE RTC"
    elif dummy:
        result["mode"] = "RTC ERROR / DUMMY"
    else:
        result["mode"] = "RTC PRESENT (CHECK FLAGS)"

    return result

def rtc_changed(a, b):
    return any(a.get(k) != b.get(k) for k in (
        "year", "month", "day", "hour", "minute", "second"
    ))

def same_rtc_minute(a, b):
    return all(a.get(k) == b.get(k) for k in (
        "year", "month", "day", "hour", "minute"
    ))
