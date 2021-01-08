# Installation

To get the artifact run (estimated running time: 5 minutes)

```
git clone --recursive https://github.com/vitsalis/pycg-evaluation ~/pycg-evaluation
```

## Install Docker Images

We provide a `Dockerfile` to build images that contain:

* An installation of Python version 3.5.
* An installation of `PyCG`.
* An installation of `Pyan`.
* An installation of `Depends`.
* A user named `pycg` with sudo priviledges.

### Build Images from Source

**Note**:
If you do not want to build the images on your own, please skip this step and
proceed to the next section ("Pull Images from Dockerhub").

To build the image named `pycg-eval`, run the following command (estimated running
time: 10 minutes)

```
>>> cd ~/pycg-evaluation
>>> docker build -t pycg-eval -f Dockerfile .
```

After building the Docker image successfully, please navigate to the root
directory of the artifact
```
cd ~/pycg-evaluation
```

### Pull Images from Dockerhub

You can also download the Docker images from Dockerhub by using the following
commands

```
docker pull vitsalis/pycg-eval
# Rename the image to be consistent with the scripts
docker tag vitsalis/pycg-eval pycg-eval
```

After downloading the Docker image successfully, please navigate to the root
directory of the artifact
```
cd ~/pycg-evaluation
```
