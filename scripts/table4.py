import sys
import csv
import os
import operator

def read_csv(path):
    data = []
    complete_all = 0
    sound_all = 0
    num_all = 0
    with open(path, "r") as f:
        reader = csv.reader(f, delimiter=",")
        for idx, (proj, precision, recall) in enumerate(reader):
            if idx == 0:
                continue
            curr = [proj, precision, recall]
            if proj == "Average":
                avg = curr
            else:
                data.append(curr)

    res = sorted(data, key=operator.itemgetter(0))
    res.append(avg)
    return res

def main():
    if len(sys.argv) < 2:
        print ("Usage: table4.py results_dir")
        sys.exit(1)

    results_dir = sys.argv[1]
    pycg_path = os.path.join(results_dir, "pycg_macro_benchmark_eval.csv")
    pyan_path = os.path.join(results_dir, "pyan_macro_benchmark_eval.csv")
    depends_path = os.path.join(results_dir, "depends_macro_benchmark_eval.csv")

    pycg_results = read_csv(pycg_path)
    pyan_results = read_csv(pyan_path)
    depends_results = read_csv(depends_path)

    header = ["", "Precision", "Recall"]
    sub_header = ["Project"] + ["PyCG", "Pyan", "Depends"] * 2

    rows = [[pc[0], pc[1], py[1], dp[1], pc[2], py[2], dp[2]] for
            pc, py, dp in zip(pycg_results, pyan_results, depends_results)]

    header_format = "{:<20}" +  "{:>45}" + "{:>58}"
    row_format = "{:<20}" +  "{:>20} {:>20}" * 3
    print (header_format.format(*header))
    print ("-"*150)
    print (row_format.format(*sub_header))
    print ("-"*150)
    for row in rows[:-1]:
        print (row_format.format(*row))
    print ("-"*150)
    print (row_format.format(*rows[-1]))


if __name__ == "__main__":
    main()
