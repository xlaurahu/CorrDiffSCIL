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
>It take about 5-10 mins for Kubernetes cluster to download the images(~26GB), keep checking the real-time status of the container in watch mode by running `kubectl get pods -n sdsu-shen-climate-lab -w | grep corrdiff`.

Once the status shows running:
`kubectl logs -f deployment/corrdiff-nim-<YOUR NAME> -n sdsu-shen-climate-lab`

## Run CorrDiff 








