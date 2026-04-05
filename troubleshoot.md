# Troubleshooting Corrdiff NIM 

If the status of your NIM alternates between `ImagePullOff`  and `ErrImagePull`, please use the following command in zsh or bash to find the root of the issue:

```zsh
kubectl describe pod corrdiff-nim-<USERNAME>-<POD NUMBER> -n <NAMESPACE>
```
As a response, you should see the issue in a message starting as follows:
```zsh
Events:
  Type     Reason   Age                From     Message
  ----     ------   ----               ----     -------
```
Identify strings such as `Warning, Failed` or `Failed to pull image` and check the message given after; you should be able to see what exactly went wrong during the image pull. Please make sure the version of Corrdiff you are deploying is `corrdiff:1.1.0`.

If it is an authentication error, the best thing to do is to delete your previous API key and start a new one. 

To do so, first delete your deployment, then delete your old API Key and generate a new one [HERE](https://build.nvidia.com/explore/discover). 

In your terminal, paste the following code. Remember to change <NAMESPACE> into your namespace, and replace <YOUR API KEY> with your new key:

```zsh
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

Now, replace the api key value in your YAML file, and try restarting the NIM in your terminal again:

```zsh
kubectl rollout restart deployment corrdiff-nim-<USERNAME> -n <NAMESPACE>
```



