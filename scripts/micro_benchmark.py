import sys
import os
import csv
import subprocess
import json

def get_subdirs(item):
    return os.listdir(item)

def equal_sound(out, expected):
    for item in expected:
        if not item in out:
            return False
        for edge in expected[item]:
            if not edge in out[item]:
                return False

    return True

def equal_complete(out, expected):
    for item in out:
        if not item in expected:
            continue
        for edge in out[item]:
            if not edge in expected[item]:
                return False

    return True

def get_python_files(test):
    python_files = []
    for path, names, fnames in os.walk(test):
        for fname in fnames:
            if fname.endswith(".py"):
                python_files.append(os.path.join(path, fname))
    return python_files

def write_csv(res_file, data):
    header = ["Category", "Num-Cases", "Complete", "Sound"]
    with open(res_file, "w+") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(header)
        for cat in sorted(data.keys()):
            writer.writerow([cat, data[cat]["all"], data[cat]["complete"], data[cat]["sound"]])

def iterate_cats(test_suite_dir, ex, results_file, do_test):
    overall_complete = 0
    overall_sound = 0
    all_tests = 0
    data = {}
    for cat in sorted(get_subdirs(test_suite_dir)):
        print ("Iterating category {}...".format(cat))
        complete_passed = 0
        sound_passed = 0
        tests = get_subdirs(os.path.join(test_suite_dir, cat))
        for test in tests:
            test_path = os.path.join(test_suite_dir, cat, test)
            if do_test(test_path, ex, equal_complete):
                complete_passed += 1

            if do_test(test_path, ex, equal_sound):
                sound_passed += 1

        data[cat] = {"complete": complete_passed, "sound": sound_passed, "all": len(tests)}
        overall_complete += complete_passed
        overall_sound += sound_passed
        all_tests += len(tests)
    write_csv(results_file, data)

def clean_cg_pyan(cg):
    def clean(node):
        node = node.replace(".init.", "__init__")
        node = node.replace(".call.", "__call__")
        node = node.replace(".repr.", "__repr__")
        node = node.replace(".eq.", "__eq__")
        node = node.replace(".str.", "__str__")
        node = node.replace("src.", "")
        return node

    new_cg = {}
    for node in cg:
        cleaned_node = clean(node)
        new_cg[cleaned_node] = []
        for item in cg[node]:
            new_cg[cleaned_node].append(clean(item))
    return new_cg

def do_test_pyan(test, pyan_path, equal_func):
    cmd = ["python3", pyan_path]
    python_files = []
    cg_path = os.path.join(test, "callgraph.json")

    for path, names, fnames in os.walk(test):
        for fname in fnames:
            if fname.endswith(".py"):
                python_files.append(os.path.join(path, fname))

    try:
        out = subprocess.check_output(cmd + python_files, stderr=subprocess.DEVNULL).splitlines()[0]
    except Exception as e:
        return False
    out_cg = json.loads(out.decode('utf-8'))
    out_cg = clean_cg_pyan(out_cg)

    with open(cg_path) as f:
        expected_cg = json.loads(f.read())

    return equal_func(out_cg, expected_cg)

def do_test_pycg(test, ex, equal_func):
    cmd = ["pycg", "--package", test]
    cg_path = os.path.join(test, "callgraph.json")

    python_files = get_python_files(test)

    try:
        out = subprocess.check_output(cmd + python_files).splitlines()[0]
    except Exception as e:
        return False
    out_cg = json.loads(out.decode('utf-8'))

    with open(cg_path) as f:
        expected_cg = json.loads(f.read())

    return equal_func(out_cg, expected_cg)

def convert_path_depends(path, package):
    mod_path, func = path.split("(")
    func = func[:-1]
    rel = os.path.relpath(mod_path, package)
    rel = rel.replace("/", ".").split(".py")[0]
    if rel.endswith(".__init__"):
        rel = rel.split(".__init__")[0]
    return rel + "." + func

def clean_cg_depends(cg, package):
    converted = {}
    ids_map = {}
    for id, variable in enumerate(cg['variables']):
        conv_path = convert_path_depends(variable, package)
        converted[conv_path] = []
        ids_map[id] = conv_path

    for cell in cg['cells']:
        if not cell["values"].get("Call", None):
            continue
        src = ids_map[cell["src"]]
        dst = ids_map[cell["dest"]]
        converted[src].append(dst)

    return converted

def do_test_depends(test, depends_path, equal_func):
    outfile = "out"
    outpath = "out.json"
    cg_path = os.path.join(test, "callgraph.json")
    cmd = ["java", "-jar",
            os.path.join(depends_path, "target", "depends-0.9.6-jar-with-dependencies.jar"),
            "-g=method", "python", test, outfile]

    try:
        subprocess.check_output(cmd, stderr=subprocess.DEVNULL)
    except:
        return False

    with open(outpath) as f:
        out_cg = json.load(f)

    with open(cg_path) as f:
        expected_cg = json.load(f)

    out_cg = clean_cg_depends(out_cg, os.path.abspath(test))

    return equal_func(out_cg, expected_cg)

def main():
    if len(sys.argv) < 5:
        print ("Usage micro_benchmark.py test_suite_dir results_dir pyan_path depends_path")
        sys.exit(1)

    test_suite_dir = sys.argv[1]
    results_dir = sys.argv[2]
    pyan_path = sys.argv[3]
    depends_path = sys.argv[4]

    pycg_results = os.path.join(results_dir, "pycg_micro_benchmark.csv")
    pyan_results = os.path.join(results_dir, "pyan_micro_benchmark.csv")
    depends_results = os.path.join(results_dir, "depends_micro_benchmark.csv")

    print ("-" * 40)
    print ("Iterating categories for PyCG")
    print ("-" * 40)
    iterate_cats(test_suite_dir, None, pycg_results, do_test_pycg)
    print ("\n")

    print ("-" * 40)
    print ("Iterating categories for Pyan")
    print ("-" * 40)
    iterate_cats(test_suite_dir, pyan_path, pyan_results, do_test_pyan)
    print ("\n")

    print ("-" * 40)
    print ("Iterating categories for Depends")
    print ("-" * 40)
    iterate_cats(test_suite_dir, depends_path, depends_results, do_test_depends)

if __name__ == "__main__":
    main()
