# Getting Started with NRP Nautilus

## Prerequisites

Before you begin, it is recommended to install a package manager for your operating system to simplify tool installation:

- **Linux / macOS** — [Homebrew](https://brew.sh/)
- **Windows** — [Chocolatey](https://chocolatey.org/)

**Install Homebrew (Linux/macOS):**
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

**Install Chocolatey (Windows — run in PowerShell as Administrator):**
```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; `
[System.Net.ServicePointManager]::SecurityProtocol = [System.Net.SecurityProtocolType]::Tls12; `
iex ((New-Object System.Net.WebClient).DownloadString('https://chocolatey.org/install.ps1'))
```

---

## Setup Steps

### 1. Log In to NRP Nautilus

Go to [https://nrp.ai/](https://nrp.ai/) and sign in with your credentials.

### 2. Verify Namespace Access

Navigate to **User Information** and confirm that `sdsu-shen-climate-lab` appears under **Namespaces and Groups**.

### 3. Install `kubectl` and `kubelogin`

**Linux / macOS:**
```bash
brew install kubectl
brew install int128/kubelogin/kubelogin
```

**Windows:**
```powershell
choco install kubernetes-cli
choco install kubelogin
```

> [!NOTE]
> For additional installation options, see the [official Kubernetes documentation](https://kubernetes.io/docs/tasks/tools/).

### 4. Verify `kubectl` Installation

```bash
kubectl version --client
```

Ensure the output shows `Client Version: v1.35.0` before proceeding.

### 5. Configure `kubectl`

Create the `.kube` directory:

```bash
mkdir -p ~/.kube
```

Download the cluster configuration file from [this link](https://github.com/xlaurahu/CorrDiffSCIL/blob/main/config) and move it into `~/.kube/`.

### 6. Set the Namespace Context

```bash
kubectl config set-context nautilus --namespace=sdsu-shen-climate-lab
```

### 7. Verify Cluster Access

```bash
kubectl get pods -n sdsu-shen-climate-lab
```

A successful response listing pods (or an empty list with no errors) confirms you are connected to the cluster.



















































