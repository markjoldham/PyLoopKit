#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 11 14:53:05 2019

@author: ed
"""
import os
import datetime
import pandas as pd
import numpy as np
from input_data_tools import dict_inputs_to_dataframes, input_table_to_dict
from loop_data_manager import update
import plotly.graph_objs as go
from plotly.offline import plot


# %% functions
def prepare_bg(df, current_time):

    df_trace = go.Scattergl(
        name="bg",
        x=df["glucose_dates"],
        y=df["glucose_values"],
        hoverinfo="y+name",
        mode='markers',
        marker=dict(
            size=6,
            line=dict(width=0),
            color="#9886CF"
        )
    )

    df_axis = dict(
            domain=[0.5, 1],
            range=[0, 400],
            tickvals=[-100, 40, 70, 180, 250, 400],
            fixedrange=True,
            hoverformat=".0f",
            zeroline=False,
            showgrid=True,
            gridcolor="#c0c0c0",
            title=dict(
                text="Blood Glucose (mg/dL)",
                font=dict(
                    size=11
                )
            )
        )

    df_annotations = go.layout.Annotation(
        x=current_time,
        y=df["glucose_values"].values[-1] + 10,
        xref="x",
        yref="y2",
        text="evaluation point",
        showarrow=True,
        arrowhead=1,
        ax=0,
        ayref="y2",
        ay=425
    )

    df_trace.yaxis = "y2"

    return df, df_trace, df_axis, df_annotations


def create_contiguous_ts(date_min, date_max, freq="1s"):
    date_range = pd.date_range(
        date_min,
        date_max,
        freq=freq
    )

    contig_ts = pd.DataFrame(date_range, columns=["datetime"])
    contig_ts["time"] = contig_ts["datetime"].dt.time

    return contig_ts


def convert_times_and_types(obj):
    """ Convert dose types into strings """
    return str(obj.name)


def downsample(df, current_time, freq="5min"):
    df_range = pd.date_range(
        df["datetime"].min(),
        current_time,
        freq=freq
    )

    df = pd.merge(
        pd.DataFrame(df_range, columns=["datetime"]),
        df,
        on="datetime",
        how="left"
    )

    # add back in the current time
    last_index = df.index.max()
    if df.loc[last_index, "datetime"] != current_time:
        df.loc[last_index + 1, :] = df.loc[last_index, :]
        df.loc[last_index + 1, "datetime"] = current_time

    return df


def make_basal_traces(df, value_name, trace_name):
    if "scheduled" in trace_name:
        dash = "dot"
        width = 1
        fill = 'none'
        opacity = 0.75
    else:
        dash = "solid"
        width = 0
        fill = 'tozeroy'
        opacity = 0.25

    b_traces = []
    for i in range(0, len(df)):

        if i < len(df)-1:
            legend_on = False
            x_vals = [
                df["datetime"][i],
                df["datetime"][i+1],
                df["datetime"][i+1],
            ]

            y_vals = [
                df[value_name][i],
                df[value_name][i],
                df[value_name][i+1],
            ]

        else:
            legend_on = True
            x_vals = [
                df["datetime"][i],
            ]

            y_vals = [
                df[value_name][i],
            ]

        tmp_trace = go.Scatter(
            name=trace_name,
            legendgroup=trace_name,
            showlegend=legend_on,
            mode='lines',
            x=x_vals,
            y=y_vals,
            hoverinfo="none",
            line=dict(
                shape='vh',
                color='#5691F0',
                dash=dash,
                width=width,
            ),
            fill=fill,
            fillcolor='rgba(86,145,240,{})'.format(opacity)
        )

        tmp_trace.yaxis = "y"

        b_traces.append(tmp_trace)

    return b_traces


def prepare_basal(basal_rates, df_dose, contig_ts, current_time):
    unique_dose_types = df_dose["type"].unique()
    df = pd.merge(
        contig_ts,
        basal_rates,
        left_on="time",
        right_on="basal_rate_start_times",
        how="left"
    )

    df["basal_rate_values"].fillna(method='ffill', inplace=True)
    df.dropna(subset=['basal_rate_values'], inplace=True)

    # temp basal data
    if (("basal" in unique_dose_types) | ("suspend" in unique_dose_types)):
        temp_basal = (
            df_dose[
                ((df_dose["type"] == "basal") | (df_dose["type"] == "suspend"))
            ].copy()
        )

        temp_basal["type"].replace("basal", "temp", inplace=True)
        all_temps = pd.DataFrame()
        for idx in temp_basal.index:
            rng = pd.date_range(
                temp_basal.loc[idx, "dose_start_times"],
                (
                    temp_basal.loc[idx, "dose_end_times"]
                    - datetime.timedelta(seconds=1)
                ),
                freq="1s"
            )
            temp_ts = pd.DataFrame(rng, columns=["datetime"])
            temp_ts["tbr"] = temp_basal.loc[idx, "dose_values"]
            temp_ts["type"] = temp_basal.loc[idx, "type"]
            all_temps = pd.concat([all_temps, temp_ts])

        df = pd.merge(df, all_temps, on="datetime", how="left")
        df["type"].fillna("scheduled", inplace=True)

    else:
        df["tbr"] = np.nan

    df["delivered"] = df["tbr"]
    df.loc[df["delivered"].isnull(), "delivered"] = (
        df.loc[df["delivered"].isnull(), "basal_rate_values"]
    )

    # preapre scheduled basal rate
    sbr_df = df[df["basal_rate_start_times"].notnull()].copy()
    sbr_df.reset_index(drop=True, inplace=True)

    sbr_traces = (
        make_basal_traces(sbr_df, "basal_rate_values", "scheduled basal rate")
    )

    # prepare basal delivered
    basal_df = df[df["datetime"] <= current_time].copy()
    basal_df["transition"] = (
        basal_df["delivered"] != basal_df["delivered"].shift(1)
    )
    basal_df.loc[basal_df.index.max(), "transition"] = True
    basal_df = basal_df[basal_df["transition"]]
    basal_df.reset_index(drop=True, inplace=True)

    basal_traces = (
        make_basal_traces(basal_df, "delivered", "basal delivered")
    )

    return basal_df, sbr_df, basal_traces, sbr_traces


def prepare_bolus(df_dose):
    df = df_dose[df_dose["type"] == "bolus"]

    df_trace = go.Scatter(
        name="bolus",
        showlegend=True,
        mode='markers',
        x=df["dose_start_times"],
        y=df["dose_values"],
        hoverinfo="y+name",
        marker=dict(
            symbol='triangle-down',
            size=15 + df["dose_values"],
            color="#5691F0"
        ),
    )

    df_trace.yaxis = "y"

    return df, df_trace


def prepare_carbs(df_events, df_ratios, continguous_ts):
    carb_df = pd.merge(
        continguous_ts,
        df_ratios,
        left_on="time",
        right_on="carb_ratio_start_times",
        how="left"
    )
    carb_df["carb_ratio_values"].fillna(method='ffill', inplace=True)
    carb_df.dropna(subset=['carb_ratio_values'], inplace=True)

    # df events
    carb_df = pd.merge(
        carb_df,
        df_events,
        left_on="datetime",
        right_on="carb_dates",
        how="left",
    )

    # add bolus height for figure
    carb_df["bolus_height"] = (
        carb_df["carb_values"] / carb_df["carb_ratio_values"]
    )

    # TODO: visualize the carb-to-insulin-ratio (cir)
    # in the meantime drop rows where grams is null
    carb_df.dropna(subset=['carb_values'], inplace=True)

    df_trace = go.Scatter(
        name="carbs",
        mode='markers + text',
        x=carb_df["datetime"],
        y=carb_df["bolus_height"] + 0.75,
        hoverinfo="name",
        marker=dict(
            color="#0AA648",
            size=25
        ),
        opacity=0.75,
        text=carb_df["carb_values"],
        textposition='middle center'
    )

    df_trace.yaxis = "y"

    return carb_df, df_trace


def prepare_target_range(df_target_range, continguous_ts, current_time):
    df = pd.merge(
        continguous_ts,
        df_target_range,
        left_on="time",
        right_on="target_range_start_times",
        how="left"
    )
    df["target_range_minimum_values"].fillna(method='ffill', inplace=True)
    df["target_range_maximum_values"].fillna(method='ffill', inplace=True)
    target_at_eval_df = df[df["datetime"] == current_time]
    df.dropna(subset=['target_range_minimum_values'], inplace=True)

    # downsample
    target = df[df["target_range_value_units"].notnull()].copy()
    target.reset_index(drop=True, inplace=True)

    target_traces = []
    for i in range(0, len(target)):
        min_val = target["target_range_minimum_values"][i]
        max_val = target["target_range_maximum_values"][i]
        if max_val - min_val < 5:
            width = 5
        else:
            width = 1

        if i < len(target)-1:
            legend_on = False
            x_vals = [
                target["datetime"][i],
                target["datetime"][i],
                target["datetime"][i+1],
                target["datetime"][i+1],
                target["datetime"][i]
            ]

            y_vals = [
                min_val,
                max_val,
                max_val,
                min_val,
                min_val
            ]

        else:
            legend_on = True
            x_vals = [
                target["datetime"][i],
                target["datetime"][i]
            ]

            y_vals = [
                min_val,
                max_val,
            ]

        tmp_trace = go.Scatter(
            name="target range (@ eval = {}-{} mg/dL)".format(
              int(target_at_eval_df["target_range_minimum_values"].values[0]),
              int(target_at_eval_df["target_range_maximum_values"].values[0])
            ),
            legendgroup="target_range",
            showlegend=legend_on,
            mode='lines',
            x=x_vals,
            y=y_vals,
            hoverinfo="none",
            line=dict(
                shape='vh',
                width=width,
                color="rgba(152, 134, 207, 0.25)"
            ),
            fill="tonext",
            fillcolor="rgba(152, 134, 207, 0.125)",
        )

        tmp_trace.yaxis = "y2"
        target_traces.append(tmp_trace)

    return df, target_traces


def prepare_insulin_axis(basal, bolus, carbs, current_time):

    max_value = max(
        basal["basal_rate_values"].max() + 1,
        basal["tbr"].max() + 1,
        carbs["bolus_height"].max() + 2,
        bolus["dose_values"].max() + 2,
    )

    axis = dict(
        domain=[0, 0.2],
        range=[0, max_value],
        fixedrange=True,
        hoverformat=".2f",
        showgrid=True,
        gridcolor="#c0c0c0",
        title=dict(
            text="Insulin (U, U/hr)",
            font=dict(
                size=10
            )
        )
    )

    annotation = go.layout.Annotation(
        x=current_time,
        y=max_value - 0.25,
        xref="x",
        yref="y",
        text="evaluation point",
        showarrow=True,
        arrowhead=1,
        ax=0,
        ayref="y",
        ay=max_value + 0.25
    )
    return axis, annotation


def prepare_loop_prediction(predicted_bg_dates, predicted_bg_values):
    bg_prediction_trace = go.Scattergl(
        name="predicted bg",
        x=predicted_bg_dates,
        y=predicted_bg_values,
        hoverinfo="y+name",
        mode='lines',
        line=dict(
            color="#9886CF",
            dash="longdash",
        )
    )
    bg_prediction_trace.yaxis = "y2"

    return bg_prediction_trace


def prepare_loop_temp_basal(basal, current_time, recommended_temp_basal):
    if recommended_temp_basal is not None:
        rec_temp_basal_rate = recommended_temp_basal[0]
        rec_temp_basal_duration = recommended_temp_basal[1]
    else:
        rec_temp_basal_rate = basal.loc[basal.index.max(), "basal_rate_values"]
        rec_temp_basal_duration = 30

    rec_basal_trace = go.Scatter(
        name="temp basal set = {} U/hr".format(rec_temp_basal_rate),
        mode='lines',
        x=[
           current_time,
           current_time + datetime.timedelta(minutes=rec_temp_basal_duration),
        ],
        y=[rec_temp_basal_rate, rec_temp_basal_rate],
        hoverinfo="y+name",
        line=dict(
            shape='vh',
            color='#5691F0',
            dash='solid'
        ),
        fill='tozeroy',
    )

    rec_basal_trace.yaxis = "y"

    return rec_basal_trace


def prepare_loop_bolus(recommended_bolus, current_time):

    df_trace = go.Scatter(
        name="recommended bolus = {} U".format(recommended_bolus),
        showlegend=True,
        mode='markers',
        x=[current_time],
        y=[recommended_bolus+0.25],
        hoverinfo="text",
        hovertext=["{} rec bolus".format(recommended_bolus)],
        marker=dict(
            symbol='triangle-down-open',
            size=10 + recommended_bolus,
            color="#5691F0"
        ),
    )

    df_trace.yaxis = "y"

    return df_trace


def prepare_suspend(suspend_threshold, current_time):
    df_trace = go.Scatter(
        name="suspend threshold = {} mg/dL".format(suspend_threshold),
        mode='lines',
        x=[
           current_time - datetime.timedelta(days=1),
           current_time + datetime.timedelta(days=1),
        ],
        y=[suspend_threshold, suspend_threshold],
        hoverinfo="none",
        line=dict(
            shape='vh',
            color='red',
            dash='solid'
        ),
        opacity=0.25,
    )
    df_trace.yaxis = "y2"

    return df_trace


def prepare_insulin_effect_onboard_trace(
        loop_output, bolus, isf, continguous_ts
):
    if len(bolus) > 0:
        insulin_effect_df = pd.DataFrame()
        insulin_effect_df["insulin_effect_dates"] = (
            loop_output["historical_insulin_effect_dates"]
        )
        insulin_effect_df["insulin_effect_values"] = (
            loop_output["historical_insulin_effect_values"]
        )
        insulin_effect_df["delta"] = (
            insulin_effect_df["insulin_effect_values"]
            - insulin_effect_df["insulin_effect_values"].shift(1)
        )

        # get bolus and isf time series
        df = pd.merge(
            continguous_ts,
            bolus,
            left_on="datetime",
            right_on="dose_start_times",
            how="left"
        )

        # add isf time series
        df = pd.merge(
            df,
            isf,
            left_on="time",
            right_on="sensitivity_ratio_start_times",
            how="left"
        )

        df["sensitivity_ratio_values"].fillna(method='ffill', inplace=True)

        df["total_effect"] = (
            df["dose_values"] * df["sensitivity_ratio_values"]
        )

        df = df.dropna(subset=['total_effect'])

        insulin_effect_onboard_df = pd.merge(
            df,
            insulin_effect_df.rename(
                columns={"insulin_effect_dates": "datetime"}
            ),
            on="datetime",
            how="outer"
        )

        insulin_effect_onboard_df["total_effect"].fillna(0, inplace=True)
        insulin_effect_onboard_df.sort_values("datetime", inplace=True)
        insulin_effect_onboard_df.reset_index(drop=True, inplace=True)

        insulin_effect_onboard_df["temp"] = (
            insulin_effect_onboard_df["total_effect"]
            + insulin_effect_onboard_df["delta"].fillna(0)
        )

        insulin_effect_onboard_df["values"] = (
            insulin_effect_onboard_df["temp"].cumsum()
        )

        df_trace = go.Scatter(
            name="insulin effect on board",
            mode='lines',
            x=insulin_effect_onboard_df["datetime"],
            y=insulin_effect_onboard_df["values"],
            hoverinfo="y+name",
            line=dict(
                color='#5691F0',
                dash='solid'
            ),
            fill='tozeroy',
            fillcolor='rgba(86,145,240, 0.125)'
        )

    else:

        df_trace = go.Scatter(
            name="insulin effect on board",
            mode='lines',
            x=[],
            y=[],
            hoverinfo="y+name",
            line=dict(
                color='#5691F0',
                dash='solid'
            ),
            fill='tozeroy',
            fillcolor='rgba(86,145,240, 0.125)'
        )

    df_trace.yaxis = "y3"

    df_axis = dict(
            domain=[0.25, .475],
            fixedrange=True,
            hoverformat=".0f",
            zeroline=False,
            showgrid=True,
            gridcolor="#c0c0c0",
            title=dict(
                text="Effect On-Board (mg/dL)",
                font=dict(
                    size=10
                )
            )
        )

    return df_trace, df_axis


def prepare_carb_effect_onboard_trace(
        loop_output, carbs, isf, carb_ratios, continguous_ts
):
    if len(carbs) > 0:
        df = pd.merge(
            continguous_ts,
            isf,
            left_on="time",
            right_on="sensitivity_ratio_start_times",
            how="left"
        )
        df["sensitivity_ratio_values"].fillna(method='ffill', inplace=True)

        df = pd.merge(
            df,
            carb_ratios,
            left_on="time",
            right_on="carb_ratio_start_times",
            how="left"
        )
        df["carb_ratio_values"].fillna(method='ffill', inplace=True)

        df["csf"] = df["sensitivity_ratio_values"] / df["carb_ratio_values"]

        date_min = (
            carbs["datetime"].dt.round("5min").min() - pd.Timedelta("5min")
        )
        date_max = loop_output["cob_timeline_dates"][0] - pd.Timedelta("5min")
        carb_effect_ob = (
            create_contiguous_ts(date_min, date_max, freq="5min")
        )
        carb_effect_ob["cob"] = np.nan
        # TODO: there has to be a better way to get historical carbs on board
        # this method is re-running the loop algorithm
        for d in carb_effect_ob["datetime"]:
            inputs = loop_output["input_data"]
            inputs["time_to_calculate_at"] = (
                datetime.datetime.fromisoformat(d.isoformat())
            )
            temp_loop_output = update(inputs)
            carb_effect_ob.loc[carb_effect_ob["datetime"] == d, "cob"] = (
                temp_loop_output["carbs_on_board"]
            )

        # get the carbs on board time series
        cob_df = pd.DataFrame()
        cob_df["datetime"] = loop_output["cob_timeline_dates"]
        cob_df["cob"] = loop_output["cob_timeline_values"]

        carb_effect_ob = pd.concat(
            [carb_effect_ob, cob_df], ignore_index=True, sort=True
        )

        carb_effect_ob = pd.merge(
            carb_effect_ob,
            df[["datetime", "csf"]],
            on="datetime",
            how="left"
        )

        carb_effect_ob["values"] = (
            carb_effect_ob["cob"] * carb_effect_ob["csf"]
        )

        carb_effect_ob_trace = go.Scatter(
            name="carb effect on board",
            mode='lines',
            x=carb_effect_ob["datetime"],
            y=carb_effect_ob["values"],
            hoverinfo="y+name",
            line=dict(
                color='#0AA648',
                dash='solid'
            ),
            fill='tozeroy',
            fillcolor='rgba(10,166,72, 0.125)'
        )

    else:
        carb_effect_ob_trace = go.Scatter(
            name="carb effect on board",
            mode='lines',
            x=[],
            y=[],
            line=dict(
                color='#0AA648',
                dash='solid'
            ),
            fill='tozeroy',
            fillcolor='rgba(10,166,72, 0.125)'
        )

    carb_effect_ob_trace.yaxis = "y3"

    return carb_effect_ob_trace


def prepare_layout(
    current_time, top_axis, bottom_axis, top_annotation, bottom_annotation,
    middle_axis
):
    layout = go.Layout(
        showlegend=True,
        plot_bgcolor="white",
        yaxis=bottom_axis,
        yaxis2=top_axis,
        yaxis3=middle_axis,
        xaxis=dict(
            range=(
                current_time - datetime.timedelta(hours=8),
                current_time + datetime.timedelta(hours=6)
            ),
            showgrid=True,
            gridcolor="#c0c0c0",
        ),
        annotations=[
            top_annotation,
            bottom_annotation
        ],
        dragmode="pan",
        hovermode="x"
    )

    return layout


def make_scenario_figure(loop_output):
    inputs = loop_output["input_data"]

    # convert dict_inputs_to_dataframes
    (
     basal_rates, carb_events, carb_ratios, dose_events, blood_glucose,
     df_last_temporary_basal, df_misc, df_sensitivity_ratio,
     df_settings, df_target_range
    ) = dict_inputs_to_dataframes(inputs)

    current_time = inputs.get("time_to_calculate_at")

    # %% blood glucose data
    bg_df, bg_trace, bg_axis, bg_annotation = (
        prepare_bg(blood_glucose, current_time)
    )

    # create a contiguous time series for the other data types
    date_min = bg_df["glucose_dates"].min() - datetime.timedelta(days=1)
    date_max = current_time + datetime.timedelta(days=1)
    continguous_ts = create_contiguous_ts(date_min, date_max)

    # target range
    target_range, target_traces = (
        prepare_target_range(df_target_range, continguous_ts, current_time)
    )

    # suspend threshold
    suspend_threshold = inputs["settings_dictionary"]["suspend_threshold"]
    suspend_trace = prepare_suspend(suspend_threshold, current_time)

    # %% insulin and carb data
    dose_events["type"] = (
        dose_events["dose_types"].apply(convert_times_and_types)
    )

    # basal data
    basal, sbr, basal_delivered_traces, scheduled_basal_traces,  = (
        prepare_basal(basal_rates, dose_events, continguous_ts, current_time)
    )

    # bolus data
    bolus, bolus_trace = prepare_bolus(dose_events)

    # carb data (cir and carb events)
    carbs, carb_trace = prepare_carbs(carb_events, carb_ratios, continguous_ts)

    # prepare insulin axis
    insulin_axis, insulin_annotation = (
        prepare_insulin_axis(basal, bolus, carbs, current_time)
    )

    # %% add loop prediction, temp basal, and bolus recommendation
    # loop prediction
    predicted_glucose_dates = loop_output.get("predicted_glucose_dates")
    predicted_glucose_values = loop_output.get("predicted_glucose_values")
    loop_prediction_trace = prepare_loop_prediction(
        predicted_glucose_dates, predicted_glucose_values
    )

    # recommended temp basal
    loop_temp_basal = loop_output.get("recommended_temp_basal")
    loop_basal_trace = (
        prepare_loop_temp_basal(basal, current_time, loop_temp_basal)
    )

    # recommended bolus
    loop_rec_bolus = loop_output.get("recommended_bolus")[0]
    loop_bolus_trace = prepare_loop_bolus(loop_rec_bolus, current_time)

    # insulin effect on-board trace
    insulin_effect_on_board_trace, effect_on_board_axis = (
        prepare_insulin_effect_onboard_trace(
            loop_output, bolus, df_sensitivity_ratio, continguous_ts
        )
    )

    # carb effect on-board trace
    carb_effect_on_board_trace = (
        prepare_carb_effect_onboard_trace(
            loop_output, carbs, df_sensitivity_ratio,
            carb_ratios, continguous_ts
        )
    )

    # %% make figure
    fig_layout = prepare_layout(
        current_time, bg_axis, insulin_axis, bg_annotation, insulin_annotation,
        effect_on_board_axis
    )

    traces = []
    traces.extend([bg_trace, loop_prediction_trace])
    traces.extend(target_traces)
    traces.extend([
        suspend_trace, insulin_effect_on_board_trace,
        carb_effect_on_board_trace,
        loop_basal_trace, loop_bolus_trace, carb_trace, bolus_trace
    ])
    traces.extend(scheduled_basal_traces)
    traces.extend(basal_delivered_traces)

    fig = go.Figure(data=traces, layout=fig_layout)

    return fig


# %% view example scenario(s)
def view_example():
    # load in example scenario files
    cutom_scenario_files = [
        "custom-scenario-table-template-simple.csv",
        "custom-scenario-table-template-complex.csv",
        "custom-scenario-table-example-3.csv",
        "hypothetical-scenario-1.csv"
    ]
    path = os.path.join(".", "example_files")
    table_path_name = os.path.join(path, cutom_scenario_files[2])
    custom_table_df = pd.read_csv(table_path_name, index_col=0)
    inputs_from_file = input_table_to_dict(custom_table_df)
    loop_algorithm_output = update(inputs_from_file)
    plotly_fig = make_scenario_figure(loop_algorithm_output)
    plot(plotly_fig, filename=table_path_name+".html")


if __name__ == "__main__":
    view_example()
