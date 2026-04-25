#!/usr/bin/env python3
"""Test A4 — replay frame GRIZZLE-001 reelle 2026-04-15."""
import sys; sys.path.insert(0, '.')
from core.charge_point import ChargePoint

sv_list = [
    {'measurand':'Current.Import','unit':'A','value':'23.34','context':'Sample.Periodic'},
    {'measurand':'Voltage','unit':'V','value':'242.00','context':'Sample.Periodic'},
    {'measurand':'Energy.Active.Import.Register','unit':'Wh','value':'1896','context':'Sample.Periodic'},
    {'measurand':'Power.Active.Import','unit':'kW','value':'5.65','context':'Sample.Periodic'},
    {'measurand':'Temperature','unit':'Celsius','value':'29','context':'Sample.Periodic'},
]
result = {}
for sv in sv_list:
    out = ChargePoint._normalize_sample(sv)
    if out:
        field, v, phase = out
        result[field] = v

tests = [
    ('energy_wh',     1896.0,  result.get('energy_wh')),
    ('power_w',       5650.0,  result.get('power_w')),     # kW -> W
    ('current_a',     23.34,   result.get('current_a')),
    ('voltage_v',     242.0,   result.get('voltage_v')),
    ('temperature_c', 29.0,    result.get('temperature_c')),
]
ok = 0
for name, expected, actual in tests:
    status = 'OK' if actual == expected else 'FAIL'
    if status == 'OK': ok += 1
    print(f'  [{status}] {name:15s} = {actual} (attendu {expected})')
print(f'\n{ok}/{len(tests)} tests OK')
sys.exit(0 if ok == len(tests) else 1)
