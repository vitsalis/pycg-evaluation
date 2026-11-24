#! /bin/bash

if [ -z $1 ]; then
    echo "You have to provide the path to the macro-benchmark directory"
    exit 1
fi

if [ -z $2 ]; then
    echo "You have to provide the path to the Depends directory"
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
depends_path=$(realpath $2)
convert_path=$(realpath $3)
results_file="$(realpath $4)/depends_macro_benchmark_time.csv"


# create a temporary directory to store the call graphs
mkdir -p ~/tmp
cp -r $mb_dir/projects/{Sublist3r,asciinema,autojump,fabric,face_classification} ~/tmp/
mkdir -p ~/tmp/cgs/

# create directory that will store the Depends call graphs
depends_cgs_dir="$mb_dir/depends-cgs"
mkdir -p $depends_cgs_dir

echo "Generating call graph for: autojump"
echo "==================================="
start_time
java -jar $depends_path/target/depends-0.9.6-jar-with-dependencies.jar -g=method python \
    ~/tmp/autojump/bin out &> /dev/null
end_time
mv out.json ~/tmp/cgs/autojump.json
autojump_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: fabric"
echo "================================="
# remove test files
rm -r ~/tmp/fabric/tests
start_time
java -jar $depends_path/target/depends-0.9.6-jar-with-dependencies.jar -g=method python \
    ~/tmp/fabric/ out &> /dev/null
end_time
mv out.json ~/tmp/cgs/fabric.json
fabric_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: asciinema"
echo "===================================="
start_time
java -jar $depends_path/target/depends-0.9.6-jar-with-dependencies.jar -g=method python \
    ~/tmp/asciinema/asciinema out &> /dev/null
end_time
mv out.json ~/tmp/cgs/asciinema.json
asciinema_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: face_classification"
echo "=============================================="
start_time
java -jar $depends_path/target/depends-0.9.6-jar-with-dependencies.jar -g=method python \
    ~/tmp/face_classification/src out &> /dev/null
end_time
mv out.json ~/tmp/cgs/face_classification.json
face_classification_time=$ELAPSED
echo -e "\n"

echo "Generating call graph for: Sublist3r"
echo "===================================="
start_time
java -jar $depends_path/target/depends-0.9.6-jar-with-dependencies.jar -g=method python \
    ~/tmp/Sublist3r out &> /dev/null
end_time
mv out.json ~/tmp/cgs/Sublist3r.json
sublist3r_time=$ELAPSED
echo -e "\n"


echo "Converting call graphs to reference format"
echo "------------------------------------------"
python3 $convert_path ~/tmp/autojump/bin ~/tmp/cgs/autojump.json $depends_cgs_dir/autojump.json
python3 $convert_path ~/tmp/fabric ~/tmp/cgs/fabric.json $depends_cgs_dir/fabric.json
python3 $convert_path ~/tmp/asciinema/ ~/tmp/cgs/asciinema.json $depends_cgs_dir/asciinema.json
python3 $convert_path ~/tmp/face_classification/src ~/tmp/cgs/face_classification.json $depends_cgs_dir/face_classification.json
python3 $convert_path ~/tmp/Sublist3r ~/tmp/cgs/Sublist3r.json $depends_cgs_dir/Sublist3r.json
echo -e "\n"

rm -r ~/tmp

touch $results_file
echo "Project,Time" > $results_file
echo "autojump,$autojump_time" >> $results_file
echo "fabric,$fabric_time" >> $results_file
echo "asciinema,$asciinema_time" >> $results_file
echo "face_classification,$face_classification_time" >> $results_file
echo "Sublist3r,$sublist3r_time" >> $results_file
