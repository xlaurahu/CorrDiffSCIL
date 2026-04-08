# Earth-2 Correction Diffusion NIM User Guide
A guide for generating an AI weather forecast using NVIDIA Earth2Studio CorrDiff NIM 1.0.0

![Animated Hurricane Helene Predictions](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HurricaneHele.gif)

## Important Prerequisites

In order to deploy CorrDiff NIM, you need to be added to a namespace in NRP Nautilus hypercluster. You also need `kubectl` and `kubelogin` from Kubernetes for pulling the container from the NIM.   

If you are currently not in a namespace, please contact your admin. To install `kubectl` and gain access to the GPU clusters, follow the instructions [HERE](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Kubernetes.md).

## NGC API Key

To acquire the NIM, we need to create an NVIDIA NGC account and generate a personal API Key

Create an account [HERE](https://ngc.nvidia.com/signin). 

After your account is setup, click on your profile on the top right corner, then go to `Setup`. 
Once you are in the `Setup` section, find `API Keys` and click on Generate API Key. 

In the API Keys section, you can see all the personal keys you have created. Click on `Generate Personal Key` and add key detials. Make sure you select all the services under Key Permission before it is generated. 

Copy and paste the key that only you have access to. NGC does not save your key values for you, if you ever lose your key, you can always generate a new API Key. 

>[!CAUTION]
>Your personal key should be kept private at all times. 

With your API key values, go to your terminal and create a secret using `kubectl` for your key:

```
kubectl create secret docker-registry ngc-secret-<USERNAME> \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password= 'YOUR API KEY' \
  -n sdsu-shen-climate-lab
```
Replace `'YOUR API KEY'` with your actual key values.

Then create the ngc-api-key 

```
kubectl create secret docker ngc-api-key-<USERNAME> \
  --from-literal=NGC_API_KEY= 'YOUR API KEY' \
  -n sdsu-shen-climate-lab
```
Replace `'YOUR API KEY'` with your actual key values and replace <USERNAME> with your username.

Download and edit the corrdiff-nim-deployment.yaml file by replacing <USERNAME> with your username and <YOUR NAMESPACE> with the title of your namespace. 

To verify that the secret exists:

```
kubectl get secrets -n <YOUR NAMESPACE>
```
If they are properly stored in your system, you should see:

```
NAME                            TYPE                             DATA   AGE
ngc-secret-<USERNAME>            kubernetes.io/dockerconfigjson   1      2m
ngc-api-key-<USERNAME>           Opaque                           1      2m
```

---

## Launching the NIM

To launch the NIM, run the following in your terminal:

```
kubectl create -f corrdiff-nim-deployment-<YOUR NAME>.yaml -n <YOUR NAMESPACE>
```

Then run:
```
kubectl get pods -n <YOUR NAMESPACE> -w | grep corrdiff
```

>[!TIP]
>It takes about 5-10 mins for the Kubernetes cluster to download the images(~26GB), keep checking the live status of the container in watch mode. The container is ready when the status shows _RUNNING_. To check your live connection logs, run `kubectl logs -f deployment/corrdiff-nim-laurahu -n sdsu-shen-climate-lab` in your terminal. 

---
## Required Installations in JupyterHub

Install `earth2studio` in your kernel 
```
pip install earth2studio
```
Verify installation in your Python3 kernel 

```
import earth2studio
print(earth2studio.__version__)
```

Make sure your python version is >3.10.

Install submodules NVIDIA Earth-2 Correction Diffusion in NIM to ensure that data loads safely: 

```
pip install earth2studio[corrdiff]
pip install earth2studio[data]
```
---

## Run CorrDiff NIM

Create a designated folder in your JupyterHub called `corrdiff`

Download the files named `corrdiff_output_lat.npy`, `corrdiff_output_lon.npy` from this Github repository, and upload it to the folder. These files tell us how the output is mapped onto CONUS. 

Download `CorrDiffHurrHeleVis.ipynb`, this is the ipynb file that deploys NIM to generate downscaled outputs. 

### About the ipynb Script 

The script includes API key validation and NIM health check; it is a prerequisite to ensure that the status of both checkpoints is good before running inferences. The runtime for your inference depends on your sample size and step size. Users are recommended to limit the inference runtime by adjusting the timeout. 

Note that CorrDiff NIM only generates raw tensor outputs; users should handle metadata post-processing. The sample script includes channel-specific ensemble mean and other post-processing strategies, which are suitable for hurricane tracking. Visit the CorrDiff model card for more information on inputs and outputs. 

---
## Troubleshooting

Common issues encountered during when deploying CorrDiff NIM v1.0.0 and their solutions can be found [HERE](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/troubleshoot.md). 

---

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








>[!NOTE]
>The new corrdiff version 1.1.0 is out on 1/20/2026, this version requires AI Enterprise subscription.




