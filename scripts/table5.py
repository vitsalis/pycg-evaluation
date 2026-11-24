import sys
import csv
import os
import operator

def read_csv(path):
    data = []
    time_sum = 0
    cnt = 0
    with open(path, "r") as f:
        reader = csv.reader(f, delimiter=",")
        for idx, (project, time) in enumerate(reader):
            if idx == 0:
                continue
            tm = time
            try:
                tm = float(time)
                time_sum += tm
                cnt += 1
            except:
                pass

            data.append([project, tm])
    data = sorted(data, key=operator.itemgetter(0))
    data.append(["Average", round(time_sum / cnt, 2)])
    return data

def main():
    if len(sys.argv) < 2:
        print ("Usage: table5.py results_dir")
        sys.exit(1)

    results_dir = sys.argv[1]
    pycg_path = os.path.join(results_dir, "pycg_macro_benchmark_time.csv")
    pyan_path = os.path.join(results_dir, "pyan_macro_benchmark_time.csv")
    depends_path = os.path.join(results_dir, "depends_macro_benchmark_time.csv")

    pycg_results = read_csv(pycg_path)
    pyan_results = read_csv(pyan_path)
    depends_results = read_csv(depends_path)

    header = ["Project", "PyCG", "Pyan", "Depends"]

    rows = [[pc[0], pc[1], py[1], dp[1]] for
            pc, py, dp in zip(pycg_results, pyan_results, depends_results)]

    header_format = "{:<20}" +  "{:>20}" + "{:>20}" + "{:>23}"
    row_format = "{:<20}" +  "{:>20}" * 3
    print (header_format.format(*header))
    print ("-"*150)
    for row in rows[:-1]:
        print (row_format.format(*row))
    print ("-"*150)
    print (row_format.format(*rows[-1]))


if __name__ == "__main__":
    main()
