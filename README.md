# Earth-2 Correction-Diffusion Model 
A guide for generating AI weather forecast using NVIDIA Earth2Studio CorrDiff Model 

## Required Installations 

Install `conda` in your terminal and make sure `python3 --version` > 3.10 

```
pip install earth2studio
```

Activate earth2studio kernel 'conda activate earth2studio'.

Verify installation in your Python3 kernel 

```
import earth2studio
print(earth2studio.__version__)
```

Install submodules NVIDIA Earth-2 Correction Diffusion in NIM to ensure that data loads safely: 


```
pip install earth2studio[corrdiff]
pip install earth2studio[data]
```


## Launching the NIM

To gain easy access to the CorrDiff model, we want to use the CorrDiff NIM(NVIDIA Inference Microservice) provided by Earth2Studio. In order to acquire the NIM, we need to register a personal API Key for the process [HERE](https://build.nvidia.com/settings/api-keys). It is recommended that you paste your key somewhere you have easy access to. 

Note that your key value starts with *nvapi-* and ends with *vEg*. Your key is only valid for one year. If you ever lose your key, you should delete the old one and generate a new API Key. 

>[!CAUTION]
>Your personal key should be kept private at all times.

To properly launch the NIM on your NVIDIA pod, download and edit the corrdiff-nim-deployment.yaml file to your username and change the value to your personal API key.

```
name: NGC_API_KEY
  value: "YOUR API KEY"
```

To create the deployment, run the following in your terminal:

```
kubectl create -f corrdiff-nim-deployment-<YOUR NAME>.yaml -n <YOUR LAB NAMESPACE>
```

Then run:
```
kubectl get pods -n <YOUR LAB NAMESPACE> | grep corrdiff
```

>[!TIP]
>It takes about 5-10 mins for the Kubernetes cluster to download the images(~26GB), keep checking the live status of the container in watch mode by running `kubectl get pods -n <YOUR LAB NAMESPACE> -w | grep corrdiff`. The container is ready when the status shows _RUNNING_. To check your live connection logs, run `kubectl logs -f deployment/corrdiff-nim-laurahu -n sdsu-shen-climate-lab` in your terminal. 


## Run CorrDiff NIM

To make predictions using CorrDiff NIM, a sample script that generates predictions for _Hurricane Helene(9/26/24 - 9/27/24)_ can be found under the file name `CorrDiffHurrHeleVis.ipynb`. 

The script includes API key validation and NIM health check; it is a prerequisite to ensure that the status of both checkpoints is good before running inferences. The runtime for your inference depends on your sample size and step size. Users are recommended to limit the inference runtime by adjusting the timeout. 

Note that CorrDiff NIM only generates raw tensor outputs; users should handle post-processing of the metadata. The sample script includes channel-specific ensemble mean and other post-processing strategies, which are suitable for hurricane tracking. Visit the CorrDiff model card for more information on inputs and outputs. 

The predicted windspeed, temperature, and vorticity for Hurricane Helene:

![Animated Hurricane Helene Predictions](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/combined.gif)

The predicted hurricane track against the ground truth:

![Predicted track](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HurrHele_Prediction_track.png)

## Delete Deployment 

To free up space for other users, one should always delete their NIM container once they are done deploying it. To delete your deployment, run the following commands in your terminal. 
```
kubectl delete deployment corrdiff-nim-<YOUR NAME> -n <YOUR LAB NAMESPACE>
kubectl delete service corrdiff-nim-service-<YOUR NAME> -n <YOUR LAB NAMESPACE>
```


## References

* [NVIDIA NIM](https://docs.nvidia.com/nim/earth-2/corrdiff/latest/overview.html)
* [Model Card](https://build.nvidia.com/nvidia/corrdiff/modelcard)
* [Earth2Studio](https://github.com/NVIDIA/earth2studio)




### Bonus .zshrc shortcuts
Mac users can download the .zshrc shortcut from `MacOSshorcut.sh`



