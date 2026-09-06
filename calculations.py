#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точные расчёты из даты рождения: Сюцай (психоматрица), личный год,
число пути, фаза возраста. Всё детерминировано — модель не считает сама.
"""

from datetime import date, datetime
from typing import List, Optional

SUQIAN_CELLS = {
    1: 'характер/воля', 2: 'энергия', 3: 'интерес/наука', 4: 'здоровье',
    5: 'логика/интуиция', 6: 'труд/быт', 7: 'удача', 8: 'долг/доброта',
    9: 'память/ум',
}


def parse_birth_date(text: str) -> Optional[date]:
    """ДД.ММ.ГГГГ (разделители . - /). Год только 4-значный."""
    for fmt in ('%d.%m.%Y', '%d-%m-%Y', '%d/%m/%Y'):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _digit_sum(n: int) -> int:
    return sum(int(c) for c in str(abs(n)))


def _reduce(n: int, keep=(11, 22)) -> int:
    while n > 9 and n not in keep:
        n = _digit_sum(n)
    return n


def calc_age(birth: date, today: Optional[date] = None) -> int:
    today = today or date.today()
    return today.year - birth.year - (
        (today.month, today.day) < (birth.month, birth.day))


def calc_psychomatrix(birth: date) -> str:
    """Квадрат Пифагора (Сюцай): 4 рабочих числа, подсчёт цифр 1–9."""
    raw = f"{birth.day:02d}{birth.month:02d}{birth.year:04d}"
    digits = [int(c) for c in raw]

    first = sum(digits)
    second = _digit_sum(first)
    third = first - 2 * int(str(birth.day)[0])
    fourth = _digit_sum(third)

    pool = (digits
            + [int(c) for c in str(first)]
            + [int(c) for c in str(second)]
            + [int(c) for c in str(abs(third))]
            + [int(c) for c in str(fourth)])

    counts = {d: 0 for d in range(1, 10)}
    for d in pool:
        if d:
            counts[d] += 1

    return ", ".join(f"{SUQIAN_CELLS[d]} {counts[d]}" for d in range(1, 10))


def calc_personal_year(birth: date, today: Optional[date] = None) -> int:
    """Цифры дня + месяца рождения + цифры текущего года, к одной цифре."""
    today = today or date.today()
    return _reduce(_reduce(birth.day) + _reduce(birth.month)
                   + _reduce(sum(int(c) for c in str(today.year))))


def calc_path_number(birth: date) -> int:
    return _reduce(sum(int(c) for c in
                       f"{birth.day:02d}{birth.month:02d}{birth.year:04d}"))


def life_stage(age: int) -> str:
    """Фаза возраста — ориентир психологии развития, не диагноз."""
    if age < 18:
        return "детство/юность"
    if age <= 22:
        return "18–22: выбор идентичности — «кто я»"
    if age <= 28:
        return "23–28: поиск опор и своего пути"
    if age <= 32:
        return "29–32: пересборка опор (первый рубеж ~29,5)"
    if age <= 37:
        return "33–37: фаза переоценки — «моё ли это дело»"
    if age <= 42:
        return "38–42: подготовка к середине пути"
    if age <= 46:
        return "43–46: кризис середины — переоценка достижений"
    if age <= 52:
        return "47–52: пересборка смысла"
    if age <= 59:
        return "53–59: зрелость — передача опыта"
    return "60+: наследие и завершение кругов"


def calc_summary(birth_date: str) -> Optional[dict]:
    birth = parse_birth_date(birth_date)
    if not birth:
        return None
    today = date.today()
    age = calc_age(birth, today)
    return {
        'age': age,
        'suqian': calc_psychomatrix(birth),
        'personal_year': calc_personal_year(birth, today),
        'path': calc_path_number(birth),
        'stage': life_stage(age),
    }


def build_calc_lines(birth_date: str) -> List[str]:
    """Готовые строки для системного промта и /profile."""
    s = calc_summary(birth_date)
    if not s:
        return []
    return [
        f"возраст {s['age']}",
        f"Сюцай: {s['suqian']}",
        f"личный год {s['personal_year']}",
        f"число пути {s['path']}",
        f"фаза возраста: {s['stage']}",
    ]
