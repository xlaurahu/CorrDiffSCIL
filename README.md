# Earth-2 Correction Diffusion NIM User Guide
A guide for generating an AI weather forecast using NVIDIA Earth2Studio CorrDiff NIM 1.0.0

![Animated Hurricane Helene Predictions](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HurricaneHele.gif)


This guide covers two paths depending on your access:

- **[GPU Access: Deploy Your Own NIM](#gpu-access-deploy-your-own-nim)** — for those with a GPU cluster namespace.
- **[Non-GPU Access: Hosted Inference](#non-gpu-access-hosted-inference)** — for those without GPU/Kubernetes access.

---

# GPU Access: Deploy Your Own NIM

:movie_camera: **Watch a video tutorial on CorrDiff Deployment [Here](https://www.youtube.com/watch?v=rKQSJZzlZLo)!**

## Important Prerequisites

In order to deploy CorrDiff NIM, you need to be added to a namespace in the NRP Nautilus hypercluster. You also need `kubectl` and `kubelogin` from Kubernetes for pulling the container from the NIM.   

If you are currently not in a namespace, please contact your admin. To install `kubectl` and gain access to the GPU clusters, follow the instructions [HERE](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/Kubernetes.md).

## NGC API Key

To acquire the NIM, we need to create an NVIDIA NGC account and generate a personal API Key

Create an account [HERE](https://ngc.nvidia.com/signin). 

Copy and paste the key that only you have access to. NGC does not save your key values for you; if you ever lose your key, you can always generate a new API Key. 

>[!CAUTION]
>Your personal key should be kept private at all times. 

With your API key values, go to your terminal and create a secret using `kubectl` for your key:

```
kubectl create secret docker-registry ngc-secret-<USERNAME> \
  --docker-server=nvcr.io \
  --docker-username='$oauthtoken' \
  --docker-password= 'YOUR API KEY' \
  -n <YOUR NAMESPACE>
```
Replace `'YOUR API KEY'` with your actual key values.

Then create the ngc-api-key 

```
kubectl create secret docker ngc-api-key-<USERNAME> \
  --from-literal=NGC_API_KEY= 'YOUR API KEY' \
  -n <YOUR NAMESPACE>
```
Replace `'YOUR API KEY'` with your actual key values and replace `<USERNAME>` and `<YOUR NAMESPACE>` with your designated username and namespace.

Check that your secret and api key exist:
```
kubectl get secrets -n <YOUR NAMESPACE>
```
If they are properly stored in your system, you should see:

```
NAME                            TYPE                             DATA   AGE
ngc-secret-<USERNAME>            kubernetes.io/dockerconfigjson   1      2m
ngc-api-key-<USERNAME>           Opaque                           1      2m
```

Download and edit the corrdiff-nim-deployment.yaml file by replacing <USERNAME> with your username and <YOUR NAMESPACE> with the title of your namespace [HERE](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/corrdiff-nim-deployment.yaml). Please store the file in a path where you run your Kubernetes. 

---

## Launch CorrDiff NIM

To launch the NIM, run the following in your terminal:

```
kubectl create -f corrdiff-nim-deployment-<USERNAME>.yaml -n <YOUR NAMESPACE>
```

Then run:
```
kubectl get pods -n <YOUR NAMESPACE> -w | grep corrdiff
```
To check your live connection logs, run:
```
kubectl logs -f deployment/corrdiff-nim-<USERNAME> -n <YOUR NAMESPACE>
```

>[!TIP]
>It takes about 5-10 mins for the Kubernetes cluster to download the images(~26GB), keep checking the live status of the container in watch mode. The container is ready when the status shows _RUNNING_. 

---

## (Optional) Expose Your NIM to Users Without Cluster Access

By default the Service created above (`corrdiff-nim-service-<USERNAME>`) is `ClusterIP`, meaning
it's only reachable from other pods inside the same Nautilus cluster (e.g. your own JupyterHub
notebook) — not from someone's laptop or an external Python environment.

If you want to let others run inference against your deployment without needing an NGC
account, or a namespace, see [ExposeNIMPublicly.md](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/ExposeNIMPublicly.md)
for the full step-by-step: applying an kubectl Ingress, why it's configured the way it is, verifying it
actually works end-to-end, and pointing people to
[HostedInference.md](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HostedInference.md) to
call your public URL — no GPU required on their end.

>[!CAUTION]
>This makes your GPU inference endpoint reachable from the public internet with no
>authentication. Anyone with the URL can submit inference requests and consume your shared GPU
>quota. Share the URL only with people you trust to use it responsibly.

---
## Required Installations in Python Env.

Install `earth2studio` in your kernel 
```
pip install earth2studio
```
Verify installation in your Python3 kernel 

```
import earth2studio
print(earth2studio.__version__)
```

Make sure your Python version is >3.10.

Install submodules NVIDIA Earth-2 Correction Diffusion in NIM to ensure that data loads safely: 

```
pip install earth2studio[corrdiff]
pip install earth2studio[data]
```
---

## Run CorrDiff NIM

Create a designated folder in your JupyterHub.

Download the files named `corrdiff_output_lat.npy`, `corrdiff_output_lon.npy` from this GitHub repository, and upload them to the folder. These files tell us how the output is mapped onto CONUS. 

Examples are stored in location-named folders.

### About the ipynb Script 

The script includes API key validation and a NIM health check; it is a prerequisite to ensure that both checkpoints are healthy before running inferences. The runtime for your inference depends on your sample size and step size. We recommend limiting the inference runtime by adjusting the timeout. 

Note that CorrDiff NIM only generates raw tensor outputs; users should handle metadata post-processing. The sample script includes channel-specific ensemble mean and other post-processing strategies, which are suitable for hurricane tracking. Visit the CorrDiff model card for more information on inputs and outputs. 

---
## Troubleshooting

Common issues encountered when deploying CorrDiff NIM and their solutions can be found [HERE](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/troubleshoot.md). 

---

## Delete Containers 

To free up space for other users, one should always delete their NIM container once they are done deploying it. To delete your deployment, run the following commands in your terminal. 
```
kubectl delete deployment corrdiff-nim-<YOUR NAME> -n <YOUR LAB NAMESPACE>
kubectl delete service corrdiff-nim-service-<YOUR NAME> -n <YOUR LAB NAMESPACE>
```

---

# Non-GPU Access: Hosted Inference

>[!NOTE]
>This path is intended for special events or deployment demos only. If you expect to use CorrDiff
>on an ongoing basis, gaining personal or organizational access is recommended instead — see
>[GPU Access: Deploy Your Own NIM](#gpu-access-deploy-your-own-nim) above.

**Have limited GPU access and just want to run inference?** You don't need to deploy your own
NIM. See [HostedInference.md](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HostedInference.md) to request an already-running CorrDiff endpoint directly from any Python environment. 

---

## References

* [NVIDIA NIM](https://docs.nvidia.com/nim/earth-2/corrdiff/latest/overview.html)
* [Model Card](https://build.nvidia.com/nvidia/corrdiff/modelcard)
* [Earth2Studio](https://github.com/NVIDIA/earth2studio)




>[!NOTE]
>The new corrdiff version 1.1.0 is out on 1/20/2026. This version requires an AI Enterprise subscription.



