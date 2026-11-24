#! /bin/bash

if [ -z $1 ]; then
    echo "You have to provide the path to the macro-benchmark directory"
    exit 1
fi

if [ -z $2 ]; then
    echo "You have to provide the path to the Pyan executable"
    exit 1
fi

if [ -z $3 ]; then
    echo "You have to provide the path to the convert script"
    exit 1
fi

if [ -z $4 ]; then
    echo "You have to provide the path to the results directory"
    exit 1
fi

function start_time()
{
    STARTTIME=$(date +%s%N)
}

function end_time()
{
    ENDTIME=$(date +%s%N)
    ELAPSED=$(echo "scale=2; ($ENDTIME - $STARTTIME) / 10^9" | bc -l)
}

mb_dir=$(realpath $1)
pyan_path=$(realpath $2)
convert_path=$(realpath $3)
results_file="$(realpath $4)/pyan_macro_benchmark_time.csv"


# create a temporary directory to store the call graphs
mkdir -p ~/tmp
cp -r $mb_dir/projects/{Sublist3r,asciinema,autojump,fabric,face_classification} ~/tmp/
mkdir -p ~/tmp/cgs/

# create directory that will store the Pyan call graphs
pyan_cgs_dir="$mb_dir/pyan-cgs"
mkdir -p $pyan_cgs_dir

echo "Generating call graph for: autojump"
echo "==================================="
py_files=$(find ~/tmp/autojump/bin -type f -name "*.py")
start_time
python3 $pyan_path --no-defines $py_files > ~/tmp/cgs/autojump.json
end_time
autojump_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: fabric"
echo "================================="
py_files=$(find ~/tmp/fabric/ -type f -name "*.py" | grep -v tests)
start_time
python3 $pyan_path --no-defines $py_files > ~/tmp/cgs/fabric.json
end_time
fabric_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: asciinema"
echo "===================================="
py_files=$(find ~/tmp/asciinema/asciinema -type f -name "*.py")
start_time
python3 $pyan_path --no-defines $py_files > ~/tmp/cgs/asciinema.json
end_time
asciinema_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: face_classification"
echo "=============================================="
py_files=$(find ~/tmp/face_classification/src/ -type f -name "*.py")
start_time
python3 $pyan_path --no-defines $py_files > ~/tmp/cgs/face_classification.json
end_time
face_classification_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: Sublist3r"
echo "===================================="
py_files=$(find ~/tmp/Sublist3r -type f -name "*.py")
start_time
python3 $pyan_path --no-defines $py_files > ~/tmp/cgs/Sublist3r.json
end_time
sublist3r_time=$ELAPSED
echo -e "\n"

echo "Converting call graphs to reference format"
echo "------------------------------------------"
python3 $convert_path ~/tmp/cgs/autojump.json $pyan_cgs_dir/autojump.json
python3 $convert_path ~/tmp/cgs/face_classification.json $pyan_cgs_dir/face_classification.json
python3 $convert_path ~/tmp/cgs/Sublist3r.json $pyan_cgs_dir/Sublist3r.json
echo -e "\n"

rm -r ~/tmp

touch $results_file
echo "Project,Time" > $results_file
echo "autojump,$autojump_time" >> $results_file
echo "fabric,-" >> $results_file
echo "asciinema,-" >> $results_file
echo "face_classification,$face_classification_time" >> $results_file
echo "Sublist3r,$sublist3r_time" >> $results_file
