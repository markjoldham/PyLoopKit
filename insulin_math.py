#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jun 20 10:29:59 2019

@author: annaquinlan

Github URL: https://github.com/tidepool-org/LoopKit/blob/
57a9f2ba65ae3765ef7baafe66b883e654e08391/LoopKit/InsulinKit/InsulinMath.swift
"""
# pylint: disable=R0913, R0914, C0200
from math import floor
from datetime import timedelta, datetime
from date import time_interval_since, time_interval_since_reference_date
from loop_math import simulation_date_range_for_samples
from dose_entry import net_basal_units
from exponential_insulin_model import percent_effect_remaining
from walsh_insulin_model import walsh_percent_effect_remaining

MAXIMUM_RESERVOIR_DROP_PER_MINUTE = 6.5


def dose_entries(reservoir_dates, unit_volumes):
    """ Converts a continuous, chronological sequence of reservoir values
        to a sequence of doses
    Runtime: O(n)

    Arguments:
    reservoir_dates -- list of datetime objects
    unit_volumes -- list of reservoir volumes (in units of insulin)

    Output:
    A tuple of lists in (dose_type (basal/bolus), start_dates, end_dates,
        insulin_values) format
    """
    assert len(reservoir_dates) > 1,\
        "expected input lists to contain two or more items"
    assert len(reservoir_dates) == len(unit_volumes),\
        "expected input shape to match"

    dose_types = []
    start_dates = []
    end_dates = []
    insulin_values = []

    previous_date = reservoir_dates[0]
    previous_unit_volume = unit_volumes[0]

    for i in range(1, len(reservoir_dates)):
        volume_drop = previous_unit_volume - unit_volumes[i]
        duration = time_interval_since(reservoir_dates[i], previous_date)/60

        if (duration > 0 and 0 <= volume_drop <=
                MAXIMUM_RESERVOIR_DROP_PER_MINUTE * duration):
            dose_types.append("tempBasal")
            start_dates.append(previous_date)
            end_dates.append(reservoir_dates[i])
            insulin_values.append(volume_drop)

        previous_date = reservoir_dates[i]
        previous_unit_volume = unit_volumes[i]

    assert len(dose_types) == len(start_dates) == len(end_dates) ==\
        len(insulin_values), "expected output shape to match"

    return (dose_types, start_dates, end_dates, insulin_values)


def is_continuous(reservoir_dates, unit_volumes, start, end,
                  maximum_duration):
    """ Whether a span of chronological reservoir values is considered
        continuous and therefore reliable.

    Reservoir values of 0 are automatically considered unreliable due to
    the assumption that an unknown amount of insulin can be delivered after
    the 0 marker.

    Arguments:
    reservoir_dates -- list of datetime objects that correspond by index to
                        unit_volumes
    unit_volumes -- volume of reservoir in units, corresponds by index to
                    reservoir_dates
    start -- datetime object that is start of the interval which to validate
             continuity
    end -- datetime object that is end of the interval which to validate
             continuity
    maximum_duration -- the maximum interval to consider reliable for a
                        reservoir-derived dose

    Variable names:
    start_date -- the beginning of the interval in which to validate
                   continuity
    end_date -- the end of the interval in which to validate continuity

    Outputs:
    Whether the reservoir values meet the critera for continuity
    """
    try:
        first_date_value = reservoir_dates[0]
        first_volume_value = unit_volumes[0]
    except IndexError:
        return False

    start_date = start
    # The first value has to be at least as old as the start date
    # as a reference point.
    if first_date_value > start_date:
        return False

    last_date_value = first_date_value
    last_volume_value = first_volume_value

    for i in range(0, len(unit_volumes)):  # pylint: disable=C0200
        # Volume and interval validation only applies for values in
        # the specified range
        if reservoir_dates[i] < start_date or reservoir_dates[i] > end:
            last_date_value = reservoir_dates[i]
            last_volume_value = unit_volumes[i]
            continue
        # We can't trust 0. What else was delivered?
        if unit_volumes[i] <= 0:
            return False
        # Rises in reservoir volume indicate a rewind + prime, and primes
        # can be easily confused with boluses.
        # Small rises (1 U) can be ignored as they're indicative of a
        # mixed-precision sequence.
        if unit_volumes[i] > last_volume_value + 1:
            return False
        # Ensure no more than the maximum interval has passed
        if (time_interval_since(reservoir_dates[i], last_date_value)/60
                > maximum_duration):
            return False

        last_date_value = reservoir_dates[i]
        last_volume_value = unit_volumes[i]

    return True


def annotated(dose_types, start_dates, end_dates, values,
              scheduled_basal_rates, basal_start_times, basal_rates,
              basal_minutes, convert_to_units_hr=False):
    """ Annotates doses with the context of the scheduled basal rates

    Arguments:
    basal_start_times -- list of times the basal rates start at
    basal_rates -- list of basal rates(U/hr)
    basal_minutes -- list of basal lengths (in mins)
    dose_start_date -- start date of the range (datetime obj)
    dose_end_date -- end date of the range (datetime obj)

    Output:
    5 lists of annotated dose properties
    """
    assert len(dose_types) == len(start_dates) == len(end_dates) ==\
        len(values) == len(scheduled_basal_rates),\
        "expected input shapes to match"

    assert len(basal_start_times) == len(basal_rates) == len(basal_minutes),\
        "expected input shapes to match"

    if not dose_types or not basal_start_times:
        return ([], [], [], [], [])

    output_types = []
    output_start_dates = []
    output_end_dates = []
    output_values = []
    output_scheduled_basal_rates = []

    for i in range(0, len(dose_types)):
        (dose_type, start_date, end_date, value, scheduled_basal_rate) =\
            annotate_individual_dose(
                dose_types[i], start_dates[i], end_dates[i], values[i],
                basal_start_times, basal_rates, basal_minutes,
                convert_to_units_hr)

        output_types.extend(dose_type)
        output_start_dates.extend(start_date)
        output_end_dates.extend(end_date)
        output_values.extend(value)
        output_scheduled_basal_rates.extend(scheduled_basal_rate)

    assert len(output_types) == len(output_start_dates) ==\
        len(output_end_dates) == len(output_values) ==\
        len(output_scheduled_basal_rates), "expected output shapes to match"

    return (output_types, output_start_dates, output_end_dates, output_values,
            output_scheduled_basal_rates)


def annotate_individual_dose(dose_type, dose_start_date, dose_end_date, value,
                             basal_start_times, basal_rates, basal_minutes,
                             convert_to_units_hr=False):
    """ Annotates a dose with the context of the scheduled basal rate
        If the dose crosses a schedule boundary, it will be split into
        multiple doses so each dose has a single scheduled basal rate.

        * basal "value" MUST be in units/hr! *

    Arguments:
    dose_type -- type of dose (basal, bolus, etc)
    dose_start_date -- start date of the dose (datetime obj)
    dose_end_date -- end date of the dose (datetime obj)
    value -- actual basal rate of dose in U/hr (if a basal)
             or the value of the bolus in U
    basal_start_times -- list of times the basal rates start at
    basal_rates -- list of basal rates(U/hr)
    basal_minutes -- list of basal lengths (in mins)


    Output:
    Tuple in format (basal_start_times, basal_rates, basal_minutes) within
    the range of dose_start_date and dose_end_date
    """
    if dose_type not in ["Basal", "TempBasal"]:
        return ([dose_type], [dose_start_date], [dose_end_date], [value],
                [0])

    output_types = []
    output_start_dates = []
    output_end_dates = []
    output_values = []
    output_scheduled_basal_rates = []

    # these are the lists containing the scheduled basal value(s) within
    # the temp basal's duration
    (sched_basal_starts, sched_basal_ends, sched_basal_rates) = between(
        basal_start_times, basal_rates, basal_minutes, dose_start_date,
        dose_end_date)

    for i in range(0, len(sched_basal_starts)):
        if i == 0:
            start_date = dose_start_date
        else:
            start_date = sched_basal_starts[i]

        if i == len(sched_basal_starts) - 1:
            end_date = dose_end_date
        else:
            end_date = sched_basal_starts[i+1]

        output_types.append(dose_type)
        output_start_dates.append(start_date)
        output_end_dates.append(end_date)

        if convert_to_units_hr:
            output_values.append(
                value / (time_interval_since(
                    dose_end_date, dose_start_date)/60/60))
        else:
            output_values.append(value)
        output_scheduled_basal_rates.append(sched_basal_rates[i])

    assert len(output_types) == len(output_start_dates) ==\
        len(output_end_dates) == len(output_values) ==\
        len(output_scheduled_basal_rates), "expected output shapes to match"
    return (output_types, output_start_dates, output_end_dates, output_values,
            output_scheduled_basal_rates)


def between(basal_start_times, basal_rates, basal_minutes, dose_start_date,
            dose_end_date, repeat_interval=24):
    """ Returns a slice of scheduled basal rates that occur between two dates

    Arguments:
    basal_start_times -- list of times the basal rates start at
    basal_rates -- list of basal rates(U/hr)
    basal_minutes -- list of basal lengths (in mins)
    dose_start_date -- start date of the range (datetime obj)
    dose_end_date -- end date of the range (datetime obj)

    Output:
    Tuple in format (basal_start_times, basal_rates, basal_minutes) within
    the range of dose_start_date and dose_end_date
    """

    if dose_start_date > dose_end_date:
        return ([], [], [])

    reference_time_interval = timedelta(
        hours=basal_start_times[0].hour, minutes=basal_start_times[0].minute,
        seconds=basal_start_times[0].second)
    max_time_interval = reference_time_interval + timedelta(
        hours=repeat_interval)

    start_offset = schedule_offset(dose_start_date, basal_start_times[0])
    end_offset = start_offset + timedelta(seconds=time_interval_since(
        dose_end_date, dose_start_date))

    if end_offset > max_time_interval:
        boundary_date = dose_start_date + (max_time_interval - start_offset)
        (start_times_1, end_times_1, basal_rates_1) = between(
            basal_start_times, basal_rates, basal_minutes, dose_start_date,
            boundary_date)
        (start_times_2, end_times_2, basal_rates_2) = between(
            basal_start_times, basal_rates, basal_minutes, boundary_date,
            dose_end_date)

        return (start_times_1 + start_times_2,
                end_times_1 + end_times_2,
                basal_rates_1 + basal_rates_2)

    start_index = 0
    end_index = len(basal_start_times)

    for (i, start_time) in enumerate(basal_start_times):
        start_time = timedelta(
            hours=start_time.hour,
            minutes=start_time.minute,
            seconds=start_time.second)
        if start_offset >= start_time:
            start_index = i
        if end_offset < start_time:
            end_index = i
            break

    reference_date = dose_start_date - start_offset

    if start_index > end_index:
        return ([], [], [])

    (output_start_times, output_end_times, output_basal_rates) = ([], [], [])

    for i in range(start_index, end_index):
        end_time = timedelta(
            hours=basal_start_times[i+1].hour,
            minutes=basal_start_times[i+1].minute,
            seconds=basal_start_times[i+1].second) if i+1 <\
            len(basal_start_times) else max_time_interval

        output_start_times.append(reference_date + timedelta(
            hours=basal_start_times[i].hour,
            minutes=basal_start_times[i].minute,
            seconds=basal_start_times[i].second))

        output_end_times.append(reference_date + end_time)
        output_basal_rates.append(basal_rates[i])

    assert len(output_start_times) == len(output_end_times) ==\
        len(output_basal_rates), "expected output shape to match"

    return (output_start_times, output_end_times, output_basal_rates)


def schedule_offset(date_to_offset, reference_time,
                    repeat_interval=24):
    """ Returns the time interval for a given date normalized to the span of
        the schedule items

    Arguments:
    date_to_offset -- datetime object of the date to convert
    reference_time -- time object that's normally the first basal dose time in
                      a basal schedule
    repeat_interval -- the interval with which the basal schedule repeats

    Output:
    datetime timedelta object representing offset
    """
    reference_time_seconds = (reference_time.hour * 3600 +
                              reference_time.minute * 60 +
                              reference_time.second)
    interval = time_interval_since_reference_date(date_to_offset)

    return timedelta(seconds=(interval-reference_time_seconds) %
                     (repeat_interval * 60 * 60) + reference_time_seconds)


def insulin_on_board(dose_types, start_dates, end_dates, values,
                     scheduled_basal_rates, model, start=None, end=None,
                     delay=10, delta=5):
    """ Calculates the timeline of insulin remaining for a collection of doses

        This model allows us to specify time of peak activity, as well as
        duration, and provides activity and IOB decay functions

    Arguments:
    dose_types -- list of types of doses (basal, bolus, etc)
    start_dates -- list of datetime objects representing the dates
                   the doses started at
    end_dates -- list of datetime objects representing the dates
                   the doses ended at
    values -- list of insulin values for doses
    scheduled_basal_rates -- basal rates scheduled during the times of doses
    model -- list of insulin model parameters in format [DIA, peak_time]
    start -- datetime object of time to start calculating the IOB timeline
    end -- datetime object of time to end the IOB timeline
    delay -- the time to delay the dose effect
    delta -- the differential between timeline entries

    Output:
    Tuple in format (times_iob_was_calculated_at, iob_values (U of insulin))
    """
    assert len(dose_types) == len(start_dates) == len(end_dates) ==\
        len(values) == len(scheduled_basal_rates),\
        "expected input shapes to match"

    if not dose_types:
        return ([], [])

    try:
        if len(model) == 1:
            (start, end) = simulation_date_range_for_samples(
                start_times=start_dates, end_times=end_dates,
                duration=model[0]*60, delay=delay, delta=delta)
        else:
            (start, end) = simulation_date_range_for_samples(
                start_times=start_dates, end_times=end_dates,
                duration=model[0], delay=delay, delta=delta)
    except IndexError:
        return ([], [])

    date = start
    iob_dates = []
    iob_values = []

    def find_partial_iob(i):
        return insulin_on_board_calc(
            dose_types[i], start_dates[i], end_dates[i], values[i],
            scheduled_basal_rates[i], date, model, delay, delta)

    while date <= end:

        iob_sum = 0
        for i in range(0, len(start_dates)):
            iob_sum += find_partial_iob(i)

        iob_dates.append(date)
        iob_values.append(iob_sum)
        date += timedelta(minutes=delta)

    assert len(iob_dates) == len(iob_values)
    return (iob_dates, iob_values)


# date is time calc-ed at, start_date+end_date are props of dose
def insulin_on_board_calc(type_, start_date, end_date, value,
                          scheduled_basal_rate, date, model, delay, delta):
    """ Calculates the insulin on board for a specific dose at a specific time

    Arguments:
    type_ -- String with type of dose ("Bolus" or "TempBasal")
    start_date -- the date the dose started at (datetime object)
    end_date -- the date the dose ended at (datetime object)
    value -- insulin value for dose
    scheduled_basal_rate -- basal rate scheduled during the times of dose
                            (0 for a bolus)
    date -- date the IOB is being calculated (datetime object)
    model -- list of insulin model parameters in format [DIA, peak_time]
    delay -- the time to delay the dose effect
    delta -- the differential between timeline entries

    Output:
    IOB at date
    """
    time = time_interval_since(date, start_date)/60
    if time < 0:
        return 0
    if len(model) == 1:  # walsh model
        if time_interval_since(end_date, start_date) <= 1.05 * delta:
            return net_basal_units(type_, value, start_date, end_date,
                                   scheduled_basal_rate) *\
                    walsh_percent_effect_remaining((time - delay), model[0])
        # This will normally be for basals
        return net_basal_units(type_, value, start_date, end_date,
                               scheduled_basal_rate) *\
            continuous_delivery_insulin_on_board(start_date, end_date,
                                                 date, model, delay, delta)

    # Consider doses within the delta time window as momentary
    # This will normally be for boluses
    if time_interval_since(end_date, start_date)/60 <= 1.05 * delta:
        return net_basal_units(type_, value, start_date, end_date,
                               scheduled_basal_rate) *\
                percent_effect_remaining((time - delay), model[0], model[1])
    # This will normally be for basals
    return net_basal_units(type_, value, start_date, end_date,
                           scheduled_basal_rate) *\
        continuous_delivery_insulin_on_board(start_date, end_date,
                                             date, model, delay, delta)


def continuous_delivery_insulin_on_board(start_date, end_date, at_date,
                                         model, delay, delta):
    """ Calculates the percent of original insulin that is still on board
         at a specific time for a dose given over a period greater than
         1.05x the delta (this will almost always be a basal)

    Arguments:
    start_date -- the date the dose started at (datetime object)
    end_date -- the date the dose ended at (datetime object)
    at_date -- date the IOB is being calculated (datetime object)
    model -- list of insulin model parameters in format [DIA, peak_time]
    delay -- the time to delay the dose effect
    delta -- the differential between timeline entries

    Output:
    Percentage of insulin remaining at the at_date
    """
    dose_duration = time_interval_since(end_date, start_date)/60
    time = time_interval_since(at_date, start_date)/60
    iob = 0
    dose_date = 0

    while (dose_date <= min(floor((time + delay) / delta)
                            * delta, dose_duration)):
        if dose_duration > 0:
            segment = (max(0, min(dose_date + delta, dose_duration)
                           - dose_date) / dose_duration)
        else:
            segment = 1
        if len(model) == 1:  # if walsh model
            iob += segment * walsh_percent_effect_remaining(
                (time - delay - dose_date), model[0])
        else:
            iob += segment * percent_effect_remaining(
                (time - delay - dose_date), model[0], model[1])
        dose_date += delta

    return iob


def glucose_effects(dose_types, dose_start_dates, dose_end_dates, dose_values,
                    scheduled_basal_rates, model, sensitivity_start_times,
                    sensitivity_end_times, sensitivity_values, delay=10,
                    delta=5):
    """ Calculates the timeline of glucose effects for a collection of doses

    Arguments:
    dose_types -- list of types of doses (basal, bolus, etc)
    dose_start_dates -- list of datetime objects representing the dates
                       the doses started at
    dose_end_dates -- list of datetime objects representing the dates
                       the doses ended at
    dose_values -- list of insulin values for doses
    scheduled_basal_rates -- basal rates scheduled during the times of doses
    model -- list of insulin model parameters in format [DIA, peak_time]
    sensitivity_start_times -- list of time objects of start times of
                               given insulin sensitivity values
    sensitivity_end_times -- list of time objects of start times of
                             given insulin sensitivity values
    sensitivity_values -- list of sensitivities (mg/dL/U)
    delay -- the time to delay the dose effect
    delta -- the differential between timeline entries

    Output:
    Tuple in format (times_glucose_effect_was_calculated_at,
                     glucose_effect_values (mg/dL))
    """
    assert len(dose_types) == len(dose_start_dates) == len(dose_end_dates)\
        == len(dose_values) == len(scheduled_basal_rates),\
        "expected input shapes to match"

    if not dose_types:
        return ([], [])

    if len(model) == 1:
        (start, end) = simulation_date_range_for_samples(
            start_times=dose_start_dates, end_times=dose_end_dates,
            duration=model[0]*60, delay=delay, delta=delta)
    else:
        (start, end) = simulation_date_range_for_samples(
            start_times=dose_start_dates, end_times=dose_end_dates,
            duration=model[0], delay=delay, delta=delta)

    date = start
    effect_dates = []
    effect_values = []

    def find_partial_effect(i):
        sensitivity = find_sensitivity_at_time(
            sensitivity_start_times, sensitivity_end_times,
            sensitivity_values, dose_start_dates[i])
        return glucose_effect(
            dose_types[i], dose_start_dates[i], dose_end_dates[i],
            dose_values[i], scheduled_basal_rates[i], date, model,
            sensitivity, delay, delta)

    while date <= end:

        effect_sum = 0
        for i in range(0, len(dose_start_dates)):
            effect_sum += find_partial_effect(i)

        effect_dates.append(date)
        effect_values.append(effect_sum)
        date += timedelta(minutes=delta)

    assert len(effect_dates) == len(effect_values),\
        "expected output shapes to match"
    return (effect_dates, effect_values)


def find_sensitivity_at_time(sensitivity_start_times, sensitivity_end_times,
                             sensitivity_values, time_to_check):
    """ Finds sensitivity setting value at a given time

    Arguments:
    sensitivity_start_times -- list of time objects of start times of
                               given insulin sensitivity values
    sensitivity_end_times -- list of time objects of start times of
                             given insulin sensitivity values
    sensitivity_values -- list of sensitivities (mg/dL/U)
    time_to_check -- finding the sensitivity value at this time

    Output:
    Sensitivity value (mg/dL/U)
    """
    assert len(sensitivity_start_times) == len(sensitivity_end_times) ==\
        len(sensitivity_values), "expected input shapes to match"
    for i in range(0, len(sensitivity_start_times)):
        if is_time_between(sensitivity_start_times[i],
                           sensitivity_end_times[i], time_to_check):
            return sensitivity_values[i]
    return None


def is_time_between(start, end, time_to_check):
    """ Check if time is within an interval

    Arguments:
    start -- time (or datetime) of start of interval
    end -- time (or datetime) of end of interval
    time_to_check -- see if this time (or datetime) value is within the
                     interval

    Output:
    True if within interval, False if not
    """
    # convert from datetime to time if needed so we can compare
    if isinstance(start, datetime):
        start = start.time()
    if isinstance(end, datetime):
        end = end.time()
    if isinstance(time_to_check, datetime):
        time_to_check = time_to_check.time()

    if start < end:
        return start <= time_to_check <= end
    # if it crosses midnight
    return time_to_check >= start or time_to_check <= end


def glucose_effect(dose_type, dose_start_date, dose_end_date, dose_value,
                   scheduled_basal_rate, date, model,
                   insulin_sensitivity, delay, delta):
    """ Calculates the timeline of glucose effects for a specific dose

    Arguments:
    dose_type -- types of dose (basal, bolus, etc)
    dose_start_date -- datetime object representing date doses start at
    dose_end_date -- datetime object representing date dose ended at
    dose_value -- insulin value for dose
    scheduled_basal_rate -- basal rate scheduled during the time of dose
    date -- datetime object of time to calculate the effect at
    insulin_sensitivity -- sensitivity (mg/dL/U)
    delay -- the time to delay the dose effect
    delta -- the differential between timeline entries

    Output:
    Glucose effect (mg/dL)
    """
    time = time_interval_since(date, dose_start_date)/60
    if time < 0:
        return 0
    # Consider doses within the delta time window as momentary
    # This will normally be for boluses
    if time_interval_since(dose_end_date, dose_start_date)/60 <= 1.05 * delta:

        if len(model) == 1:  # walsh model
            return net_basal_units(dose_type, dose_value, dose_start_date,
                                   dose_end_date, scheduled_basal_rate) *\
                -insulin_sensitivity * (1 - walsh_percent_effect_remaining(
                    (time - delay), model[0]))

        return net_basal_units(dose_type, dose_value, dose_start_date,
                               dose_end_date, scheduled_basal_rate) *\
            -insulin_sensitivity * (1 - percent_effect_remaining(
                (time - delay), model[0], model[1]))
    # This will normally be for basals, and handles Walsh model automatically
    return net_basal_units(dose_type, dose_value, dose_start_date,
                           dose_end_date, scheduled_basal_rate) *\
        -insulin_sensitivity * continuous_delivery_glucose_effect(
            dose_start_date, dose_end_date, date, model, delay, delta)


def continuous_delivery_glucose_effect(dose_start_date, dose_end_date, at_date,
                                       model, delay, delta):
    """ Calculates the percent of glucose effect at a specific time for
        a dose given over a period greater than 1.05x the delta
        (this will almost always be a basal)

    Arguments:
    dose_start_date -- the date the dose started at (datetime object)
    dose_end_date -- the date the dose ended at (datetime object)
    at_date -- date the IOB is being calculated (datetime object)
    model -- list of insulin model parameters in format [DIA, peak_time]
    delay -- the time to delay the dose effect
    delta -- the differential between timeline entries

    Output:
    Percentage of insulin remaining at the at_date
    """
    dose_duration = time_interval_since(dose_end_date, dose_start_date)/60
    time = time_interval_since(at_date, dose_start_date)/60
    activity = 0
    dose_date = 0

    while (dose_date <= min(floor((time + delay) / delta)
                            * delta, dose_duration)):
        if dose_duration > 0:
            segment = (max(0, min(dose_date + delta, dose_duration)
                           - dose_date) / dose_duration)
        else:
            segment = 1

        if len(model) == 1:  # if walsh model
            activity += segment * (1 - walsh_percent_effect_remaining(
                (time - delay - dose_date), model[0]))

        else:
            activity += segment * (1 - percent_effect_remaining(
                (time - delay - dose_date), model[0], model[1]))
        dose_date += delta
    return activity
