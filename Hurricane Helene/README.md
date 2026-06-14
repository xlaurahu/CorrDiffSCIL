# Hurricane Helene — CorrDiff Case Study 9/26/2024

## Overview

Hurricane Helene made landfall near Perry, Florida on September 26, 2024 as a Category 4 storm, producing catastrophic rainfall, storm surge, and wind damage across the southeastern United States. The storm's rapid intensification before landfall and its exceptionally broad wind field made it one of the most impactful Atlantic hurricanes in recent history, with extreme precipitation extending hundreds of miles inland into the Appalachians and causing historic flooding in western North Carolina.

This case study evaluates CorrDiffSCIL — NVIDIA's generative AI downscaling model driven by HRRR initial conditions — on its ability to reproduce the spatial structure of precipitation, surface temperature, and wind speed at 15Z on September 26, 2024 (the landfall period), and to track the storm center location against both the HRRR forecast and the observed best track.

## Validation Against HRRR and Observed Truth

CorrDiff takes HRRR (High-Resolution Rapid Refresh) output as its initial condition and generates high-resolution downscaled fields. We validate against two references:

- **HRRR**: the operational NWP model serving as the low-resolution input baseline, representing what the model starts from
- **Observed truth**: best-track position data and surface observations, representing what actually happened

This dual comparison lets us assess whether CorrDiff adds value beyond the HRRR baseline and whether it can faithfully track the storm's structure and movement.

---

## Precipitation (9/26 15Z)
![Precipitation](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Hurricane%20Helene/HurrHele_CONUS_Precp_9_26_15.png)

## Surface Temperature (9/26 15Z)
![Temperature](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Hurricane%20Helene/HurrHele_CONUS_Temp_9_26_15.png)

## Wind Speed (9/26 15Z)
![Wind Speed](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Hurricane%20Helene/HurrHele_CONUS_Windspeed_9_26_15.png)

## Storm Track Validation: CorrDiff vs. HRRR vs. Observed
![Track Validation](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Hurricane%20Helene/HurrHele_Prediction_track.png)

## CorrDiff Animation
![Animation](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Hurricane%20Helene/HurricaneHele.gif)

---

## References

[NHC Best Track — Hurricane Helene (ATCF archive)](ftp://ftp.nhc.noaa.gov/atcf/archive/)

[HRRR Model — NOAA ESRL](https://rapidrefresh.noaa.gov/hrrr/)

[NWS Historic Rainfall and Flooding April 2–6 2025 Summary](https://www.weather.gov/lmk/HistoricRainfallFloodingApril2-62025)
