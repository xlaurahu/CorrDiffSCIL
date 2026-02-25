# Earth-2 Correction-Diffusion Model 
A guide for generating AI weather forecast using NVIDIA Earth2Studio CorrDiff Model 

## Required Installations 

Install `conda` in your terminal and make sure `python3 --version` > 3.10 

`pip install earth2studio`

Activate earth2studio kernel 'conda activate earth2studio'

Verify installation in your Python3 kernel 

```
import earth2studio
print(earth2studio.__version__)
```

Install submodules NVIDIA Earth-2 Correction Diffusion in NIM

`pip install earth2studio[corrdiff]`

To ensure that data loads safely 

`pip install earth2studio[data]`

## Launching the NIM

To gain access to the CorrDiff model, we need to use the NIM(NVIDIA Inference Microservice). To launch the NIM, a personal API Key is required for the login process. To obtain the API key, register an account with NVIDIA [HERE](https://build.nvidia.com/settings/api-keys). It is recommended that you paste your key somewhere you have easy access to. 

Note that your key value starts with *nvapi-* and ends with *vEg*. Your key is only valid for one year. If you ever lose your key, you should delete the old one and generate a new API Key. 

>[!CAUTION]
>Your personal key should be kept private at all times.

To properly launch the NIM on your NVIDIA pod, download and edit the corrdiff-nim-deployment.yaml file to your username and change the value to your personal API key.

```
name: NGC_API_KEY
  value: "YOUR API KEY"
```

To create the deployment, run the following in your terminal:

`kubectl create -f corrdiff-nim-deployment-<YOUR NAME>.yaml -n sdsu-shen-climate-lab`

Then run:
`kubectl get pods -n sdsu-shen-climate-lab | grep corrdiff`

>[!TIP]
>It takes about 5-10 mins for Kubernetes cluster to download the images(~26GB), keep checking the real-time status of the container in watch mode by running `kubectl get pods -n sdsu-shen-climate-lab -w | grep corrdiff`.

Once the status shows running:
`kubectl logs -f deployment/corrdiff-nim-<YOUR NAME> -n sdsu-shen-climate-lab`

## Run CorrDiff NIM

To make predictions using CorrDiff NIM, a sample script to generate predictions for _Hurricane Helene(9/26/24 - 9/27/24)_ can be found in `CorrDiffHurrHeleVis.ipynb`. The script includes API key validation and NIM health check; it is a prerequisite to ensure that the status of both checkpoints is good before running inferences. The runtime for your inference depends on your sample size and step size. Users are recommended to limit the inference runtime by adjusting the timeout. 

Note that CorrDiff NIM only generates raw tensor outputs; users should handle post-processing of the metadata. The script utilizes channel-specific ensemble mean and other post-processing strategies that are suitable for hurricane tracking. For more information about the CorrDiff model, please visit the CorrDiff model card [HERE](https://build.nvidia.com/nvidia/corrdiff/modelcard). 

The animated prediction visualization for Hurricane Helene:
![Animated Hurricane Helene Predictions](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/combined.gif)

The script titled `HurrHeleTrack.ipynb` produces the predicted track for Hurricane Helene against the ground truth documented by NOAA. 

The predicted hurricane track:
![Predicted track](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HurrHele_Prediction_track.png)




