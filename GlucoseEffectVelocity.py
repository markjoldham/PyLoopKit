#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Jun 14 09:23:51 2019

@author: annaquinlan

Github URL: https://github.com/tidepool-org/LoopKit/blob/
57a9f2ba65ae3765ef7baafe66b883e654e08391/LoopKit/GlucoseEffectVelocity.swift
"""


class GlucoseEffectVelocity:
    def __init__(self, start_date, end_date, quantity, unit):
        self.start_date = start_date
        self.end_date = end_date
        self.quantity = quantity
        self.unit = unit
