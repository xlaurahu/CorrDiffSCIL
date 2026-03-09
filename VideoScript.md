# Earth-2 Correction-Diffusion Model: Generating AI Weather Forecasts with CorrDiff US

## Introduction 

Hi everyone, I am going to walk you through how to generate AI-based high-resolution weather forecasts using NVIDIA's 
Earth-2 Corrective-Diffusion model, known as CorrDiff US.

In this video, we will cover:

* Required installations
* Launching CorrDiff using NVIDIA CorrDiff NIM v1.0.0
* Running Hurricane predictions and output visualizations

By the end of this video, you should be able to produce high-resolution weather predictions like below:

![Windspeed](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HurrHele_CONUS_Windspeed_9_26_15.png)

Before proceeding, make sure you have an active connection to the Tide Nautulius pod before
running any `kubectl` commands. If you are not connected, establish your Nautulius session first.

## Required Installation 

First, we want to set up our environment.

In your kernel, make sure you have `conda` installed and Python version greater than 3.10. You can verify your
Python version with the command `python3--version`. Next, we want to install Earth2Studio using the 
command `pip install earth2studio`. As a standard practice, you should register a separate kernel for Earth2Studio.


## Launching CorrDiff NIM
Our next step is to launch the CorrDiff NIM. NIM stands for NVIDIA Inference Microservice, which is a containerized service 
that deploys NVIDIA AI models as production-ready inference endpoints. To access the NIM, you first need to register 
for a personal NVIDIA API Key [API link](https://build.nvidia.com/settings/api-keys). Your key should start with `nvapi-` and it 
is only valid for one year. You must keep your API key private at all times. 

Once you have obtained your API Key, download the [YAML file](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/corrdiff-nim-deployment.yaml). Inside the file, locate 
line 37 - 38:
```
name: NGC_API_KEY
  value: "YOUR API KEY"
```
Replace "YOUR API KEY" with your actual key value and save the file using your username. 

Now you can deploy the NIM through Kubernetes in your terminal by running 
```
kubectl create -f corrdiff-nim-deployment-<YOUR NAME>.yaml -n <Lab Namespace>
```
The container takes a couple of minutes to initialize. You can check the live status of your pod by running 
```
kubectl get pods -n <Lab Namespace> -w | grep corrdiff
```
Make sure the status of your pod shows _RUNNING_.

## Running CorrDiff for Hurricane Helene 

### Basic Visualization 

In this [ipynb file](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/CorrDiffHurrHeleVis.ipynb), we have a sample script that deploys the CorrDiff NIM to generate predictions for Hurricane Helene from 9/26/2024 15:00 to 9/27/2024 6:00. 

Important code chunks to point out in the script:

* API key validation and NIM health check
* Input function, variable, and format 
* Inference request and format
* Denormalization scheme
* Finding latitude and longitude files
* Applying regional mask 

### Hurricane Track 

In this [ipynb file](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/HurrHeleTrack.ipynb), we generated the predicted track for hurricane Helene over the same time frame. 

Important code chunks to point out in the script:

* Loop through different hours
* Selected track variable in the applied regional mask 
* Loading ground truth data


## Deleting deployments

It is always good practice to delete your container after completing your task. To delete your deployments, run the following commands in your terminal.

```
kubectl delete deployment corrdiff-nim-laurahu -n sdsu-shen-climate-lab
kubectl delete service corrdiff-nim-service-laurahu -n sdsu-shen-climate-lab
```

## Conclusion 

You are at the end of this video tutorial. For more information on the model, visit NVIDIA's [official webpage](https://docs.nvidia.com/nim/earth-2/corrdiff/latest/overview.html) for CorrDiff NIM or the [model card](https://build.nvidia.com/nvidia/corrdiff/modelcard). 

Thank you so much for watching. I hope this tutorial gives you a good idea of how to generate weather forecasts using CorrDiff NIM, and I hope you can try and generate your own desired forecasts using this wonderful tool. 






  
















