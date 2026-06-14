# Lower Ohio River Valley Flood 4/3/2025

## Overview

The Lower Ohio River Valley is a flood-prone corridor where converging weather systems and terrain funneling can produce extreme precipitation events within a short window. On April 3, 2025, a significant rainfall event struck this region, raising flood concerns along the Ohio River and its tributaries. This area is particularly sensitive to flash flooding because the river basin drains a large upstream area, meaning even localized heavy rainfall can rapidly translate into dangerous stream rises that threaten communities downstream.

Accurate, high-resolution precipitation forecasting is critical for early flood warning in this region. We evaluate CorrDiff — NVIDIA's generative AI downscaling model — against observational data to assess whether it can capture the spatial structure and intensity of such high-impact precipitation events.

## Validation with MRMS 3-Hour Radar Data

As the observational benchmark, we use MRMS (Multi-Radar Multi-Sensor) Quantitative Precipitation Estimates at a 3-hour accumulation interval. MRMS is ideal for this validation for two reasons:

1. **Spatial resolution**: MRMS operates at ~1 km resolution, which closely matches CorrDiff's downscaled output resolution and allows a fair apples-to-apples comparison of precipitation spatial patterns, peak intensities, and storm structure.
2. **Temporal resolution**: The 3-hour QPE product captures sub-daily rainfall evolution, enabling us to track how the precipitation system developed and compare it to CorrDiff's hourly outputs on the same timescale.

Together, these properties make MRMS the most appropriate validation source for testing whether CorrDiff can realistically reproduce extreme rainfall at the scale relevant to flood forecasting.

---
## CorrDiff Initial Condition
![Initial](https://github.com/xlaurahu/LowerOhioRiver/blob/main/corrdiff_precip_initial.png)

## CorrDiff Daily Precipitation 
![CorrDiff](https://github.com/xlaurahu/LowerOhioRiver/blob/main/Corrdiff_summed_precip.png)

## MRMS Radar Daily Precipitation 
![MRMS](https://github.com/xlaurahu/LowerOhioRiver/blob/main/Mrms_summed_precip.png)

## Hourly Precipitation: [mm/day](https://github.com/xlaurahu/LowerOhioRiver/tree/main/HourlyPrecp_mm%3Aday), [mm/hr](https://github.com/xlaurahu/LowerOhioRiver/tree/main/HourlyPrecp_mm%3Ahr)

## Regional CorrDiff Prediction vs. MRMS and Other Observational Products 
![Compare](https://github.com/xlaurahu/LowerOhioRiver/blob/main/totalsumprecp.png)

## Uncertainty Measure with 8 Samples

<img src="https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Tennessee%20Valley/corrdiff_city_timeseries.png" width="400"/>

---

## References: 

[MRMS](https://noaa-mrms-pds.s3.amazonaws.com/index.html#CONUS/RadarOnly_QPE_03H_00.00/20250404/)

[CoCoRaHs](https://www.weather.gov/lmk/HistoricRainfallFloodingApril2-62025)

[CMorph](https://icharm.sdsu.edu/)
