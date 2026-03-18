# Troubleshooting 


## Corrdiff NIM

### NIM Authentication

If the status of your NIM alternates between `ImagePullOff`  and `ErrImagePull`, and your NIM connection returns a 402 error in JupyterHub, that indicates you encountered an authentication error. 
You typically encounter this when you try to deploy the NIM from a device other than the one you registered. 

To troubleshoot, first delete your deployment, then delete your old API Key and generate a new one [HERE](https://build.nvidia.com/explore/discover). 

In your terminal, paste the following code. Remember to change <NAMESPACE> into your namespace, and replace <YOUR API KEY> with your new key:

```bash
#delete your previous secret 
kubectl delete secret ngc-secret -n <NAMESPACE>

#create a new one using your new key
kubectl create secret docker-registry ngc-secret \                                                                    
  --docker-server=nvcr.io \                       
  --docker-username='$oauthtoken' \                                                                                   
  --docker-password=<YOUR API KEY> \                                                                              
  --docker-email=<YOUR EMAIL> \                                                                                       
  -n <NAMESPACE> 
```

Now, replace the api key value in your YAML file, and try deploying the NIM in your terminal again. 



