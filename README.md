[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.4456584.svg)](https://doi.org/10.5281/zenodo.4456584)


# Artifact for "PyCG: Practicall Call Graph Generation in Python" (ICSE'21)

This is the artifact for the ICSE'21 paper titled "PyCG: Practical Call Graph
Generation in Python".

An archived version of the artifact is also available on Zenodo.
See https://doi.org/10.5281/zenodo.4456584


# Artifact Elements

This artifact contains the following:

1. The source code of our prototype implementation `PyCG` (provided as a Git
   submodule).
2. The source code of the tools we compare against`PyCG`, namely `Pyan` and
   `Depends` (provided as Git submodules).
    * We have made minimal modifications on these tools so that they can run
      inside the Docker environment and produce output in JSON format. The
      respective modifications can be found under the `data/modifications`
      directory. 
3. The data used to perform the comparison and the evaluation.
    * A micro-benchmark of 112 minimal Python modules stored in
      `data/micro-benchmark`.
    * A macro-benchmark of 5 Python packages (provided as Git submodules)
      stored in `data/macro-benchmark` along with their ground-truth call
      graphs.
    * Sample results of the evaluation stored in `data/results`.
4. The source code used to perform the evaluation under the `scripts`
   directory.

Note that our prototype implementation `PyCG` is available as open-source
software under the Apache 2.0 Licence, and can also be found in the
following repository: https://github.com/vitsalis/pycg

# Requirements

*Copied from REQUIREMENTS.md*

* A Unix-like operating system (tested on OSx).
* A Docker installation.
* A Git installation.
* At least 5GB of available disk space.

# Installation

*Copied from INSTALL.md*

To get the artifact run (estimated running time: ~1 minute)

```bash
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
time: ~10 minutes):

```bash
>>> cd ~/pycg-evaluation
>>> docker build -t pycg-eval -f Dockerfile .
```

After building the Docker image successfully, please navigate to the root
directory of the artifact:
```bash
cd ~/pycg-evaluation
```

### Pull Images from Dockerhub

You can also download the Docker images from Dockerhub by using the following
commands:

```bash
docker pull vitsalis/pycg-eval
# Rename the image to be consistent with the scripts
docker tag vitsalis/pycg-eval pycg-eval
```

After downloading the Docker image, please navigate to the root
directory of the artifact:
```bash
cd ~/pycg-evaluation
```

# Getting Started

## Navigating through the Docker Image

Before producing the results of the evaluation,
let's explore the contents of our freshly-created Docker image (i.e.,
`pycg-eval`).
Run the following command to create and start the new container.
```bash
docker run -ti --rm pycg-eval
```

After executing the command you can enter the home directory (i.e.,
`/home/pycg`) of the `pycg` user. `PyCG` is already installed in the Docker
environment. However, if you want to build `PyCG` on your own, you can run:

```bash
pycg@3ddc263ed13a:~$ cd /pycg
pycg@3ddc263ed13a:/pycg$ python3 setup.py clean
pycg@3ddc263ed13a:/pycg$ sudo python3 setup.py install
pycg@3ddc263ed13a:/pycg$ cd ~
```

### Usage

With `PyCG` installed you can use it through the `pycg` command:

```bash
pycg@3ddc263ed13a:~$ pycg --help
usage: pycg [-h] [--package PACKAGE] [--fasten] [--product PRODUCT]
            [--forge FORGE] [--version VERSION] [--timestamp TIMESTAMP]
            [-o OUTPUT]
            [entry_point [entry_point ...]]

positional arguments:
  entry_point           Entry points to be processed

optional arguments:
  -h, --help            show this help message and exit
  --package PACKAGE     Package containing the code to be analyzed
  --fasten              Produce call graph using the FASTEN format
  --product PRODUCT     Package name
  --forge FORGE         Source the product was downloaded from
  --version VERSION     Version of the product
  --timestamp TIMESTAMP
                        Timestamp of the package's version
  -o OUTPUT, --output OUTPUT
                        Output path
```

`PyCG` takes as input a list of Python files to analyze.
For Python files organized inside a package, the `--package` argument is
required which specifies the root directory of the package under analysis.
The optional `--fasten`, `--product`, `--forge`, `--version`, `--timestamp` arguments
are related to a more descriptive call graph format and are not used for the
purposes of the paper.

## Generating an example call graph

Let's see how we can generate call graphs for Python programs using `PyCG`.
We wil use our previously created Docker image so that we can generate the call
graphs in a fresh environment.

We will investigate the `crypto` module described in Section 2 of the ICSE paper.
The source code for the module can be retrieved from the file
`scripts/crypto/crypto.py` of the artifact and has also been included under the
`/example` directory of the Docker image.

If you are not already inside the Docker environment, you can enter by executing
the following:
```bash
docker run -ti --rm pycg-eval
```

Then, navigate to the `/example` directory and use `PyCG` to generate the
call graph.

```bash
pycg@3ddc263ed13a:~$ cd /example
pycg@3ddc263ed13a:/example$ pycg crypto.py | jq .
{
  "cryptops.decrypt": [],
  "cryptops": [],
  "crypto.Crypto.apply": [
    "cryptops.encrypt",
    "cryptops.decrypt"
  ],
  "crypto.Crypto.__init__": [],
  "cryptops.encrypt": [],
  "crypto": [
    "crypto.Crypto.__init__",
    "crypto.Crypto.apply"
  ]
}
```

`PyCG` outputs the call graph in JSON format. Note that we have used the `jq`
utility to display JSON output in a human readable format.
`PyCG` generates call graphs using the adjacency list graph format. In this
format the keys of the JSON output correspond to the nodes of the graph.
A list is assigned to every node. Each entry in the list corresponds to an edge stemming from
the key and leading to the list entry.

Note that the call graph generated by `PyCG`
corresponds to the call graph depicted in Figure 2a of the paper.

You can exit the container to proceed to the evaluation:

```bash
pycg@3ddc263ed13a:~$ exit
```

# Evaluation

## Micro-Benchmark

You can start by evaluating the micro-benchmark described in Section 5.1 of the
paper. An overview of its contents can be viewed on Table 1.

The source code of the micro-benchmark
(i.e., `data/micro-benchmark`/) is structured as follows. First,
the root directory contains 16 subdirectories, one for each category of
Table 1. Then, each subdirectory contains directories for each one of
the tests that it defines. Finally, each test directory contains the following
files:

* `main.py`: The entry-point Python module for the test case.
* `README.md`: A short description of the test case.
* `callgraph.json`: The ground-truth call graph in JSON format.
* Other `*.py` files that are imported from the `main.py` module.

In the following, we will produce the results of Table 3, by examining the
completeness and soundness of the call graphs generated by `PyCG`, `Pyan` and
`Depends`. To do so, create and enter the `pycg-eval` container:

```bash
docker run --rm -ti \
    -v $(pwd)/data/micro-benchmark:/home/pycg/micro-benchmark/ \
    -v $(pwd)/scripts:/home/pycg/scripts/ \
    -v $(pwd)/data/results:/home/pycg/results/ pycg-eval
```

The `-v` option instructs Docker to mount a local volume inside the Docker
container. This allows you to access the `data/micro-benchmark`, `scripts` and
`data/results` directories from inside the Docker environment.

For the purposes of this evaluation we have implemented a script
residing in `scripts/micro_benchmark.py`
that takes as input the path to the the micro-benchmark and
generates Python call graphs for `PyCG`, `Pyan` and `Depends`.
The script implements the following process for each tool:
1. Iterate each test case of the micro-benchmark and produce a corresponding
   call graph.
2. If needed, convert the resulting call graph to the format used by the
   micro-benchmark.
3. Compare the resulting call graph with the ground-truth one in terms of
   completeness and soundness.
4. Store the overall results in a CSV file.

Note that the results for each tool will be stored in
CSV format in the files `pycg_micro_benchmark.csv`, `pyan_micro_benchmark.csv`,
and `depends_micro_benchmark.csv` under the `data/results` directory for future
reference.
(estimated running time: 10 minutes)

```bash
pycg@b789f559b119:~$ python3 scripts/micro_benchmark.py \
        micro-benchmark results /pyan/pyan.py /depends
```

Now, let's collect the previously generated results for the micro-benchmark
evaluation and present them in a human readable format. The script `table3.py`
does the job:

```bash
pycg@b789f559b119:~$ python3 scripts/table3.py results
```

The expected output of the above command is stored in the
`data/results/table3.txt` file.

Finally, let's exit the container and proceed to the macro-benchmark:

```bash
pycg@b789f559b119:~$ exit
```

### Macro-Benchmark

Now, we will focus on the macro-benchmark described in Section 5.1 of the
paper. An overview of its contents can be viewed in Table 2.

The macro-benchmark directory contains two subdirectories:
* `projects`: Contains the source code for the macro-benchmark projects (Table
  2) as Git submodules.
* `ground-truth-cgs`: Contains the ground truth call graphs for each of the
  projects.

We will start by generating the call graphs for each project using `PyCG`,
`Pyan`, and `Depends` while keeping track of their execution times.
To do that, please create and enter the Docker container:

```bash
docker run --rm -ti \
    -v $(pwd)/data/macro-benchmark:/home/pycg/macro-benchmark/ \
    -v $(pwd)/scripts:/home/pycg/scripts/ \
    -v $(pwd)/data/results:/home/pycg/results/ pycg-eval
```

We have implemented corresponding scripts that generate call graphs using the
three tools. These scripts take as parameters the location of the
macro-benchmark, (if needed) a script that converts tool generated call graphs
into the format used by the macro-benchmark, and a directory to store execution
metrics results in CSV format.

First, let's you can generate the `PyCG` call graphs. Note that the generated call
graphs will be stored under `data/macro-benchmark/pycg-cgs` for further
inspection:

```bash
pycg@2c5b63e7615b:~$ ./scripts/generate_pycg_cgs.sh macro-benchmark results
```

You can do the same for `Pyan`, passing a call graph convertion script as an
argument. The generated call graphs will be stored under
`data/macro-benchmark/pyan-cgs` for further inspection:

```bash
pycg@9bf0a9e35e86:~$ ./scripts/generate_pyan_cgs.sh macro-benchmark \
        /pyan/pyan.py scripts/convert_pyan_cgs.py results
```

**Note**: Don't be alarmed if `Pyan` crashes for the `fabric` and `asciinema`
projects. This is expected, and we have included their execution to
demonstrate that Pyan cannot generate a call graph for them, as stated in
Section 5.3. For example, you should expect something along the lines of
the following error for `fabric`:

```bash
Generating call graph for: fabric
=================================
Traceback (most recent call last):
  File "/pyan/pyan.py", line 11, in <module>
    print(main())
  File "/pyan/pyan/main.py", line 110, in main
    v = CallGraphVisitor(filenames, logger)
  File "/pyan/pyan/analyzer.py", line 77, in __init__
    self.process()
  File "/pyan/pyan/analyzer.py", line 84, in process
    self.process_one(filename)
  File "/pyan/pyan/analyzer.py", line 98, in process_one
    self.visit(ast.parse(content, filename))
  File "/usr/lib/python3.5/ast.py", line 245, in visit
    return visitor(node)
  File "/pyan/pyan/analyzer.py", line 175, in visit_Module
    self.generic_visit(node)  # visit the **children** of node
  File "/usr/lib/python3.5/ast.py", line 253, in generic_visit
    self.visit(item)
  File "/usr/lib/python3.5/ast.py", line 245, in visit
    return visitor(node)
  File "/pyan/pyan/analyzer.py", line 375, in visit_ImportFrom
    if self.add_uses_edge(from_node, to_node):
  File "/pyan/pyan/analyzer.py", line 1294, in add_uses_edge
    self.remove_wild(from_node, to_node, to_node.name)
  File "/pyan/pyan/analyzer.py", line 1321, in remove_wild
    if to_node.get_name().find("^^^argument^^^") != -1:
AttributeError: 'NoneType' object has no attribute 'find'
```

Finally, you can generate call graphs using `Depends`. The generated call graphs
will be stored under `data/macro-benchmark/depends-cgs`.

```bash
pycg@9bf0a9e35e86:~$ ./scripts/generate_depends_cgs.sh macro-benchmark \
    /depends scripts/convert_depends_cgs.py results
```
#### Precision & Recall

Having generated the call graphs for the macro-benchmark projects, you can
compare them against the ground-truth call graphs and generate the precision
and recall metrics presented in Table 4 of the paper.

To do so, we have implemented a script that takes as input the directory
containing the tool generated call graphs for each tool and the directory containing the
ground truth call graphs. Then, it examines the call graphs generated for each project
producing precision and recall metrics which are then stored in a CSV file.
Specifically, the macro-benchmark results for each tool are stored under the `data/results`
directory with file names `pycg_macro_benchmark_eval.csv`,
`pyan_macro_benchmark_eval.csv`, and `depends_macro_benchmark_eval.csv`
corresponding to `PyCG`, `Pyan`, and `Depends` respectivelly:

```bash
pycg@9bf0a9e35e86:~$ python3 scripts/compare_macro_benchmark_cg.py \
        macro-benchmark/pycg-cgs macro-benchmark/pyan-cgs \
        macro-benchmark/depends-cgs macro-benchmark/ground-truth-cgs results
```

Let's combine the generated CSV files and present their results in a human
readable format. To do so, you can employ the `table4.py` script:

```bash
pycg@9bf0a9e35e86:~$ python3 scripts/table4.py results
```

The results should correspond to the ones depicted on Table 4.
Specifically, in terms of precision,
`PyCG` is a little more precise than `Depends`, while
`Pyan` lags behind. In terms of recall, `PyCG` achieves the highest
marks followed by `Pyan` and `Depends`.
A sample output is provided in the `data/results/table4.txt` file.

#### Performance

We will now combine the execution time metrics generated and present them in a
human readable format. To do so, invoke:

```bash
pycg@9bf0a9e35e86:~$ python3 scripts/table5.py results
```


The output of this command should follow the trends depicted in Table
5 of the paper.
Specifically, `Pyan` should have the fastest execution
times, followed by `PyCG`. `Depends` should have the slowest execution
times. A sample output is provided in `data/results/table5.txt`.
