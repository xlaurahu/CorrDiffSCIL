# Troubleshooting 


## Corrdiff NIM

### NIM Authentication

If the status of your NIM alternates between `ImagePullOff`  and `ErrImagePull` with your NIM connection shows a 402 error on your JupyterHub, that means you encountered an authentication error. 
You typically encounter this when you try to deploy the NIM from a different device(found out the hard way ...). 

To troubleshoot, first delete your deployment, then delete your old API Key and generate a new one [HERE](https://build.nvidia.com/explore/discover). 

In your terminal, paste the following code, change <NAMESPACE> into your namespace, and replace <YOUR API KEY> with your new key:

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

Now, change the api key value in your YAML file and try deploying the NIM in your terminal again. 



