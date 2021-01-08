FROM debian:stretch

# install python3
RUN apt -yqq update && apt install -yqq\
    python3\
    python3-pip\
    sudo\
    default-jdk\
    maven\
    jq\
    bc

# create the pycg user 
RUN useradd -ms /bin/bash pycg && \
    echo pycg:pycg | chpasswd && \
    cp /etc/sudoers /etc/sudoers.bak && \
    echo 'pycg ALL=(root) NOPASSWD:ALL' >> /etc/sudoers
USER pycg
WORKDIR /home/pycg/

# install pycg
COPY ./PyCG/ /pycg
RUN cd /pycg && sudo python3 setup.py install

# add working copy of pyan
COPY ./pyan/ /pyan
# add modifications that enable pyan to produce json output
COPY ./data/modifications/pyan/pyan.py /pyan/pyan.py
COPY ./data/modifications/pyan/pyan/main.py /pyan/pyan/main.py

# add working copy of depends
COPY ./depends/ /depends
# add modification that fix es a unicode error in depends
COPY ./data/modifications/src/main/java/depends/entity/repo/InMemoryEntityRepo.java /depends/src/main/java/depends/entity/repo/InMemoryEntityRepo.java
# compile the source code
RUN cd /depends && sudo ./lib_install.sh && sudo mvn clean package -DskipTests

COPY ./scripts/crypto/ /example
