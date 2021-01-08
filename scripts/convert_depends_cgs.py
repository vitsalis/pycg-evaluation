import sys
import json
import os

def convert_path(path, package):
    mod_path, func = path.split("(")
    func = func[:-1]
    rel = os.path.relpath(mod_path, package)
    rel = rel.replace("/", ".").split(".py")[0]
    if rel.endswith(".__init__"):
        rel = rel.split(".__init__")[0]
    return rel + "." + func

def main():
    if len(sys.argv) < 4:
        print ("Usage convert.py package cg_path output_path")
        sys.exit(1)

    package = sys.argv[1]
    cg_path = sys.argv[2]
    output_path = sys.argv[3]
    with open(cg_path) as f:
        cg = json.load(f)

    converted = {}
    ids_map = {}
    for id, variable in enumerate(cg['variables']):
        conv_path = convert_path(variable, package)
        converted[conv_path] = []
        ids_map[id] = conv_path

    for cell in cg['cells']:
        if not cell["values"].get("Call", None):
            continue
        src = ids_map[cell["src"]]
        dst = ids_map[cell["dest"]]
        converted[src].append(dst)

    with open(output_path, "w+") as f:
        f.write(json.dumps(converted))

if __name__ == "__main__":
    main()
